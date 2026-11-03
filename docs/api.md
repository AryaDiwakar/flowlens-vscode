# Local API

The extension starts the API on an ephemeral loopback port. Mutating or data-reading requests require the per-process `X-FlowLens-Token`; `/health` is intentionally unauthenticated so the extension can detect startup. The service is not intended to be exposed on a network interface.

| Method | Route | Purpose |
|---|---|---|
| GET | `/health` | Local readiness probe |
| POST | `/scan` | Discover workspace assets, SQL findings, and inferred/configured lineage |
| POST | `/sql/analyze` | Analyze supplied SQL text using SQLGlot |
| POST | `/profile` | Profile CSV/Parquet; record profile metrics locally |
| POST | `/validate` | Run YAML-configured, read-only DuckDB checks and return failed-row samples |
| POST | `/metrics/evaluate` | Evaluate numeric KPI queries declared in local YAML and compare with saved observations |
| POST | `/schema/diff` | Compare current schema to the preceding saved observation |
| POST | `/impact` | Trace downstream nodes and detect graph cycles |
| POST | `/investigate` | Rank hypotheses from current static scan and local profile/validation history |
| POST | `/history` | Read the most recent local analysis events |

OpenAPI is served by FastAPI at `/docs` while the local backend is running. It is not exposed as a hosted API.
