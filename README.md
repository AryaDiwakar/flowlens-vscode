# FlowLens

FlowLens is a local-first VS Code extension for understanding and debugging data-engineering workspaces. It combines conservative static analysis with local CSV/Parquet profiling; it does not require an LLM or send source/data to an external API.

## Implemented now

- Workspace discovery for SQL, Python, CSV, Parquet, YAML, and JSON; explicit YAML pipeline task relationships; literal Python reader/writer paths and local imports with confidence labels.
- SQLGlot dialect-aware parse findings, table reads/writes, CTE relationships, `SELECT *`, unused CTEs, joins without conditions, unbounded mutations, grouping mismatches, and deep nesting signals.
- NetworkX lineage graph, cycle detection, downstream impact paths, and React Flow visualization in a VS Code-themed dashboard.
- DuckDB CSV/Parquet profiles with exact row counts, null/unique counts, numeric summaries and quantiles, categorical top values, temporal bounds, duplicate counts, and IQR outliers. First-N sampling is disclosed.
- YAML validation for `not_null`, `unique`, `accepted_values`, `range`, `regex`, `type_check`, `row_count`, `referential_integrity`, and read-only `custom_sql` assertions.
- Configured KPI queries against CSV/Parquet with persisted numeric baselines and anomaly evidence; queries are restricted to the selected dataset and CTEs.
- Locally persisted profile/validation/analysis/KPI history, schema snapshots/diffs, robust numeric-distribution and categorical-shift signals when baselines exist, and ranked evidence-based investigation hypotheses.
- VS Code diagnostics, code actions for diagnostic evidence, Activity Bar navigation, dataset validation/profile/schema commands, impact analysis, investigation, and history.

See [known limitations](docs/architecture.md#current-limitations). Static relationships are not runtime lineage; suspected contributors are not causal proof.

## Requirements and installation

- VS Code 1.85+
- Node.js 20+ and npm (for building from source)
- Python 3.10+ (the extension can create an isolated environment after user approval)

### Run from source

```sh
cd apps/extension && npm install
cd ../webview && npm install
cd ../extension && npm run vscode:prepublish
```

Open the repository in VS Code, press **F5**, and open a data workspace. Run **FlowLens: Analyze Workspace**. On first use, FlowLens offers to create a Python environment under VS Code extension storage and install pinned dependencies. This requires network access and user confirmation. Alternatively install the pinned service dependencies with `pip install -e 'services/backend[dev]'` and set `flowlens.pythonPath` to that interpreter.

### Package a VSIX

```sh
cd apps/extension
npm run package
code --install-extension flowlens-0.1.0.vsix
```

The VSIX includes the extension, built React webview, backend Python sources, and pinned backend requirements. Runtime dependency installation remains an explicit user-approved step.

## Try the sample project

Open `examples/sample_project` as a workspace. It includes a CSV, SQL/Python jobs, an explicit pipeline YAML, `.flowlens/quality.yml`, and `.flowlens/metrics.yml`. Run analysis, profile and validate `data/customers.csv`, and evaluate the configured KPIs. The sample intentionally includes static findings and a dynamic Python path that FlowLens reports as unresolved.

## Development and tests

```sh
cd services/backend && python -m pip install -e '.[dev]' && pytest
cd apps/webview && npm install && npm test && npm run build
cd apps/extension && npm install && npm test
npm run test:integration
```

The Extension Host suite uses an installed VS Code if found; otherwise `@vscode/test-electron` downloads one. Set `FLOWLENS_TEST_BACKEND=1` to include an end-to-end extension-to-local-API scan test (requires the configured Python interpreter to have backend dependencies).

FastAPI OpenAPI docs are available at `/docs` while the extension-started local service is running. See [API](docs/api.md), [quality configuration](docs/quality-rules.md), [settings and pipeline config](docs/configuration.md), and [architecture/limitations](docs/architecture.md). Run the seeded synthetic evaluator with `python benchmarks/run_benchmark.py --split evaluation`; its JSON report is generated from actual engine executions. See [benchmark assumptions](docs/benchmark.md).
