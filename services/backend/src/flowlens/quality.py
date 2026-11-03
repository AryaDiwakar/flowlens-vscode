from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import duckdb
import sqlglot
import yaml
from sqlglot import exp

from flowlens.models import RuleResult, ValidationReport
from flowlens.persistence import previous_dataset_events, record_event


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _dataset_file(root: Path, dataset: str) -> Path:
    supplied = Path(dataset)
    candidates = [supplied] if supplied.is_absolute() else [root / supplied]
    if not any(candidate.is_file() for candidate in candidates):
        stem = Path(dataset).stem
        candidates = sorted(p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in {".csv", ".parquet"}
                            and p.stem == stem and ".flowlens" not in p.parts and ".git" not in p.parts)
    found = [p.resolve() for p in candidates if p.is_file()]
    found = [p for p in found if p.suffix.lower() in {".csv", ".parquet"}]
    if len(found) != 1:
        raise ValueError("Dataset must resolve to exactly one CSV or Parquet file; use an explicit relative path if ambiguous")
    try:
        found[0].relative_to(root)
    except ValueError as exc:
        raise ValueError("Dataset must be inside the workspace") from exc
    return found[0]


def _reader(path: Path) -> str:
    escaped = str(path).replace("'", "''")
    return f"read_csv_auto('{escaped}')" if path.suffix.lower() == ".csv" else f"read_parquet('{escaped}')"


def _count(con: duckdb.DuckDBPyConnection, where: str, params: list[Any] | None = None) -> int:
    return int(con.execute(f"SELECT COUNT(*) FROM dataset WHERE {where}", params or []).fetchone()[0])


def _samples(con: duckdb.DuckDBPyConnection, where: str, limit: int, params: list[Any] | None = None) -> list[dict[str, Any]]:
    if not limit:
        return []
    rows = con.execute(f"SELECT * FROM dataset WHERE {where} LIMIT {limit}", params or []).fetchdf()
    return rows.astype(object).where(rows.notna(), None).to_dict(orient="records")


def _result(index: int, rule: str, column: str | None, total: int, failed: int,
            message: str, samples: list[dict[str, Any]]) -> RuleResult:
    return RuleResult(index=index, rule=rule, column=column, passed=failed == 0,
                      checked_rows=total, failed_rows=failed, message=message, samples=samples)


def run_validation(root: str, dataset: str, config_file: str = ".flowlens/quality.yml",
                   failed_row_limit: int = 5) -> ValidationReport:
    base = Path(root).expanduser().resolve()
    if not base.is_dir():
        raise ValueError("Workspace root must be an existing directory")
    source = _dataset_file(base, dataset)
    rules_path = (base / config_file).resolve()
    try:
        rules_path.relative_to(base)
    except ValueError as exc:
        raise ValueError("Quality configuration must be inside the workspace") from exc
    if not rules_path.is_file():
        raise ValueError(f"Quality configuration not found: {config_file}")
    try:
        config = yaml.safe_load(rules_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"Could not read quality configuration: {exc}") from exc
    if not isinstance(config, dict) or not isinstance(config.get("checks"), list):
        raise ValueError("Quality config must be an object containing a checks list")
    configured_dataset = config.get("dataset")
    if configured_dataset and str(configured_dataset) not in {dataset, source.stem, source.name, source.relative_to(base).as_posix()}:
        raise ValueError(f"Quality config targets {configured_dataset!r}, not {dataset!r}")

    con = duckdb.connect(database=":memory:")
    results: list[RuleResult] = []
    try:
        con.execute(f"CREATE VIEW dataset AS SELECT * FROM {_reader(source)}")
        total = int(con.execute("SELECT COUNT(*) FROM dataset").fetchone()[0])
        columns = {row[0] for row in con.execute("DESCRIBE dataset").fetchall()}
        for index, raw in enumerate(config["checks"], start=1):
            if not isinstance(raw, dict) or not isinstance(raw.get("rule"), str):
                raise ValueError(f"Check {index} must be a mapping with a rule")
            rule = raw["rule"].lower()
            column = raw.get("column")
            if rule == "row_count":
                parts: list[str] = []
                params: list[Any] = []
                if "min" in raw:
                    parts.append("? > ?"); params.extend([total, int(raw["min"]) - 1])
                if "max" in raw:
                    parts.append("? < ?"); params.extend([total, int(raw["max"]) + 1])
                passed = all(con.execute("SELECT " + part, (params[2*i], params[2*i+1])).fetchone()[0] for i, part in enumerate(parts)) if parts else True
                result = _result(index, rule, None, total, 0 if passed else 1,
                                 f"Row count is {total}; configured bounds are min={raw.get('min')}, max={raw.get('max')}.", [])
                results.append(result)
            elif rule == "custom_sql":
                query = raw.get("sql")
                if not isinstance(query, str) or not query.strip():
                    raise ValueError(f"Check {index}: custom_sql requires a SELECT query in 'sql'")
                try:
                    parsed = sqlglot.parse(query, read="duckdb")
                except sqlglot.errors.ParseError as exc:
                    raise ValueError(f"Check {index}: invalid custom SQL: {exc}") from exc
                if len(parsed) != 1 or not isinstance(parsed[0], exp.Select):
                    raise ValueError(f"Check {index}: custom_sql must be one read-only SELECT statement")
                if any(isinstance(node, (exp.Insert, exp.Update, exp.Delete, exp.Create, exp.Drop, exp.Command))
                       for node in parsed[0].walk()):
                    raise ValueError(f"Check {index}: custom_sql contains a mutation or command")
                cte_names = {cte.alias_or_name.lower() for cte in parsed[0].find_all(exp.CTE)}
                for table in parsed[0].find_all(exp.Table):
                    if (not isinstance(table.this, exp.Identifier)
                            or table.name.lower() not in {"dataset", *cte_names}
                            or table.db or table.catalog):
                        raise ValueError(f"Check {index}: custom_sql may only read the selected dataset view and its CTEs")
                failed = int(con.execute(f"SELECT COUNT(*) FROM ({query}) AS flowlens_failures").fetchone()[0])
                rows = con.execute(f"SELECT * FROM ({query}) AS flowlens_failures LIMIT {int(failed_row_limit)}").fetchdf() if failed_row_limit else None
                sample = rows.astype(object).where(rows.notna(), None).to_dict(orient="records") if rows is not None else []
                result = _result(index, rule, None, total, failed, f"Custom assertion returned {failed} failing row(s).", sample)
                results.append(result)
            else:
                if not isinstance(column, str) or column not in columns:
                    raise ValueError(f"Check {index}: column {column!r} does not exist in {source.name}")
                col = _quote_identifier(column)
                if rule == "not_null":
                    where, params, message = f"{col} IS NULL", [], f"{column} contains null values."
                elif rule == "unique":
                    duplicate = con.execute(f"SELECT COUNT(*) FROM (SELECT {col} FROM dataset WHERE {col} IS NOT NULL GROUP BY {col} HAVING COUNT(*) > 1)").fetchone()[0]
                    failed = int(con.execute(f"SELECT COUNT(*) FROM dataset WHERE {col} IN (SELECT {col} FROM dataset GROUP BY {col} HAVING COUNT(*) > 1) AND {col} IS NOT NULL").fetchone()[0])
                    sample = _samples(con, f"{col} IN (SELECT {col} FROM dataset GROUP BY {col} HAVING COUNT(*) > 1) AND {col} IS NOT NULL", failed_row_limit)
                    results.append(_result(index, rule, column, total, failed, f"{failed} row(s) belong to duplicate groups ({duplicate} duplicated value(s)).", sample))
                    continue
                elif rule == "accepted_values":
                    values = raw.get("values")
                    if not isinstance(values, list) or not values:
                        raise ValueError(f"Check {index}: accepted_values requires a non-empty values list")
                    placeholders = ",".join("?" for _ in values)
                    where, params, message = f"{col} IS NOT NULL AND {col} NOT IN ({placeholders})", values, f"{column} contains values outside the accepted set."
                elif rule == "range":
                    clauses, params = [], []
                    if "min" in raw: clauses.append(f"{col} < ?"); params.append(raw["min"])
                    if "max" in raw: clauses.append(f"{col} > ?"); params.append(raw["max"])
                    if not clauses: raise ValueError(f"Check {index}: range requires min and/or max")
                    where, message = f"{col} IS NOT NULL AND ({' OR '.join(clauses)})", f"{column} has values outside the configured range."
                elif rule == "regex":
                    pattern = raw.get("pattern")
                    if not isinstance(pattern, str): raise ValueError(f"Check {index}: regex requires a pattern")
                    try: re.compile(pattern)
                    except re.error as exc: raise ValueError(f"Check {index}: invalid regex: {exc}") from exc
                    where, params, message = f"{col} IS NOT NULL AND NOT regexp_matches(CAST({col} AS VARCHAR), ?)", [pattern], f"{column} contains values that do not match the regex."
                elif rule == "type_check":
                    expected = str(raw.get("type", "")).upper()
                    target_types = {
                        "INTEGER": "BIGINT", "INT": "BIGINT", "BIGINT": "BIGINT", "SMALLINT": "SMALLINT",
                        "STRING": "VARCHAR", "TEXT": "VARCHAR", "VARCHAR": "VARCHAR",
                        "FLOAT": "DOUBLE", "DOUBLE": "DOUBLE", "REAL": "DOUBLE", "DECIMAL": "DECIMAL(38, 10)",
                        "BOOLEAN": "BOOLEAN", "BOOL": "BOOLEAN", "DATE": "DATE", "TIMESTAMP": "TIMESTAMP",
                    }
                    target_type = target_types.get(expected)
                    if not target_type:
                        raise ValueError(f"Check {index}: unsupported type_check type {expected!r}")
                    where = f"{col} IS NOT NULL AND TRY_CAST({col} AS {target_type}) IS NULL"
                    failed = _count(con, where)
                    results.append(_result(index, rule, column, total, failed,
                                           f"{failed} non-null row(s) cannot be cast to {expected}.",
                                           _samples(con, where, failed_row_limit)))
                    continue
                elif rule == "referential_integrity":
                    ref_dataset = raw.get("reference_dataset")
                    ref_column = raw.get("reference_column")
                    if not isinstance(ref_dataset, str) or not isinstance(ref_column, str):
                        raise ValueError(f"Check {index}: referential_integrity requires reference_dataset and reference_column")
                    ref_path = _dataset_file(base, ref_dataset)
                    ref_ident = _quote_identifier(ref_column)
                    ref_view = f"reference_{index}"
                    con.execute(f"CREATE VIEW {_quote_identifier(ref_view)} AS SELECT * FROM {_reader(ref_path)}")
                    where, params, message = f"{col} IS NOT NULL AND NOT EXISTS (SELECT 1 FROM {_quote_identifier(ref_view)} r WHERE r.{ref_ident} = dataset.{col})", [], f"{column} has values missing from {ref_dataset}.{ref_column}."
                else:
                    raise ValueError(f"Check {index}: unsupported validation rule {rule!r}")
                failed = _count(con, where, params)
                results.append(_result(index, rule, column, total, failed, message, _samples(con, where, failed_row_limit, params)))
        from datetime import datetime, timezone
        relative_dataset = source.relative_to(base).as_posix()
        previous_runs = previous_dataset_events(str(base), "validation", relative_dataset, limit=1)
        comparison: dict[str, Any] = {"has_baseline": False}
        if previous_runs:
            previous_event = previous_runs[0]
            previous = previous_event["payload"]
            old_results = {(item.get("rule"), item.get("column")): item for item in previous.get("results", [])}
            deltas = []
            for item in results:
                before = old_results.get((item.rule, item.column))
                if before:
                    deltas.append({"rule": item.rule, "column": item.column,
                                   "previous_failed_rows": before.get("failed_rows", 0),
                                   "current_failed_rows": item.failed_rows,
                                   "delta_failed_rows": item.failed_rows - int(before.get("failed_rows", 0))})
            comparison = {
                "has_baseline": True,
                "previous_at": previous_event.get("created_at"),
                "previous_failed_checks": int(previous.get("failed_checks", 0)),
                "delta_failed_checks": sum(not item.passed for item in results) - int(previous.get("failed_checks", 0)),
                "rule_deltas": deltas,
            }
        report = ValidationReport(root=str(base), dataset=relative_dataset,
                                  passed=all(item.passed for item in results), total_checks=len(results),
                                  failed_checks=sum(not item.passed for item in results), results=results,
                                  timestamp=datetime.now(timezone.utc).isoformat(), comparison=comparison)
        historical = report.model_dump()
        for item in historical["results"]:
            item.pop("samples", None)
        record_event(str(base), "validation", historical)
        return report
    except duckdb.Error as exc:
        raise ValueError(f"Validation query failed: {exc}") from exc
    finally:
        con.close()
