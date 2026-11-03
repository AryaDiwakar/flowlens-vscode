# FlowLens

FlowLens brings local data-pipeline analysis into Visual Studio Code. It scans SQL, Python, CSV, Parquet, YAML, and JSON assets; builds confidence-labeled lineage; and provides editor diagnostics, profiling, validation, KPI comparisons, and investigation tools.

## Quick start

1. Open a trusted data workspace in VS Code.
2. Run **FlowLens: Analyze Workspace** from the Command Palette.
3. Open **FlowLens: Show Dashboard** to explore lineage and findings.
4. Use **Profile Dataset**, **Validate Dataset**, **Evaluate Configured KPIs**, **Compare Schema Snapshot**, or **Inspect Pipeline Impact** as needed.

On first backend use, FlowLens offers to create an isolated Python environment in VS Code extension storage and install pinned dependencies. This requires network access and your approval. FlowLens does not upload workspace code or datasets.

## Configuration

- `flowlens.pythonPath`: Python 3.10+ interpreter used for backend setup.
- `flowlens.sqlDialect`: SQLGlot dialect, such as `postgres`, `bigquery`, or `snowflake`.
- `flowlens.qualityConfig`: workspace-relative YAML validation rules; defaults to `.flowlens/quality.yml`.
- `flowlens.metricConfig`: workspace-relative KPI queries; defaults to `.flowlens/metrics.yml`.
- `flowlens.profileSampleRows`: first-N sample limit for column statistics.
- `flowlens.anomalySensitivity`: sensitivity for robust statistical signals.
- `flowlens.autoRescan`: debounced incremental analysis after relevant files change.

Analysis history, validation summaries, KPI observations, and schema snapshots are stored locally in `.flowlens/history.sqlite3`. Explicitly requested profiles retain top categorical values and frequencies there; do not profile sensitive data unless that local retention is acceptable.

## Scope and limitations

FlowLens does not execute workspace SQL/Python, connect to production warehouses or orchestrators, or establish causal root causes. Python analysis is static and conservative. KPI queries are read-only and restricted to the selected CSV/Parquet dataset and its CTEs. Statistical results are signals, not confirmed failures.

Full documentation, configuration examples, and benchmark methodology: [GitHub repository](https://github.com/AryaDiwakar/flowlens-vscode).
