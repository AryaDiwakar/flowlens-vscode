# Configuration reference

Settings live under the `flowlens` namespace in VS Code settings.

| Setting | Default | Description |
|---|---:|---|
| `flowlens.pythonPath` | `python3` | Interpreter used for the optional isolated-environment setup. Set this to a Python 3.10+ executable if needed. Once the managed environment is installed, it is used until this setting changes. |
| `flowlens.sqlDialect` | `""` | SQLGlot dialect name, for example `postgres`, `bigquery`, `snowflake`, or `duckdb`. Empty uses SQLGlot's default dialect. |
| `flowlens.qualityConfig` | `.flowlens/quality.yml` | Workspace-relative YAML validation configuration. The target is resolved inside the workspace. |
| `flowlens.autoRescan` | `true` | Debounced scan when SQL, Python, CSV, Parquet, YAML, or JSON assets change. Disable for large or frequently generated workspaces. |
| `flowlens.anomalySensitivity` | `3.5` | Robust-score sensitivity from 1 to 8. Lower values increase alert sensitivity; categorical Jensen–Shannon thresholds are derived from the same setting. |
| `flowlens.profileSampleRows` | `100000` | First-N row limit for column-level profile statistics. Range: 1 to 2,000,000. Exact dataset row count is still reported separately. |
| `flowlens.metricConfig` | `.flowlens/metrics.yml` | Workspace-relative read-only KPI query definitions. |

The supported explicit pipeline YAML contract is either:

```yaml
name: nightly
tasks:
  - name: extract
    script: jobs/extract.py
    inputs: [warehouse.source]
    outputs: [staging.source]
  - name: publish
    depends_on: [extract]
    script: models/publish.sql
    inputs: [staging.source]
    outputs: [analytics.source]
```

or `pipelines: [{name: ..., tasks: [...] }]`. Script paths are relative to the YAML file and must resolve inside the workspace. These declarations describe configured dependencies; they do not prove that an orchestrator ran them.

## KPI queries

Create `.flowlens/metrics.yml` or change `flowlens.metricConfig`:

```yaml
dataset: data/revenue.csv
metrics:
  - name: total_revenue
    sql: SELECT SUM(revenue) FROM dataset
  - name: order_count
    sql: SELECT COUNT(*) FROM dataset
```

Run **FlowLens: Evaluate Configured KPIs**. Each query must be one read-only `SELECT`, reference only the selected `dataset` view and its CTEs, and return exactly one numeric value. Values and robust baselines are stored locally in `.flowlens/history.sqlite3`; five prior observations are needed for numeric anomaly signals. This is not an external pipeline execution monitor.
