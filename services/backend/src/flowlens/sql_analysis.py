from __future__ import annotations

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from flowlens.models import Finding, SQLResult


def _table_name(table: exp.Table) -> str:
    return table.sql(dialect=None)


def analyze_sql(sql: str, file: str, dialect: str = "") -> SQLResult:
    findings: list[Finding] = []
    if not sql.strip():
        return SQLResult(findings=[], reads=[], writes=[], ctes=[], cte_dependencies={})
    try:
        statements = sqlglot.parse(sql, read=dialect or None)
    except (ParseError, ValueError) as exc:
        details = exc.errors[0] if exc.errors else {}
        line = int(details.get("line") or 1)
        findings.append(Finding(
            code="sql.parse", severity="error", message="SQL could not be parsed.",
            file=file, line=line, evidence=str(exc).splitlines()[0][:240],
            suggestion="Check the SQL syntax and selected dialect.",
        ))
        return SQLResult(findings=findings, reads=[], writes=[], ctes=[], cte_dependencies={})

    reads: set[str] = set()
    writes: set[str] = set()
    cte_names: set[str] = set()
    cte_dependencies: dict[str, set[str]] = {}
    for statement in statements:
        ctes = list(statement.find_all(exp.CTE))
        names = {cte.alias_or_name.lower() for cte in ctes}
        canonical_names = {cte.alias_or_name.lower(): cte.alias_or_name for cte in ctes}
        cte_names.update(cte.alias_or_name for cte in ctes)
        for cte in ctes:
            cte_dependencies.setdefault(cte.alias_or_name, set()).update(
                canonical_names[table.name.lower()] for table in cte.this.find_all(exp.Table) if table.name.lower() in names
            )
        write_target = None
        if isinstance(statement, exp.Create) and isinstance(statement.this, exp.Table):
            write_target = statement.this
        elif isinstance(statement, (exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Drop, exp.Alter)) and isinstance(statement.this, exp.Table):
            write_target = statement.this
        for table in statement.find_all(exp.Table):
            if table is write_target:
                continue
            name = table.name.lower()
            if name in names:
                continue
            else:
                reads.add(_table_name(table))

        main_query = statement.args.get("expression") if isinstance(statement, exp.Create) else statement
        if main_query is not None and main_query.args.get("with"):
            main_query = main_query.copy()
            main_query.set("with", None)
        used_ctes = {table.name.lower() for table in main_query.find_all(exp.Table) if table.name.lower() in names} if main_query else set()
        pending_ctes = list(used_ctes)
        while pending_ctes:
            current_cte = pending_ctes.pop()
            for dependency in cte_dependencies.get(canonical_names.get(current_cte, current_cte), set()):
                dependency_key = dependency.lower()
                if dependency_key not in used_ctes:
                    used_ctes.add(dependency_key)
                    pending_ctes.append(dependency_key)
        for cte in ctes:
            if cte.alias_or_name.lower() not in used_ctes:
                findings.append(Finding(
                    code="sql.unused_cte", severity="warning",
                    message=f"CTE '{cte.alias_or_name}' is not referenced by this statement.",
                    file=file, line=cte.meta.get("line", 1), evidence=cte.sql()[:240],
                    suggestion="Remove the CTE or reference it in the query.",
                ))

        if isinstance(statement, exp.Create) and isinstance(statement.this, exp.Table):
            writes.add(_table_name(statement.this))
        elif isinstance(statement, (exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Drop, exp.Alter)):
            target = statement.this
            if isinstance(target, exp.Table):
                writes.add(_table_name(target))

        for star in statement.find_all(exp.Star):
            # COUNT(*) and similar function arguments are not projection wildcards.
            if isinstance(star.parent, exp.Func):
                continue
            findings.append(Finding(
                code="sql.select_star", severity="warning",
                message="SELECT * makes the output schema sensitive to upstream changes.",
                file=file, line=star.meta.get("line", 1), evidence="*",
                suggestion="Select the required columns explicitly.",
            ))

        for join in statement.find_all(exp.Join):
            if (not join.args.get("on") and not join.args.get("using")
                    and str(join.args.get("kind") or "").upper() != "CROSS"
                    and not join.args.get("natural")):
                findings.append(Finding(
                    code="sql.join_without_condition", severity="warning",
                    message="JOIN has no ON or USING condition; this may create a Cartesian product.",
                    file=file, line=join.meta.get("line", 1), evidence=join.sql()[:240],
                    suggestion="Add an explicit join condition if a Cartesian product is not intended.",
                ))

        for select in statement.find_all(exp.Select):
            projections = list(select.expressions)
            aggregates = [projection for projection in projections if projection.find(exp.AggFunc)]
            group = select.args.get("group")
            if not aggregates:
                continue
            group_expressions = list(group.expressions) if group else []
            grouped_exact = {expression.sql().lower() for expression in group_expressions}
            grouped_columns = {expression.sql().lower() for expression in group_expressions if isinstance(expression, exp.Column)}
            grouped_aliases = {expression.name.lower() for expression in group_expressions if isinstance(expression, exp.Column)}
            grouped_positions = {int(expression.this) - 1 for expression in group_expressions
                                 if isinstance(expression, exp.Literal) and expression.is_int and int(expression.this) > 0}
            for projection_index, projection in enumerate(projections):
                if projection.find(exp.AggFunc) or isinstance(projection, exp.Star):
                    continue
                bare_projection = projection.this if isinstance(projection, exp.Alias) else projection
                if (projection_index in grouped_positions or bare_projection.sql().lower() in grouped_exact
                        or (isinstance(projection, exp.Alias) and projection.alias.lower() in grouped_aliases)):
                    continue
                columns = list(bare_projection.find_all(exp.Column))
                ungrouped = [column for column in columns
                             if column.sql().lower() not in grouped_columns
                             and column.name.lower() not in grouped_aliases]
                if ungrouped:
                    offending = ungrouped[0]
                    findings.append(Finding(
                        code="sql.ungrouped_column", severity="warning",
                        message=f"Selected column '{offending.sql()}' is not aggregated or listed in GROUP BY.",
                        file=file, line=offending.meta.get("line", 1), evidence=projection.sql()[:200],
                        suggestion="Add the column to GROUP BY or wrap it in an aggregate expression.",
                    ))

        max_depth = 0
        for select in statement.find_all(exp.Select):
            depth = 1
            parent = select.parent
            while parent is not None:
                if isinstance(parent, exp.Select):
                    depth += 1
                parent = parent.parent
            max_depth = max(max_depth, depth)
        if max_depth >= 4:
            findings.append(Finding(
                code="sql.nested_query_depth", severity="info",
                message=f"Query has nested SELECT depth {max_depth}; review readability and optimizer behavior.",
                file=file, line=1, evidence=f"nested SELECT depth={max_depth}",
                suggestion="Consider factoring deeply nested logic into named CTEs where that improves clarity.",
            ))

        for mutation in statement.find_all((exp.Delete, exp.Update)):
            if not mutation.args.get("where"):
                findings.append(Finding(
                    code="sql.unbounded_mutation", severity="warning",
                    message=f"{mutation.key.upper()} has no WHERE clause and may affect every row.",
                    file=file, line=mutation.meta.get("line", 1), evidence=mutation.sql()[:240],
                    suggestion="Add a restrictive WHERE clause or confirm the full-table operation is intentional.",
                ))

    return SQLResult(
        findings=findings, reads=sorted(reads), writes=sorted(writes), ctes=sorted(cte_names),
        cte_dependencies={name: sorted(dependencies) for name, dependencies in cte_dependencies.items()},
    )
