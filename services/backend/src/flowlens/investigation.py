from __future__ import annotations

from pathlib import Path
from typing import Any

import networkx as nx

from flowlens.models import Finding, Hypothesis, InvestigationResult, ScanResult


def investigate(root: str, dataset: str, scan: ScanResult, events: list[dict[str, Any]]) -> InvestigationResult:
    wanted = Path(dataset).stem.casefold()
    graph = nx.DiGraph()
    graph.add_nodes_from(node.id for node in scan.nodes)
    graph.add_edges_from((edge.source, edge.target) for edge in scan.edges)
    targets = [node.id for node in scan.nodes if node.kind in {"table", "csv", "parquet"}
               and (node.label.casefold() == wanted or Path(node.label).stem.casefold() == wanted
                    or node.label.casefold().rsplit(".", 1)[-1] == wanted)]
    ancestors: set[str] = set()
    for target in targets:
        ancestors.update(nx.ancestors(graph, target))
    upstream_files = {node.file for node in scan.nodes if node.id in ancestors and node.file}
    matching_events = [event for event in events if isinstance(event.get("payload"), dict)
                       and Path(str(event["payload"].get("dataset", ""))).stem.casefold() == wanted]
    hypotheses: list[Hypothesis] = []

    failed_validation = next((event for event in matching_events if event.get("kind") == "validation"
                              and event["payload"].get("failed_checks", 0) > 0), None)
    if failed_validation:
        report = failed_validation["payload"]
        failures = [result for result in report.get("results", []) if not result.get("passed")]
        hypotheses.append(Hypothesis(
            rank=1, description="A recorded data-quality validation failed for this dataset.",
            evidence=[f"{item.get('rule')} check on {item.get('column') or 'dataset'}: {item.get('failed_rows')} failing row(s) — {item.get('message')}" for item in failures[:5]],
            files=[str(report.get("dataset", dataset))], confidence="high for the validation failure; not causal attribution",
            rationale="This is a persisted validation result for the requested dataset, not a correlation inferred from source code.",
            next_steps=["Inspect the sampled failed rows and compare the preceding successful validation run.", "Trace upstream transformations before changing the source dataset."],
        ))

    latest_profile = next((event for event in matching_events if event.get("kind") == "profile"), None)
    if latest_profile:
        for anomaly in latest_profile["payload"].get("anomalies", []):
            hypotheses.append(Hypothesis(
                rank=len(hypotheses) + 1,
                description=f"Statistical deviation observed in {anomaly.get('metric')}.",
                evidence=[str(anomaly.get("explanation", "")), "Statistical deviation is not a confirmed pipeline failure."],
                files=[str(latest_profile["payload"].get("dataset", dataset))],
                confidence="medium statistical signal; correlation does not establish cause",
                rationale="The profile differs from its persisted rolling robust baseline; the baseline count and score are shown above.",
                next_steps=["Compare this metric with upstream dataset profiles and validation runs.", "Check whether the change is expected for this workload or reporting period."],
            ))

    latest_metrics = next((event for event in matching_events if event.get("kind") == "metric"), None)
    if latest_metrics:
        for metric in latest_metrics["payload"].get("values", []):
            anomaly = metric.get("anomaly")
            if not anomaly:
                continue
            hypotheses.append(Hypothesis(
                rank=len(hypotheses) + 1,
                description=f"Configured KPI '{metric.get('name')}' deviated from its local baseline.",
                evidence=[str(anomaly.get("explanation", "")), f"Observed metric value: {metric.get('value')}"],
                files=[str(latest_metrics["payload"].get("dataset", dataset))],
                confidence="medium statistical signal; it does not identify a cause",
                rationale="The configured read-only KPI query returned a value outside its persisted robust baseline.",
                next_steps=["Inspect the KPI query and compare its input rows with the previous observation.", "Trace upstream transformations and validation results before attributing the deviation."],
            ))

    related_findings = [finding for finding in scan.findings if finding.file in upstream_files
                        and finding.code in {"sql.join_without_condition", "sql.unbounded_mutation", "sql.parse"}]
    for finding in related_findings[:5]:
        hypotheses.append(Hypothesis(
            rank=len(hypotheses) + 1,
            description=f"Potential contributing SQL issue: {finding.message}",
            evidence=[finding.evidence or finding.message, f"Static finding at {finding.file}:{finding.line}; it does not prove runtime impact."],
            files=[finding.file], confidence="low-to-medium static signal; verify against runtime data",
            rationale="The transformation is statically upstream of a matching dataset node and contains a flagged SQL pattern.",
            next_steps=[f"Review {finding.file}:{finding.line} and compare its input/output row counts.", "Check execution-specific parameters and join keys before attributing the change."],
        ))

    unresolved_upstream = []
    for edge in scan.edges:
        if edge.relationship != "reads":
            continue
        transformation = next((node for node in scan.nodes if node.id == edge.target), None)
        table = next((node for node in scan.nodes if node.id == edge.source), None)
        if (transformation and transformation.file in upstream_files and table and table.kind == "table"
                and any(item.startswith(f"{table.label}: SQL dependency is not mapped") for item in scan.unresolved)):
            unresolved_upstream.append((table.label, transformation.file))
    for table, file in sorted(set(unresolved_upstream)):
        hypotheses.append(Hypothesis(
            rank=len(hypotheses) + 1,
            description=f"Upstream SQL references '{table}', which is not mapped to a workspace dataset or declared output.",
            evidence=[f"Static reference in {file}.", "The table may be an external database table; no catalog/runtime connection is configured to determine whether it exists."],
            files=[file], confidence="low; dependency is unresolved, not confirmed missing",
            rationale="The workspace scanner could not map this upstream table name to a local asset or declared pipeline output.",
            next_steps=["Check the connected database catalog and environment-specific schema.", "Confirm the table name and permissions in the pipeline runtime."],
        ))

    analysis_events = [event for event in events if event.get("kind") == "analysis"]
    initial_event_id = min((event.get("id", 0) for event in analysis_events), default=None)
    recent_changes: dict[str, str] = {}
    for event in analysis_events:
        if event.get("id") == initial_event_id:
            continue
        for file in event.get("payload", {}).get("changed_files", []):
            if file in upstream_files and file not in recent_changes:
                recent_changes[file] = str(event.get("created_at", "recorded analysis"))
    for file, observed_at in recent_changes.items():
        hypotheses.append(Hypothesis(
            rank=len(hypotheses) + 1,
            description=f"Upstream transformation '{file}' changed before this investigation.",
            evidence=[f"FlowLens recorded a changed-file rescan at {observed_at}.", "No source diff or causal link is available from this observation alone."],
            files=[file], confidence="low; temporal association only",
            rationale="The file was parsed as changed in a persisted incremental scan and is statically upstream of the requested dataset.",
            next_steps=[f"Inspect the editor diff for {file}.", "Compare validation/profile metrics before and after the change."],
        ))

    hypotheses.sort(key=lambda item: (0 if item.confidence.startswith("high") else 1, item.rank))
    for rank, hypothesis in enumerate(hypotheses, start=1):
        hypothesis.rank = rank
    has_recorded = bool(matching_events)
    statement = ("Evidence-ranked investigation candidates are available; none establish causality."
                 if hypotheses else "Insufficient persisted evidence to rank a root cause. Run profiling and configured validation, then compare observations.")
    return InvestigationResult(dataset=dataset, status="hypotheses" if hypotheses else "insufficient_evidence",
                               statement=statement, hypotheses=hypotheses,
                               evidence_scope="Current static workspace scan plus local FlowLens profile/validation history only; no external execution logs are connected.")
