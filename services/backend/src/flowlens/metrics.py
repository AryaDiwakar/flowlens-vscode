from __future__ import annotations

import math
import statistics
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb
import sqlglot
import yaml
from sqlglot import exp

from flowlens.anomalies import detect_metric_anomaly
from flowlens.models import MetricValue, MetricsReport
from flowlens.persistence import previous_dataset_events, record_event


def _dataset_path(root: Path, dataset: str) -> Path:
    requested = (root / dataset).resolve()
    try:
        requested.relative_to(root)
    except ValueError as exc:
        raise ValueError("KPI dataset must be inside the workspace") from exc
    if not requested.is_file() or requested.suffix.lower() not in {".csv", ".parquet"}:
        raise ValueError("KPI evaluation requires an existing workspace CSV or Parquet dataset")
    return requested


def _reader(path: Path) -> str:
    escaped = str(path).replace("'", "''")
    return f"read_csv_auto('{escaped}')" if path.suffix.lower() == ".csv" else f"read_parquet('{escaped}')"


def _metric_query(query: str, index: int) -> None:
    try:
        statements = sqlglot.parse(query, read="duckdb")
    except sqlglot.errors.ParseError as exc:
        raise ValueError(f"Metric {index}: invalid SQL: {exc}") from exc
    if len(statements) != 1 or not isinstance(statements[0], exp.Select):
        raise ValueError(f"Metric {index}: SQL must be a single read-only SELECT")
    root = statements[0]
    if any(isinstance(node, (exp.Insert, exp.Update, exp.Delete, exp.Create, exp.Drop, exp.Alter, exp.Command))
           for node in root.walk()):
        raise ValueError(f"Metric {index}: SQL contains a mutation or command")
    ctes = {cte.alias_or_name.lower() for cte in root.find_all(exp.CTE)}
    tables = list(root.find_all(exp.Table))
    if not any(isinstance(table.this, exp.Identifier) and table.name.lower() == "dataset" for table in tables):
        raise ValueError(f"Metric {index}: SQL must read the selected dataset view")
    for table in tables:
        if (not isinstance(table.this, exp.Identifier) or table.name.lower() not in {"dataset", *ctes}
                or table.db or table.catalog):
            raise ValueError(f"Metric {index}: SQL may only read the selected dataset view and its CTEs")


def evaluate_metrics(root: str, dataset: str, config_file: str = ".flowlens/metrics.yml",
                     sensitivity: float = 3.5) -> MetricsReport:
    base = Path(root).expanduser().resolve()
    if not base.is_dir():
        raise ValueError("Workspace root must be an existing directory")
    if not 1.0 <= sensitivity <= 8.0:
        raise ValueError("Sensitivity must be between 1 and 8")
    source = _dataset_path(base, dataset)
    config_path = (base / config_file).resolve()
    try:
        config_path.relative_to(base)
    except ValueError as exc:
        raise ValueError("KPI configuration must be inside the workspace") from exc
    if not config_path.is_file():
        raise ValueError(f"KPI configuration not found: {config_file}")
    try:
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"Could not read KPI configuration: {exc}") from exc
    if not isinstance(config, dict) or not isinstance(config.get("metrics"), list) or not config["metrics"]:
        raise ValueError("KPI config must contain a non-empty metrics list")
    configured_dataset = config.get("dataset")
    relative_dataset = source.relative_to(base).as_posix()
    if configured_dataset and str(configured_dataset) not in {dataset, source.name, source.stem, relative_dataset}:
        raise ValueError(f"KPI config targets {configured_dataset!r}, not {dataset!r}")

    con = duckdb.connect(database=":memory:")
    observations: list[MetricValue] = []
    try:
        con.execute(f"CREATE VIEW dataset AS SELECT * FROM {_reader(source)}")
        previous = previous_dataset_events(str(base), "metric", relative_dataset, limit=500)
        names: set[str] = set()
        for index, specification in enumerate(config["metrics"], start=1):
            if not isinstance(specification, dict):
                raise ValueError(f"Metric {index} must be a mapping")
            name, query = specification.get("name"), specification.get("sql")
            if not isinstance(name, str) or not name.strip() or name in names:
                raise ValueError(f"Metric {index} requires a unique, non-empty name")
            names.add(name)
            if not isinstance(query, str) or not query.strip():
                raise ValueError(f"Metric {index} ({name}) requires a SQL SELECT")
            _metric_query(query, index)
            cursor = con.execute(query)
            if len(cursor.description or []) != 1:
                raise ValueError(f"Metric {index} ({name}) must return exactly one column")
            rows = cursor.fetchmany(2)
            if len(rows) != 1:
                raise ValueError(f"Metric {index} ({name}) must return exactly one row")
            raw_value: Any = rows[0][0]
            if not isinstance(raw_value, (int, float, Decimal)) or isinstance(raw_value, bool):
                raise ValueError(f"Metric {index} ({name}) must return a numeric value")
            value = float(raw_value)
            if not math.isfinite(value):
                raise ValueError(f"Metric {index} ({name}) returned a non-finite number")
            historical_values = [
                float(item["value"])
                for event in previous
                for item in event["payload"].get("values", [])
                if item.get("name") == name and isinstance(item.get("value"), (int, float))
            ]
            anomaly = detect_metric_anomaly(name, value, historical_values, sensitivity)
            baseline_median = statistics.median(historical_values) if historical_values else None
            observations.append(MetricValue(name=name, value=value, baseline_observations=len(historical_values),
                                            baseline_median=baseline_median, anomaly=anomaly))

        report = MetricsReport(root=str(base), dataset=relative_dataset,
                               timestamp=datetime.now(timezone.utc).isoformat(), values=observations)
        record_event(str(base), "metric", report.model_dump())
        anomalous = [item.model_dump() for item in observations if item.anomaly]
        if anomalous:
            record_event(str(base), "anomaly", {"dataset": relative_dataset, "metrics": anomalous})
        return report
    except duckdb.Error as exc:
        raise ValueError(f"KPI query failed: {exc}") from exc
    finally:
        con.close()
