# Architecture and limitations

## Components

- `apps/extension`: VS Code commands, diagnostics, Activity Bar tree, workspace-trust gate, webview bridge, and managed backend lifecycle.
- `apps/webview`: React/TypeScript dashboard and React Flow graph; Vite emits static assets into the extension package.
- `services/backend`: typed FastAPI contracts and independent SQL, scanner, DuckDB profile/quality, NetworkX graph, anomaly, investigation, and SQLite history modules.
- `examples/sample_project`: small inspectable project and explicit quality/pipeline configuration.

The extension starts the service on loopback using an ephemeral port and random session token. It never runs workspace SQL/Python. The setup action installs pinned backend packages into VS Code extension storage only after user confirmation. The webview uses VS Code theme variables and communicates through the extension message bridge; it cannot access the backend directly.

## Lineage confidence

- SQL table references/writes and CTE dependencies come from parsed ASTs.
- A SQL table name that uniquely matches a CSV/Parquet filename stem is connected to that file with `name-match` confidence, not asserted as a catalog-confirmed identity. Ambiguous filename matches remain unresolved.
- Python file reads/writes are linked only for literal path arguments to common dataframe readers/writers. Local Python import statements are reported as code dependencies, not data lineage.
- Pipeline task ordering, script targets, inputs, and outputs are edges only when explicitly declared in the supported YAML shape: top-level `tasks`, or `pipelines: [{name, tasks}]`.
- Every edge includes a relationship and confidence. A static graph is not proof that a transformation ran.

The scanner caches per-file contributions in the backend process. Text assets use content hashes; CSV/Parquet assets use size and nanosecond modification time. Added/deleted files invalidate Python/YAML reference parsing; changed files are reparsed and unchanged SQL analyses are reused. The extension watches relevant workspace files and debounces automatic rescans. Restarting the service clears the in-memory cache.

## Local persistence and privacy

Explicit scan, profile, validation, schema, and impact commands may create `.flowlens/history.sqlite3` in the open workspace. The database records timestamps, changed asset paths, aggregate results, schema information, metric summaries, and the top 10 categorical values/frequencies from explicitly requested profiles (these values can themselves be sensitive). It does not persist validation row samples or source code. Source datasets and SQL are not copied to an external service. Add `.flowlens/` to version control only if sharing project snapshots/history is intentional. Existing non-SQLite files at the history path are not overwritten.

The optional backend setup creates an isolated environment in VS Code extension global storage and installs the pinned `requirements.txt` only after a user-initiated confirmation. No AI integration is implemented.

## Current limitations

- Directory discovery still walks the workspace on each scan. CSV/Parquet change detection uses file metadata (not a content hash), so unusual changes that preserve size and nanosecond mtime could evade refresh until their metadata changes.
- Static Python analysis is intentionally narrow. It does not resolve arbitrary expressions, runtime imports, environment variables, dynamic SQL, or execution order outside declared pipeline YAML.
- The supported pipeline YAML convention is intentionally small and does not claim compatibility with Airflow, Dagster, Prefect, dbt manifests, or vendor orchestration formats.
- SQL diagnostics are static heuristics. Aggregation checks are conservative; query plans, catalog/schema resolution, and dialect-specific runtime semantics are not available.
- Profiling uses DuckDB. Exact row count and sampled column statistics are separately disclosed; first-N sampling is not random. Profiled top categories/outlier statistics may be based on this first-N sample.
- Validation uses CSV/Parquet files, not live databases. Custom SQL must be a single parsed SELECT and is evaluated against a `dataset` view. It is not a general SQL sandbox.
- Historical profile/KPI anomaly baselines require at least five prior observations for robust numeric signals. Current methods cover row counts, null/duplicate percentages, numeric means/medians/spread/quantiles/outlier rates, configured numeric KPIs, and categorical top-10 Jensen–Shannon comparisons. These signals are not production-calibrated and do not implement Isolation Forest, full-distribution tests, or live execution-duration monitoring.
- Schema nullability means observed null behavior, not declared database constraints. Categorical distribution drift is not yet compared across snapshots.
- Root-cause output ranks persisted validation/profile evidence, recorded incremental file changes, unresolved upstream mappings, and upstream flagged SQL patterns. No runtime execution logs or external production integrations are connected, and ranked hypotheses do not establish causality.
- KPI SQL runs only on a selected local CSV/Parquet dataset. There is no live database connector, external orchestration/production monitor, execution-duration ingestion, or AI assistant.
- Extension Host tests verify activation, command registration, dashboard creation, and (opt-in) local API startup/workspace scan. They do not automate every editor action or OS-specific Python environment setup path.
