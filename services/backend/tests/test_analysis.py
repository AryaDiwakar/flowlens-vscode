from pathlib import Path

from fastapi.testclient import TestClient

from flowlens import api as api_module
from flowlens.api import app
from flowlens.scanner import scan_workspace
from flowlens.sql_analysis import analyze_sql
from flowlens.profiling import profile_dataset

api_module._token = "test-session-token"
client = TestClient(app, headers={"x-flowlens-token": "test-session-token"})


def test_sql_diagnostics_and_lineage_extraction():
    result = analyze_sql("WITH unused AS (SELECT 1) SELECT * FROM source JOIN other", "x.sql")
    codes = {finding.code for finding in result.findings}
    assert {"sql.unused_cte", "sql.select_star", "sql.join_without_condition"} <= codes
    assert set(result.reads) == {"other", "source"}


def test_malformed_sql_returns_diagnostic():
    result = analyze_sql("SELECT FROM", "bad.sql")
    assert result.findings[0].code == "sql.parse"
    assert analyze_sql("", "empty.sql").findings == []


def test_cte_dependencies_grouping_and_cross_join():
    result = analyze_sql("WITH base AS (SELECT id FROM source), final AS (SELECT id FROM base) SELECT name, COUNT(*) FROM final CROSS JOIN other GROUP BY id", "model.sql")
    assert result.cte_dependencies == {"base": [], "final": ["base"]}
    codes = [finding.code for finding in result.findings]
    assert "sql.ungrouped_column" in codes
    assert "sql.join_without_condition" not in codes
    ordinal = analyze_sql("SELECT country, COUNT(*) FROM customers GROUP BY 1", "ordinal.sql")
    assert "sql.ungrouped_column" not in {finding.code for finding in ordinal.findings}
    expression = analyze_sql("SELECT ts, COUNT(*) FROM events GROUP BY DATE(ts)", "date-group.sql")
    assert "sql.ungrouped_column" in {finding.code for finding in expression.findings}
    unused_chain = analyze_sql("WITH a AS (SELECT id FROM raw), b AS (SELECT id FROM a) SELECT id FROM other", "unused-chain.sql")
    assert {finding.message for finding in unused_chain.findings if finding.code == "sql.unused_cte"} == {
        "CTE 'a' is not referenced by this statement.", "CTE 'b' is not referenced by this statement."
    }


def test_scanner_builds_edges_and_ignores_hidden_metadata(tmp_path: Path):
    (tmp_path / "raw.csv").write_text("id\n1\n")
    (tmp_path / "model.sql").write_text("CREATE TABLE curated AS SELECT id FROM raw")
    (tmp_path / ".flowlens").mkdir()
    (tmp_path / ".flowlens" / "ignored.sql").write_text("SELECT * FROM secret")
    result = scan_workspace(str(tmp_path))
    assert {asset.path for asset in result.assets} == {"raw.csv", "model.sql"}
    assert any(edge.source == "table:raw" and edge.target == "file:model.sql" for edge in result.edges)
    assert any(edge.source == "file:model.sql" and edge.target == "table:curated" for edge in result.edges)
    assert any(edge.source == "file:raw.csv" and edge.target == "table:raw" and edge.confidence == "name-match" for edge in result.edges)
    model = analyze_sql((tmp_path / "model.sql").read_text(), "model.sql")
    assert model.reads == ["raw"]
    assert model.writes == ["curated"]


def test_scanner_finds_literal_python_inputs_imports_and_explicit_pipeline_config(tmp_path: Path):
    (tmp_path / "raw.csv").write_text("id\n1\n")
    (tmp_path / "common.py").write_text("def normalize(value):\n    return value\n")
    (tmp_path / "job.py").write_text("from common import normalize\nimport pandas as pd\ndf = pd.read_csv('raw.csv')\ndf.to_parquet('out.parquet')\ndf.to_csv(output_path)\n")
    (tmp_path / "pipeline.yml").write_text("""name: nightly
tasks:
  - name: extract
    script: job.py
    outputs: [stage]
  - name: publish
    depends_on: [extract]
    inputs: [stage]
    outputs: [mart]
""")
    result = scan_workspace(str(tmp_path))
    assert any(edge.source == "file:common.py" and edge.target == "file:job.py" and edge.relationship == "imports" for edge in result.edges)
    assert any(edge.source == "file:raw.csv" and edge.target == "file:job.py" for edge in result.edges)
    assert any(edge.source == "file:job.py" and edge.target == "file:out.parquet" and edge.confidence == "static-literal" for edge in result.edges)
    assert any(edge.relationship == "executes_before" and edge.confidence == "explicit-config" for edge in result.edges)
    assert any(asset.path == "pipeline.yml" and asset.kind == "pipeline" for asset in result.assets)
    assert any("writer uses a non-literal path" in item for item in result.unresolved)


def test_incremental_scan_reuses_unchanged_files_and_reparses_only_changes(tmp_path: Path):
    first = tmp_path / "first.sql"
    second = tmp_path / "second.sql"
    first.write_text("SELECT id FROM raw")
    second.write_text("SELECT id FROM first")
    initial = scan_workspace(str(tmp_path))
    assert initial.scan_stats["parsed_files"] == 2
    assert set(initial.scan_stats["changed_files"]) == {"first.sql", "second.sql"}
    unchanged = scan_workspace(str(tmp_path))
    assert unchanged.scan_stats["parsed_files"] == 0
    assert unchanged.scan_stats["reused_files"] == 2
    first.write_text("SELECT name FROM raw")
    changed = scan_workspace(str(tmp_path))
    assert changed.scan_stats["parsed_files"] == 1
    assert changed.scan_stats["reused_files"] == 1
    assert changed.scan_stats["changed_files"] == ["first.sql"]
    second.unlink()
    removed = scan_workspace(str(tmp_path))
    assert removed.scan_stats["removed_files"] == 1
    assert {asset.path for asset in removed.assets} == {"first.sql"}


def test_health_endpoint():
    assert client.get("/health").json()["status"] == "ok"


def test_local_api_rejects_requests_without_the_extension_token(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("flowlens.api._token", "session-secret")
    unauthenticated = TestClient(app)
    assert client.get("/health").status_code == 200
    assert unauthenticated.post("/scan", json={"root": str(tmp_path)}).status_code == 401
    assert unauthenticated.post("/scan", json={"root": str(tmp_path)}, headers={"x-flowlens-token": "session-secret"}).status_code == 200


def test_csv_profile_reports_nulls_and_numeric_statistics(tmp_path: Path):
    csv = tmp_path / "numbers.csv"
    csv.write_text("id,value\n1,10\n2,\n3,30\n")
    profile = profile_dataset(str(csv))
    assert profile.rows == 3
    value = next(column for column in profile.profiles if column.name == "value")
    assert value.null_count == 1
    assert value.mean == 20
    assert value.quantiles["q25"] == 15
    assert profile.duplicate_rows == 0


def test_profile_discloses_first_n_sampling_and_supports_parquet(tmp_path: Path):
    import pyarrow as pa
    import pyarrow.parquet as pq

    parquet = tmp_path / "sample.parquet"
    pq.write_table(pa.table({"group": ["a", "a", "b"], "value": [1, 2, 900]}), parquet)
    result = client.post("/profile", json={"file": str(parquet), "root": str(tmp_path), "sample_rows": 2})
    assert result.status_code == 200, result.text
    profile = result.json()
    assert profile["rows"] == 3 and profile["sampled"] is True
    assert profile["statistics"] == "first-N rows"
    assert profile["duplicate_rows"] == 0
    assert profile["profiles"][0]["top_values"][0]["value"] == "a"


def test_profile_rejects_empty_and_unsupported_files(tmp_path: Path):
    empty = tmp_path / "empty.csv"
    empty.write_text("")
    unsupported = tmp_path / "data.txt"
    unsupported.write_text("not a dataset format")
    for file in (empty, unsupported):
        response = client.post("/profile", json={"file": str(file), "root": str(tmp_path)})
        assert response.status_code == 400


def test_reproducible_example_workspace_is_analyzable():
    example = Path(__file__).resolve().parents[3] / "examples" / "sample_project"
    result = scan_workspace(str(example))
    assert any(asset.kind == "pipeline" for asset in result.assets)
    assert not any(finding.code == "sql.parse" for finding in result.findings)
    assert any("non-literal path" in unresolved for unresolved in result.unresolved)


def test_malformed_python_yaml_and_json_report_locations(tmp_path: Path):
    (tmp_path / "broken.py").write_text("def incomplete(:\n    pass\n")
    (tmp_path / "broken.yml").write_text("value: [\n")
    (tmp_path / "broken.json").write_text('{"value": }')
    result = scan_workspace(str(tmp_path))
    by_code = {finding.code: finding for finding in result.findings}
    assert by_code["python.parse"].line == 1
    assert by_code["config.yaml_invalid"].line >= 1
    assert by_code["config.json_invalid"].line == 1


def test_unmapped_sql_table_is_reported_as_ambiguous_not_missing(tmp_path: Path):
    (tmp_path / "query.sql").write_text("SELECT id FROM warehouse.ghost")
    result = scan_workspace(str(tmp_path))
    assert any("warehouse.ghost" in item and "may be an external" in item for item in result.unresolved)
