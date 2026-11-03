import hmac
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from flowlens.anomalies import detect_profile_anomalies
from flowlens.graph_engine import impact_analysis
from flowlens.investigation import investigate
from flowlens.metrics import evaluate_metrics
from flowlens.models import HistoryRequest, ImpactRequest, InvestigationRequest, MetricsRequest, ProfileRequest, ScanRequest, SchemaRequest, SQLRequest, ValidationRequest
from flowlens.persistence import list_events, previous_dataset_events, record_event
from flowlens.profiling import profile_dataset
from flowlens.quality import run_validation
from flowlens.schema import compare_schema
from flowlens.scanner import scan_workspace
from flowlens.sql_analysis import analyze_sql

app = FastAPI(title="FlowLens Local Analysis API", version="0.1.0")
_token = os.environ.get("FLOWLENS_TOKEN", "")


@app.middleware("http")
async def require_local_token(request: Request, call_next):
    public_docs = {"/health", "/docs", "/openapi.json", "/redoc"}
    if request.url.path not in public_docs:
        supplied = request.headers.get("x-flowlens-token", "")
        if not _token:
            return JSONResponse(status_code=503, content={"detail": "FlowLens API has no session token and is not accepting requests"})
        if not hmac.compare_digest(supplied, _token):
            return JSONResponse(status_code=401, content={"detail": "Unauthorized FlowLens client"})
    return await call_next(request)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "flowlens"}


@app.post("/scan")
def scan(request: ScanRequest):
    try:
        result = scan_workspace(request.root, request.dialect)
        record_event(result.root, "analysis", {"asset_count": len(result.assets), "finding_count": len(result.findings), "edge_count": len(result.edges), **result.scan_stats})
        return result
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/sql/analyze")
def sql(request: SQLRequest):
    return analyze_sql(request.sql, request.file, request.dialect)


@app.post("/profile")
def profile(request: ProfileRequest):
    try:
        return _profile_and_record(request.file, request.sample_rows, request.root, request.sensitivity)
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _profile_and_record(file: str, sample_rows: int, root: str | None, sensitivity: float = 3.5):
    target = Path(file).expanduser().resolve()
    base = Path(root).expanduser().resolve() if root else target.parent
    try:
        target.relative_to(base)
    except ValueError as exc:
        raise ValueError("Profile input must be inside the selected workspace") from exc
    result = profile_dataset(str(target), sample_rows)
    dataset = target.relative_to(base).as_posix()
    previous = previous_dataset_events(str(base), "profile", dataset)
    payload = result.model_dump()
    payload["dataset"] = dataset
    result.anomalies = detect_profile_anomalies(payload, previous, sensitivity)
    payload["anomalies"] = result.anomalies
    record_event(str(base), "profile", payload)
    if result.anomalies:
        record_event(str(base), "anomaly", {"dataset": dataset, "anomalies": result.anomalies})
    return result


@app.post("/validate")
def validate(request: ValidationRequest):
    try:
        return run_validation(request.root, request.dataset, request.config_file, request.failed_row_limit)
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/metrics/evaluate")
def metrics(request: MetricsRequest):
    try:
        return evaluate_metrics(request.root, request.dataset, request.config_file, request.sensitivity)
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/history")
def history(request: HistoryRequest):
    try:
        return {"root": str(Path(request.root).expanduser().resolve()), "events": list_events(request.root, request.limit)}
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/schema/diff")
def schema_diff(request: SchemaRequest):
    try:
        base = Path(request.root).expanduser().resolve()
        target = (base / request.file).resolve()
        try:
            dataset = target.relative_to(base).as_posix()
        except ValueError as exc:
            raise ValueError("Schema snapshot input must be inside the workspace") from exc
        profile_result = _profile_and_record(str(target), request.sample_rows, str(base), request.sensitivity)
        previous = previous_dataset_events(str(base), "schema_snapshot", dataset)
        diff = compare_schema(dataset, profile_result, previous)
        snapshot = {
            "dataset": dataset,
            "rows": profile_result.rows,
            "profiles": [{"name": column.name, "data_type": column.data_type, "null_count": column.null_count}
                         for column in profile_result.profiles],
        }
        record_event(str(base), "schema_snapshot", snapshot)
        return diff
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/impact")
def impact(request: ImpactRequest):
    try:
        base = Path(request.root).expanduser().resolve()
        target = (base / request.file).resolve()
        relative = target.relative_to(base).as_posix()
        scan_result = scan_workspace(str(base), request.dialect)
        result = impact_analysis(scan_result, relative)
        record_event(str(base), "impact", {"file": relative, "impacted_count": len(result.impacted_nodes)})
        return result
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/investigate")
def root_cause(request: InvestigationRequest):
    try:
        scan_result = scan_workspace(request.root, request.dialect)
        events = list_events(request.root, limit=500)
        return investigate(request.root, request.dataset, scan_result, events)
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
