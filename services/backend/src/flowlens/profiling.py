from __future__ import annotations

from pathlib import Path

import duckdb

from flowlens.models import ColumnProfile, DatasetProfile


def _literal(path: Path) -> str:
    return str(path).replace("'", "''")


def profile_dataset(file: str, sample_rows: int = 100_000) -> DatasetProfile:
    path = Path(file).expanduser().resolve()
    if not path.is_file() or path.suffix.lower() not in {".csv", ".parquet"}:
        raise ValueError("Profile input must be an existing CSV or Parquet file")
    if path.stat().st_size == 0:
        raise ValueError("Cannot profile a zero-byte dataset file")
    con = duckdb.connect(database=":memory:")
    try:
        reader = "read_csv_auto" if path.suffix.lower() == ".csv" else "read_parquet"
        relation = f"{reader}('{_literal(path)}')"
        desc = con.execute(f"DESCRIBE SELECT * FROM {relation}").fetchall()
        total = con.execute(f"SELECT COUNT(*) FROM {relation}").fetchone()[0]
        sampled = total > sample_rows
        source = f"(SELECT * FROM {relation} LIMIT {sample_rows})" if sampled else relation
        sampled_rows = min(total, sample_rows)
        if desc:
            identifiers = ", ".join('"' + row[0].replace('"', '""') + '"' for row in desc)
            duplicate_rows = int(con.execute(
                f"SELECT COALESCE(SUM(duplicate_count - 1), 0) FROM (SELECT COUNT(*) AS duplicate_count FROM {source} GROUP BY {identifiers} HAVING COUNT(*) > 1)"
            ).fetchone()[0])
        else:
            duplicate_rows = 0
        profiles: list[ColumnProfile] = []
        for col_name, data_type, *_ in desc:
            ident = '"' + col_name.replace('"', '""') + '"'
            row = con.execute(
                f"SELECT COUNT(*) - COUNT({ident}), COUNT(DISTINCT {ident}) FROM {source}"
            ).fetchone()
            null_count, unique_count = row
            numeric = any(x in data_type.upper() for x in ("INT", "DECIMAL", "DOUBLE", "FLOAT", "REAL", "NUMERIC"))
            minimum = maximum = mean = median = standard_deviation = None
            quantiles: dict[str, float | None] = {}
            outlier_count = None
            top_values: list[dict] = []
            if numeric:
                stats = con.execute(
                    f"SELECT MIN({ident}), MAX({ident}), AVG({ident}), MEDIAN({ident}), STDDEV_SAMP({ident}), "
                    f"QUANTILE_CONT({ident}, [0.05, 0.25, 0.75, 0.95]) FROM {source}"
                ).fetchone()
                minimum, maximum, mean, median, standard_deviation, qs = stats
                quantiles = {key: float(value) if value is not None else None for key, value in zip(("q05", "q25", "q75", "q95"), qs or [])}
                if quantiles.get("q25") is not None and quantiles.get("q75") is not None:
                    iqr = quantiles["q75"] - quantiles["q25"]
                    lower, upper = quantiles["q25"] - 1.5 * iqr, quantiles["q75"] + 1.5 * iqr
                    outlier_count = int(con.execute(
                        f"SELECT COUNT(*) FROM {source} WHERE {ident} < ? OR {ident} > ?", [lower, upper]
                    ).fetchone()[0])
            elif any(token in data_type.upper() for token in ("DATE", "TIME")):
                minimum, maximum = con.execute(f"SELECT MIN({ident}), MAX({ident}) FROM {source}").fetchone()
                minimum = str(minimum) if minimum is not None else None
                maximum = str(maximum) if maximum is not None else None
            else:
                categories = con.execute(
                    f"SELECT CAST({ident} AS VARCHAR), COUNT(*) AS frequency FROM {source} "
                    f"WHERE {ident} IS NOT NULL GROUP BY {ident} ORDER BY frequency DESC, CAST({ident} AS VARCHAR) LIMIT 10"
                ).fetchall()
                top_values = [{"value": value, "count": int(count)} for value, count in categories]
            profiles.append(ColumnProfile(
                name=col_name, data_type=data_type, null_count=int(null_count),
                null_percent=(float(null_count) / max(sampled_rows, 1)) * 100,
                distinct_count=int(unique_count), minimum=minimum, maximum=maximum,
                mean=mean, median=median, standard_deviation=standard_deviation,
                quantiles=quantiles, outlier_count=outlier_count,
                outlier_percent=(float(outlier_count) / max(sampled_rows, 1)) * 100 if outlier_count is not None else None,
                top_values=top_values,
            ))
        return DatasetProfile(
            file=str(path), rows=int(total), columns=len(desc), sampled=sampled,
            sample_limit=sample_rows, statistics="first-N rows" if sampled else "complete dataset",
            duplicate_rows=duplicate_rows,
            duplicate_percent=(float(duplicate_rows) / max(min(total, sample_rows), 1)) * 100,
            profiles=profiles,
        )
    except duckdb.Error as exc:
        raise ValueError(f"Could not profile dataset: {exc}") from exc
    finally:
        con.close()
