from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from flowlens.anomalies import detect_profile_anomalies
from flowlens import api as api_module
from flowlens.api import app
from flowlens.graph_engine import impact_analysis
from flowlens.investigation import investigate
from flowlens.models import ColumnProfile, DatasetProfile
from flowlens.persistence import list_events, record_event
from flowlens.scanner import scan_workspace

api_module._token = "test-session-token"
client = TestClient(app, headers={"x-flowlens-token": "test-session-token"})


def test_validation_rules_and_history_are_persisted(tmp_path: Path):
    (tmp_path / "customers.csv").write_text("id,email,age\n1,a@example.com,20\n1,b@example.com,130\n3,, -1\n")
    metadata = tmp_path / ".flowlens"
    metadata.mkdir()
    (metadata / "quality.yml").write_text("""dataset: customers
checks:
  - column: email
    rule: not_null
  - column: id
    rule: unique
  - column: age
    rule: range
    min: 0
    max: 120
  - rule: row_count
    min: 3
""")
    response = client.post("/validate", json={"root": str(tmp_path), "dataset": "customers"})
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["failed_checks"] == 3
    assert report["results"][0]["failed_rows"] == 1
    stored = list_events(str(tmp_path), kind="validation")
    assert len(stored) == 1
    assert "samples" not in stored[0]["payload"]["results"][0]
    (tmp_path / "customers.csv").write_text("id,email,age\n1,a@example.com,20\n2,b@example.com,30\n3,c@example.com,40\n")
    next_run = client.post("/validate", json={"root": str(tmp_path), "dataset": "customers"}).json()
    assert next_run["comparison"]["has_baseline"] is True
    assert next_run["comparison"]["delta_failed_checks"] == -3
    assert len(list_events(str(tmp_path), kind="validation")) == 2


def test_custom_sql_validation_rejects_mutations(tmp_path: Path):
    (tmp_path / "customers.csv").write_text("id\n1\n2\n")
    (tmp_path / ".flowlens").mkdir()
    (tmp_path / ".flowlens" / "quality.yml").write_text("""dataset: customers
checks:
  - rule: custom_sql
    sql: SELECT * FROM dataset WHERE id > 1
""")
    result = client.post("/validate", json={"root": str(tmp_path), "dataset": "customers"})
    assert result.status_code == 200 and result.json()["results"][0]["failed_rows"] == 1
    (tmp_path / ".flowlens" / "quality.yml").write_text("""checks:
  - rule: custom_sql
    sql: DELETE FROM dataset
""")
    result = client.post("/validate", json={"root": str(tmp_path), "dataset": "customers"})
    assert result.status_code == 400
    (tmp_path / ".flowlens" / "quality.yml").write_text("""checks:
  - rule: custom_sql
    sql: SELECT * FROM read_csv('https://example.invalid/private.csv')
""")
    result = client.post("/validate", json={"root": str(tmp_path), "dataset": "customers"})
    assert result.status_code == 400


def test_validation_accepts_values_referential_integrity_regex_and_type(tmp_path: Path):
    (tmp_path / "customers.csv").write_text("id,email,country\n1,one@example.com,US\n2,bad-email,XX\n")
    (tmp_path / "countries.csv").write_text("code\nUS\nCA\n")
    (tmp_path / ".flowlens").mkdir()
    (tmp_path / ".flowlens" / "quality.yml").write_text("""checks:
  - column: country
    rule: accepted_values
    values: [US, CA]
  - column: country
    rule: referential_integrity
    reference_dataset: countries.csv
    reference_column: code
  - column: email
    rule: regex
    pattern: '^[^@]+@[^@]+$'
  - column: id
    rule: type_check
    type: integer
""")
    response = client.post("/validate", json={"root": str(tmp_path), "dataset": "customers.csv"})
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["total_checks"] == 4
    assert [result["failed_rows"] for result in report["results"]] == [1, 1, 1, 0]


def test_type_check_reports_only_rows_that_fail_safe_cast(tmp_path: Path):
    (tmp_path / "measurements.csv").write_text("value\n12\nnot-a-number\n14\n")
    (tmp_path / ".flowlens").mkdir()
    (tmp_path / ".flowlens" / "quality.yml").write_text("checks:\n  - column: value\n    rule: type_check\n    type: integer\n")
    response = client.post("/validate", json={"root": str(tmp_path), "dataset": "measurements.csv"})
    assert response.status_code == 200, response.text
    result = response.json()["results"][0]
    assert result["failed_rows"] == 1
    assert result["samples"] == [{"value": "not-a-number"}]


def test_configured_kpi_baseline_detects_deviation_and_persists_it(tmp_path: Path):
    source = tmp_path / "sales.csv"
    source.write_text("amount\n100\n")
    (tmp_path / ".flowlens").mkdir()
    (tmp_path / ".flowlens" / "metrics.yml").write_text("""dataset: sales.csv
metrics:
  - name: revenue
    sql: SELECT SUM(amount) FROM dataset
""")
    for _ in range(5):
        result = client.post("/metrics/evaluate", json={"root": str(tmp_path), "dataset": "sales.csv"})
        assert result.status_code == 200, result.text
        assert result.json()["values"][0]["anomaly"] is None
    source.write_text("amount\n1000\n")
    result = client.post("/metrics/evaluate", json={"root": str(tmp_path), "dataset": "sales.csv"})
    assert result.status_code == 200, result.text
    metric = result.json()["values"][0]
    assert metric["baseline_observations"] == 5
    assert metric["baseline_median"] == 100
    assert metric["anomaly"]["metric"] == "kpi:revenue"
    history = list_events(str(tmp_path), kind="metric")
    assert history[0]["payload"]["values"][0]["value"] == 1000
    scan = scan_workspace(str(tmp_path))
    investigation = investigate(str(tmp_path), "sales.csv", scan, list_events(str(tmp_path)))
    assert any("Configured KPI 'revenue'" in item.description for item in investigation.hypotheses)


def test_kpi_sql_cannot_read_external_files_or_return_multiple_rows(tmp_path: Path):
    (tmp_path / "sales.csv").write_text("amount\n100\n")
    (tmp_path / ".flowlens").mkdir()
    config = tmp_path / ".flowlens" / "metrics.yml"
    config.write_text("metrics:\n  - name: external\n    sql: SELECT * FROM read_csv('https://example.invalid/data.csv')\n")
    response = client.post("/metrics/evaluate", json={"root": str(tmp_path), "dataset": "sales.csv"})
    assert response.status_code == 400
    config.write_text("metrics:\n  - name: static\n    sql: SELECT 100\n")
    response = client.post("/metrics/evaluate", json={"root": str(tmp_path), "dataset": "sales.csv"})
    assert response.status_code == 400
    (tmp_path / "sales.csv").write_text("amount\n100\n200\n")
    config.write_text("metrics:\n  - name: many_rows\n    sql: SELECT amount FROM dataset\n")
    response = client.post("/metrics/evaluate", json={"root": str(tmp_path), "dataset": "sales.csv"})
    assert response.status_code == 400


def test_schema_snapshots_detect_added_columns(tmp_path: Path):
    file = tmp_path / "events.csv"
    file.write_text("id\n1\n2\n")
    first = client.post("/schema/diff", json={"root": str(tmp_path), "file": "events.csv"})
    assert first.status_code == 200 and first.json()["has_baseline"] is False
    file.write_text("id,source\n1,web\n2,api\n")
    second = client.post("/schema/diff", json={"root": str(tmp_path), "file": "events.csv"})
    assert second.status_code == 200
    assert {change["kind"] for change in second.json()["changes"]} == {"column_added"}


def test_robust_anomaly_flags_deviation_only_after_baseline():
    old = [{"payload": {"rows": n, "profiles": []}} for n in [100, 101, 99, 100, 100]]
    current = {"rows": 900, "profiles": []}
    assert detect_profile_anomalies(current, old)
    assert detect_profile_anomalies({"rows": 900, "profiles": []}, old[:4]) == []


def test_categorical_distribution_anomaly_compares_local_top_value_aggregates():
    previous = [{"payload": {"rows": 100, "profiles": [{"name": "country", "null_percent": 0,
                 "top_values": [{"value": "US", "count": 90}, {"value": "CA", "count": 10}] }]}}]
    current = {"rows": 100, "profiles": [{"name": "country", "null_percent": 0,
               "top_values": [{"value": "US", "count": 10}, {"value": "CA", "count": 90}]}]}
    anomalies = detect_profile_anomalies(current, previous)
    signal = next(item for item in anomalies if item["metric"] == "category_distribution:country")
    assert signal["observed"] > signal["baseline_divergence_threshold"]
    assert "not a confirmed failure" in signal["classification"]


def test_numeric_distribution_anomaly_checks_spread_quantiles_and_outlier_rate():
    prior = [{"payload": {"rows": 100, "profiles": [{
        "name": "amount", "null_percent": 0, "mean": 10, "median": 10,
        "standard_deviation": 1, "outlier_percent": 0,
        "quantiles": {"q05": 8, "q25": 9, "q75": 11, "q95": 12},
    }]}} for _ in range(5)]
    current = {"rows": 100, "profiles": [{
        "name": "amount", "null_percent": 0, "mean": 10, "median": 10,
        "standard_deviation": 50, "outlier_percent": 25,
        "quantiles": {"q05": 1, "q25": 5, "q75": 15, "q95": 100},
    }]}
    signals = {item["metric"] for item in detect_profile_anomalies(current, prior)}
    assert {"standard_deviation:amount", "outlier_percent:amount", "q05:amount", "q25:amount", "q75:amount", "q95:amount"} <= signals


def test_impact_analysis_returns_downstream_paths_and_cycles(tmp_path: Path):
    (tmp_path / "first.sql").write_text("CREATE TABLE a AS SELECT id FROM raw")
    (tmp_path / "second.sql").write_text("CREATE TABLE b AS SELECT id FROM a")
    (tmp_path / "cycle.sql").write_text("CREATE TABLE raw AS SELECT id FROM b")
    scan = scan_workspace(str(tmp_path))
    impact = impact_analysis(scan, "first.sql")
    assert {node.label for node in impact.impacted_nodes} >= {"a", "b", "second.sql"}
    assert any(path[0] == "file:first.sql" for path in impact.paths)
    assert impact.circular_dependencies


def test_investigation_ranks_persisted_validation_evidence(tmp_path: Path):
    (tmp_path / "customers.csv").write_text("id\n1\n1\n")
    (tmp_path / ".flowlens").mkdir()
    (tmp_path / ".flowlens" / "quality.yml").write_text("checks:\n  - column: id\n    rule: unique\n")
    client.post("/validate", json={"root": str(tmp_path), "dataset": "customers.csv"})
    scan = scan_workspace(str(tmp_path))
    result = investigate(str(tmp_path), "customers.csv", scan, list_events(str(tmp_path)))
    assert result.status == "hypotheses"
    assert result.hypotheses[0].confidence.startswith("high")
    assert "causal" in result.hypotheses[0].confidence


def test_investigation_uses_incremental_change_as_low_confidence_evidence(tmp_path: Path):
    (tmp_path / "model.sql").write_text("CREATE TABLE revenue AS SELECT * FROM transactions JOIN prices")
    scan = scan_workspace(str(tmp_path))
    history = [
        {"id": 1, "kind": "analysis", "created_at": "run-1", "payload": {"changed_files": ["model.sql"]}},
        {"id": 2, "kind": "analysis", "created_at": "run-2", "payload": {"changed_files": ["model.sql"]}},
    ]
    result = investigate(str(tmp_path), "revenue", scan, history)
    changed = next(item for item in result.hypotheses if "changed before" in item.description)
    assert changed.files == ["model.sql"]
    assert changed.confidence.startswith("low")


def test_history_refuses_to_overwrite_non_sqlite_file(tmp_path: Path):
    metadata = tmp_path / ".flowlens"
    metadata.mkdir()
    history = metadata / "history.sqlite3"
    history.write_text("user content, not a database")
    with pytest.raises(ValueError, match="Refusing to overwrite"):
        record_event(str(tmp_path), "analysis", {"asset_count": 1})
    assert history.read_text() == "user content, not a database"
