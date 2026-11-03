from __future__ import annotations

import math
import statistics
from typing import Any


def _flag(metric: str, observed: float, baseline: list[float], sensitivity: float) -> dict[str, Any] | None:
    if len(baseline) < 5:
        return None
    median = statistics.median(baseline)
    deviations = [abs(value - median) for value in baseline]
    mad = statistics.median(deviations)
    if mad > 0:
        score = 0.6745 * (observed - median) / mad
        is_outlier = abs(score) >= sensitivity
        spread = 1.4826 * mad
    else:
        ordered = sorted(baseline)
        q1 = statistics.median(ordered[:len(ordered) // 2])
        q3 = statistics.median(ordered[(len(ordered) + 1) // 2:])
        iqr = q3 - q1
        if iqr == 0:
            if observed == median:
                return None
            score = math.copysign(999.0, observed - median)
            spread = 0.0
            is_outlier = True
        else:
            lower, upper = q1 - sensitivity * 0.5 * iqr, q3 + sensitivity * 0.5 * iqr
            is_outlier = observed < lower or observed > upper
            score = (observed - median) / (iqr / 1.349)
            spread = iqr / 1.349
    if not is_outlier:
        return None
    return {
        "metric": metric,
        "observed": observed,
        "baseline_median": median,
        "baseline_spread": spread,
        "baseline_observations": len(baseline),
        "score": score,
        "sensitivity": sensitivity,
        "explanation": f"Observed {observed:.4g}; baseline median {median:.4g} across {len(baseline)} prior profiles (robust score {score:.2f}).",
        "classification": "statistical signal, not a confirmed failure",
    }


def detect_profile_anomalies(profile: dict[str, Any], prior_events: list[dict[str, Any]], sensitivity: float = 3.5) -> list[dict[str, Any]]:
    if not 1.0 <= sensitivity <= 8.0:
        raise ValueError("Sensitivity must be between 1 and 8")
    prior_profiles = [event["payload"] for event in prior_events if isinstance(event.get("payload"), dict)]
    anomalies: list[dict[str, Any]] = []
    row_signal = _flag("row_count", float(profile["rows"]), [float(p["rows"]) for p in prior_profiles if isinstance(p.get("rows"), (int, float))], sensitivity)
    if row_signal:
        anomalies.append(row_signal)
    duplicate_signal = _flag("duplicate_percent", float(profile.get("duplicate_percent") or 0),
                             [float(p["duplicate_percent"]) for p in prior_profiles if isinstance(p.get("duplicate_percent"), (int, float))], sensitivity)
    if duplicate_signal:
        anomalies.append(duplicate_signal)
    prior_by_name = [{c.get("name"): c for c in p.get("profiles", []) if isinstance(c, dict)} for p in prior_profiles]
    for column in profile.get("profiles", []):
        name = column["name"]
        values = [entry[name].get("null_percent") for entry in prior_by_name if name in entry and isinstance(entry[name].get("null_percent"), (int, float))]
        signal = _flag(f"null_percent:{name}", float(column.get("null_percent") or 0), [float(v) for v in values], sensitivity)
        if signal:
            anomalies.append(signal)
        for field in ("mean", "median", "standard_deviation", "outlier_percent"):
            observed = column.get(field)
            historical = [entry[name].get(field) for entry in prior_by_name if name in entry]
            baseline = [float(value) for value in historical if isinstance(value, (int, float)) and math.isfinite(value)]
            if isinstance(observed, (int, float)) and math.isfinite(observed):
                distribution_signal = _flag(f"{field}:{name}", float(observed), baseline, sensitivity)
                if distribution_signal:
                    anomalies.append(distribution_signal)
        for quantile in ("q05", "q25", "q75", "q95"):
            observed_quantile = (column.get("quantiles") or {}).get(quantile)
            previous_quantiles = [(entry[name].get("quantiles") or {}).get(quantile)
                                  for entry in prior_by_name if name in entry]
            quantile_baseline = [float(value) for value in previous_quantiles
                                 if isinstance(value, (int, float)) and math.isfinite(value)]
            if isinstance(observed_quantile, (int, float)) and math.isfinite(observed_quantile):
                quantile_signal = _flag(f"{quantile}:{name}", float(observed_quantile), quantile_baseline, sensitivity)
                if quantile_signal:
                    anomalies.append(quantile_signal)
        prior_column = next((candidate for prior in prior_profiles
                             for candidate in prior.get("profiles", [])
                             if isinstance(candidate, dict) and candidate.get("name") == name
                             and candidate.get("top_values")), None)
        current_top = column.get("top_values") or []
        if prior_column and current_top:
            old_distribution = {str(item.get("value")): float(item.get("count", 0)) for item in prior_column["top_values"]}
            new_distribution = {str(item.get("value")): float(item.get("count", 0)) for item in current_top}
            categories = set(old_distribution) | set(new_distribution)
            old_total = sum(old_distribution.values())
            new_total = sum(new_distribution.values())
            if old_total > 0 and new_total > 0:
                old_prob = {key: old_distribution.get(key, 0.0) / old_total for key in categories}
                new_prob = {key: new_distribution.get(key, 0.0) / new_total for key in categories}
                midpoint = {key: (old_prob[key] + new_prob[key]) / 2 for key in categories}
                divergence = 0.5 * sum(p * math.log(p / midpoint[key]) for key, p in old_prob.items() if p > 0)
                divergence += 0.5 * sum(p * math.log(p / midpoint[key]) for key, p in new_prob.items() if p > 0)
                threshold = min(0.5, 0.05 * sensitivity)
                if divergence >= threshold:
                    anomalies.append({
                        "metric": f"category_distribution:{name}",
                        "observed": divergence,
                        "baseline_divergence_threshold": threshold,
                        "baseline_observations": 1,
                        "explanation": f"Top-category frequencies for {name} differ from the previous profile (Jensen–Shannon divergence {divergence:.3f}; alert threshold {threshold:.3f}).",
                        "classification": "distribution comparison signal, not a confirmed failure",
                    })
    return anomalies


def detect_metric_anomaly(name: str, observed: float, baseline: list[float], sensitivity: float = 3.5) -> dict[str, Any] | None:
    if not 1.0 <= sensitivity <= 8.0:
        raise ValueError("Sensitivity must be between 1 and 8")
    return _flag(f"kpi:{name}", observed, baseline, sensitivity)
