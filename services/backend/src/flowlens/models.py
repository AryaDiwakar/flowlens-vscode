from typing import Any

from pydantic import BaseModel, Field


class Finding(BaseModel):
    code: str
    severity: str
    message: str
    file: str
    line: int = 1
    evidence: str = ""
    suggestion: str | None = None


class Asset(BaseModel):
    path: str
    kind: str
    size_bytes: int


class GraphNode(BaseModel):
    id: str
    label: str
    kind: str
    file: str | None = None


class GraphEdge(BaseModel):
    source: str
    target: str
    relationship: str
    confidence: str = "inferred"


class ScanResult(BaseModel):
    root: str
    assets: list[Asset]
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    findings: list[Finding]
    unresolved: list[str] = Field(default_factory=list)
    scan_stats: dict[str, Any] = Field(default_factory=dict)


class ScanRequest(BaseModel):
    root: str
    dialect: str = ""


class SQLRequest(BaseModel):
    file: str
    sql: str
    dialect: str = ""


class SQLResult(BaseModel):
    findings: list[Finding]
    reads: list[str]
    writes: list[str]
    ctes: list[str]
    cte_dependencies: dict[str, list[str]] = Field(default_factory=dict)


class ProfileRequest(BaseModel):
    file: str
    sample_rows: int = Field(default=100_000, ge=1, le=2_000_000)
    root: str | None = None
    sensitivity: float = Field(default=3.5, ge=1.0, le=8.0)


class ColumnProfile(BaseModel):
    name: str
    data_type: str
    null_count: int | None = None
    null_percent: float | None = None
    distinct_count: int | None = None
    minimum: str | float | int | None = None
    maximum: str | float | int | None = None
    mean: float | None = None
    median: float | None = None
    standard_deviation: float | None = None
    quantiles: dict[str, float | None] = Field(default_factory=dict)
    outlier_count: int | None = None
    outlier_percent: float | None = None
    top_values: list[dict[str, Any]] = Field(default_factory=list)


class DatasetProfile(BaseModel):
    file: str
    rows: int
    columns: int
    sampled: bool
    sample_limit: int
    statistics: str
    duplicate_rows: int
    duplicate_percent: float
    profiles: list[ColumnProfile]
    anomalies: list[dict] = Field(default_factory=list)


class SchemaChange(BaseModel):
    kind: str
    column: str
    before: str | None = None
    after: str | None = None


class SchemaDiff(BaseModel):
    dataset: str
    has_baseline: bool
    changes: list[SchemaChange]
    previous_at: str | None = None
    current_at: str


class ValidationRequest(BaseModel):
    root: str
    dataset: str
    config_file: str = ".flowlens/quality.yml"
    failed_row_limit: int = Field(default=5, ge=0, le=100)


class RuleResult(BaseModel):
    index: int
    rule: str
    column: str | None = None
    passed: bool
    checked_rows: int
    failed_rows: int
    message: str
    samples: list[dict] = Field(default_factory=list)


class ValidationReport(BaseModel):
    root: str
    dataset: str
    passed: bool
    total_checks: int
    failed_checks: int
    results: list[RuleResult]
    timestamp: str
    comparison: dict = Field(default_factory=dict)


class HistoryRequest(BaseModel):
    root: str
    limit: int = Field(default=50, ge=1, le=500)


class SchemaRequest(BaseModel):
    root: str
    file: str
    sample_rows: int = Field(default=100_000, ge=1, le=2_000_000)
    sensitivity: float = Field(default=3.5, ge=1.0, le=8.0)


class ImpactRequest(BaseModel):
    root: str
    file: str
    dialect: str = ""


class InvestigationRequest(BaseModel):
    root: str
    dataset: str
    dialect: str = ""


class MetricsRequest(BaseModel):
    root: str
    dataset: str
    config_file: str = ".flowlens/metrics.yml"
    sensitivity: float = Field(default=3.5, ge=1.0, le=8.0)


class MetricValue(BaseModel):
    name: str
    value: float
    baseline_observations: int
    baseline_median: float | None = None
    anomaly: dict[str, Any] | None = None


class MetricsReport(BaseModel):
    root: str
    dataset: str
    timestamp: str
    values: list[MetricValue]


class ImpactResult(BaseModel):
    file: str
    impacted_nodes: list[GraphNode]
    paths: list[list[str]]
    circular_dependencies: list[list[str]]


class Hypothesis(BaseModel):
    rank: int
    description: str
    evidence: list[str]
    files: list[str]
    confidence: str
    rationale: str
    next_steps: list[str]


class InvestigationResult(BaseModel):
    dataset: str
    status: str
    statement: str
    hypotheses: list[Hypothesis]
    evidence_scope: str
