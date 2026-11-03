#!/usr/bin/env python3
"""Run deterministic engine benchmarks; reports are measured, not hard-coded."""
from __future__ import annotations

import argparse
import csv
import json
import random
import statistics
import tempfile
import time
import tracemalloc
from pathlib import Path
from typing import Iterable

from flowlens.anomalies import detect_profile_anomalies
from flowlens.graph_engine import impact_analysis
from flowlens.investigation import investigate
from flowlens.metrics import evaluate_metrics
from flowlens.persistence import list_events
from flowlens.profiling import profile_dataset
from flowlens.quality import run_validation
from flowlens.scanner import scan_workspace
from flowlens.schema import compare_schema

SPLIT_SEEDS = {"development": 202601, "evaluation": 202602}


def metric_counts(expected: set, predicted: set) -> dict[str, float | int]:
    true_positive = len(expected & predicted)
    false_positive = len(predicted - expected)
    false_negative = len(expected - predicted)
    precision = true_positive / (true_positive + false_positive) if true_positive + false_positive else 1.0
    recall = true_positive / (true_positive + false_negative) if true_positive + false_negative else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"true_positive": true_positive, "false_positive": false_positive,
            "false_negative": false_negative, "precision": precision, "recall": recall, "f1": f1}


def average_precision(labels: list[bool], scores: list[float]) -> float:
    positives = sum(labels)
    if not positives:
        return 0.0
    ordered = sorted(zip(scores, labels), key=lambda item: item[0], reverse=True)
    hit = 0
    total_precision = 0.0
    for rank, (_, label) in enumerate(ordered, start=1):
        if label:
            hit += 1
            total_precision += hit / rank
    return total_precision / positives


def _workspace(root: Path, seed: int) -> tuple[set[tuple[str, str]], set[tuple[str, str]]]:
    rng = random.Random(seed)
    root.mkdir(parents=True)
    with (root / "events.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["event_id", "amount"])
        for event_id in range(1, 251):
            writer.writerow([event_id, round(rng.uniform(2, 100), 2)])
    (root / "quality.csv").write_text("customer_id,email,age\n1,a@example.test,20\n2,b@example.test,30\n2,,130\n4,d@example.test,-1\n")
    metadata = root / ".flowlens"
    metadata.mkdir()
    (metadata / "quality.yml").write_text("""dataset: quality.csv
checks:
  - column: email
    rule: not_null
  - column: customer_id
    rule: unique
  - column: age
    rule: range
    min: 0
    max: 120
""")
    (metadata / "metrics.yml").write_text("""dataset: events.csv
metrics:
  - name: total_amount
    sql: SELECT SUM(amount) FROM dataset
""")
    with (root / "health.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["metric", "value", "category", "optional"])
        for index in range(100):
            writer.writerow(["revenue", round(10 + rng.uniform(-0.2, 0.2), 2), "A" if index < 90 else "B", "present"])
    (root / "schema_before.csv").write_text("id,value\n1,old\n2,old\n")
    (root / "schema_after.csv").write_text("id,value,channel\n1,new,web\n2,new,api\n")
    (root / "clean.sql").write_text("CREATE TABLE clean_events AS SELECT event_id, amount FROM events;\n")
    (root / "daily.sql").write_text("CREATE TABLE daily_revenue AS SELECT SUM(amount) AS revenue FROM clean_events;\n")
    (root / "bad_join.sql").write_text("CREATE TABLE suspect AS SELECT * FROM events JOIN clean_events;\n")
    (root / "missing_upstream.sql").write_text("SELECT event_id FROM warehouse.absent_source;\n")
    (root / "cycle_a.sql").write_text("CREATE TABLE cycle_a AS SELECT id FROM cycle_b;\n")
    (root / "cycle_b.sql").write_text("CREATE TABLE cycle_b AS SELECT id FROM cycle_a;\n")
    expected_findings = {("bad_join.sql", "sql.select_star"), ("bad_join.sql", "sql.join_without_condition")}
    expected_edges = {
        ("file:events.csv", "table:events"),
        ("table:events", "file:clean.sql"), ("file:clean.sql", "table:clean_events"),
        ("table:clean_events", "file:daily.sql"), ("file:daily.sql", "table:daily_revenue"),
        ("table:events", "file:bad_join.sql"), ("table:clean_events", "file:bad_join.sql"),
        ("file:bad_join.sql", "table:suspect"),
        ("table:warehouse.absent_source", "file:missing_upstream.sql"),
        ("table:cycle_b", "file:cycle_a.sql"), ("file:cycle_a.sql", "table:cycle_a"),
        ("table:cycle_a", "file:cycle_b.sql"), ("file:cycle_b.sql", "table:cycle_b"),
    }
    return expected_findings, expected_edges


def _robust_score(value: float, baseline: list[float]) -> float:
    median = statistics.median(baseline)
    mad = statistics.median(abs(item - median) for item in baseline)
    if mad == 0:
        return 999.0 if value != median else 0.0
    return 0.6745 * (value - median) / mad


def run(split: str, seed: int) -> dict:
    rng = random.Random(seed + 1)
    with tempfile.TemporaryDirectory(prefix="flowlens-benchmark-") as directory:
        root = Path(directory) / "evaluation-project"
        expected_findings, expected_edges = _workspace(root, seed)
        tracemalloc.start()
        scan_start = time.perf_counter()
        scan = scan_workspace(str(root))
        scan_seconds = time.perf_counter() - scan_start
        _, scan_peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        predictions = {(finding.file, finding.code) for finding in scan.findings}
        actual_edges = {(edge.source, edge.target) for edge in scan.edges}
        sql_metrics = metric_counts(expected_findings, predictions)
        lineage_metrics = metric_counts(expected_edges, actual_edges)
        unresolved_predictions = {item.split(": SQL dependency", 1)[0] for item in scan.unresolved if ": SQL dependency" in item}
        unresolved_expected = {"warehouse.absent_source"}

        bad_join = root / "bad_join.sql"
        bad_join.write_text("CREATE TABLE suspect AS SELECT event_id FROM events JOIN clean_events ON events.event_id = clean_events.event_id;\n")
        incremental_start = time.perf_counter()
        incremental_scan = scan_workspace(str(root))
        incremental_seconds = time.perf_counter() - incremental_start
        impact = impact_analysis(scan, "bad_join.sql")
        root_cause = investigate(str(root), "suspect", scan, list_events(str(root)))
        truth = "bad_join.sql"
        ranks = [index for index, hypothesis in enumerate(root_cause.hypotheses, start=1) if truth in hypothesis.files]
        rank = ranks[0] if ranks else None
        root_metrics = {
            "top1_localization_accuracy": 1.0 if rank == 1 else 0.0,
            "top3_localization_accuracy": 1.0 if rank is not None and rank <= 3 else 0.0,
            "mean_reciprocal_rank": 1.0 / rank if rank else 0.0,
            "ranked_files": [file for hypothesis in root_cause.hypotheses for file in hypothesis.files],
            "ground_truth_file": truth,
        }

        baseline = [999, 1001, 1000, 998, 1002, 1000, 1001, 999]
        prior = [{"payload": {"rows": count, "profiles": []}} for count in baseline]
        cases = [(1001, False), (2500, True), (998, False), (4200, True)]
        predicted = [bool(detect_profile_anomalies({"rows": value, "profiles": []}, prior)) for value, _ in cases]
        anomaly_labels = [label for _, label in cases]
        anomaly_scores = [abs(_robust_score(value, baseline)) for value, _ in cases]
        anomaly_metrics = metric_counts({i for i, label in enumerate(anomaly_labels) if label},
                                        {i for i, label in enumerate(predicted) if label})
        anomaly_metrics["pr_auc_average_precision"] = average_precision(anomaly_labels, anomaly_scores)

        validation = run_validation(str(root), "quality.csv")
        for _ in range(5):
            metric_baseline = evaluate_metrics(str(root), "events.csv")
        with (root / "events.csv").open(newline="", encoding="utf-8") as handle:
            metric_rows = list(csv.reader(handle))
        for row in metric_rows[1:]:
            row[1] = str(float(row[1]) * 4)
        with (root / "events.csv").open("w", newline="", encoding="utf-8") as handle:
            csv.writer(handle).writerows(metric_rows)
        metric_current = evaluate_metrics(str(root), "events.csv")
        kpi_observation = metric_current.values[0]
        baseline_profile = profile_dataset(str(root / "health.csv"))
        prior_profiles = [{"payload": {"rows": baseline_profile.rows,
                           "profiles": [column.model_dump() for column in baseline_profile.profiles]}}
                          for _ in range(5)]
        with (root / "health.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["metric", "value", "category", "optional"])
            for index in range(100):
                writer.writerow(["revenue", round(100 + rng.uniform(-0.2, 0.2), 2), "B" if index < 90 else "A", "" if index < 50 else "present"])
        changed_profile = profile_dataset(str(root / "health.csv"))
        profile_signals = detect_profile_anomalies({
            "rows": changed_profile.rows,
            "profiles": [column.model_dump() for column in changed_profile.profiles],
        }, prior_profiles)
        before_schema = profile_dataset(str(root / "schema_before.csv"))
        after_schema = profile_dataset(str(root / "schema_after.csv"))
        schema_diff = compare_schema("snapshot.csv", after_schema, [{
            "created_at": "synthetic-baseline", "payload": {"profiles": [column.model_dump() for column in before_schema.profiles]}
        }])

        profile_start = time.perf_counter()
        profile = profile_dataset(str(root / "events.csv"), sample_rows=100_000)
        profile_seconds = time.perf_counter() - profile_start

        return {
            "benchmark": "flowlens-synthetic-v1", "split": split, "seed": seed,
            "limitations": ["Synthetic fixtures only; these scores do not predict production accuracy.",
                            "Python memory is tracemalloc heap and excludes native DuckDB allocations.",
                            "Incremental latency measures a single modified SQL file in a warm process."],
            "sql_analysis": sql_metrics,
            "lineage_reconstruction": {**lineage_metrics, "expected_edges": len(expected_edges), "predicted_edges": len(actual_edges)},
            "unresolved_dependency_mapping": {**metric_counts(unresolved_expected, unresolved_predictions),
                                               "expected_unmapped_tables": sorted(unresolved_expected),
                                               "predicted_unmapped_tables": sorted(unresolved_predictions)},
            "data_quality": {"checks": validation.total_checks, "failed_checks": validation.failed_checks,
                             "failed_rows_by_rule": {item.rule: item.failed_rows for item in validation.results}},
            "configured_kpi": {"name": kpi_observation.name, "observed": kpi_observation.value,
                               "baseline_observations": kpi_observation.baseline_observations,
                               "baseline_median": kpi_observation.baseline_median,
                               "flagged": kpi_observation.anomaly is not None},
            "schema_drift": {"has_baseline": schema_diff.has_baseline,
                             "change_kinds": [change.kind for change in schema_diff.changes]},
            "profile_anomaly_signals": [{"metric": signal["metric"], "classification": signal["classification"]}
                                        for signal in profile_signals],
            "anomaly_detection": {**anomaly_metrics, "false_positive_rate": anomaly_metrics["false_positive"] / max(sum(not value for value in anomaly_labels), 1)},
            "root_cause": root_metrics,
            "system_performance": {
                "initial_scan_seconds": scan_seconds, "incremental_scan_seconds": incremental_seconds,
                "initial_scan_python_peak_bytes": scan_peak, "profile_seconds": profile_seconds,
                "incremental_parsed_files": incremental_scan.scan_stats["parsed_files"],
                "incremental_reused_files": incremental_scan.scan_stats["reused_files"],
                "profiled_rows": profile.rows, "profiled_columns": profile.columns,
                "cycle_detected": bool(impact.circular_dependencies),
            },
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", choices=sorted(SPLIT_SEEDS), default="evaluation")
    parser.add_argument("--seed", type=int)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    seed = args.seed if args.seed is not None else SPLIT_SEEDS[args.split]
    report = run(args.split, seed)
    output = args.output or Path(__file__).parent / "reports" / f"{args.split}-{seed}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(output), "results": report}, indent=2))


if __name__ == "__main__":
    main()
