from __future__ import annotations

import ast
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock

import yaml

from flowlens.models import Asset, Finding, GraphEdge, GraphNode, ScanResult
from flowlens.sql_analysis import analyze_sql

IGNORED_DIRS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".flowlens", ".vscode-test",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox", ".next", ".turbo", "dist", "build", "target",
}
KINDS = {
    ".sql": "sql", ".py": "python", ".csv": "csv", ".parquet": "parquet",
    ".yml": "yaml", ".yaml": "yaml", ".json": "json",
}


@dataclass
class FileContribution:
    asset: Asset
    nodes: dict[str, GraphNode] = field(default_factory=dict)
    edges: dict[tuple[str, str, str], GraphEdge] = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)
    unresolved: set[str] = field(default_factory=set)


_CACHE: dict[tuple[str, str], dict[str, tuple[str, FileContribution]]] = {}
_SCAN_LOCK = RLock()


def _python_paths(source: str) -> list[str]:
    """Return literal paths used with common dataframe file readers only."""
    paths: set[str] = set()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else ""
        if name in {"read_csv", "read_parquet", "read_json"} and node.args:
            value = node.args[0]
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                paths.add(value.value)
    return sorted(paths)


def _has_dynamic_python_reads(source: str) -> bool:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else ""
        if name in {"read_csv", "read_parquet", "read_json"} and node.args:
            argument = node.args[0]
            if not isinstance(argument, ast.Constant) or not isinstance(argument.value, str):
                return True
    return False


def _python_output_paths(source: str) -> tuple[list[str], bool]:
    paths: set[str] = set()
    dynamic = False
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return [], False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else ""
        if name not in {"to_csv", "to_parquet", "to_json"} or not node.args:
            continue
        argument = node.args[0]
        if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
            paths.add(argument.value)
        else:
            dynamic = True
    return sorted(paths), dynamic


def _python_imports(tree: ast.Module, source_file: Path, root: Path) -> list[Path]:
    targets: set[Path] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
            parents = [root] * len(modules)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                parent = source_file.parent
                for _ in range(max(0, node.level - 1)):
                    parent = parent.parent
            else:
                parent = root
            modules = [node.module or ""]
            parents = [parent] * len(modules)
        else:
            continue
        for module, parent in zip(modules, parents):
            if not module:
                continue
            stem = Path(*module.split("."))
            for candidate in (parent / stem.with_suffix(".py"), parent / stem / "__init__.py"):
                resolved = candidate.resolve()
                if resolved.is_relative_to(root) and resolved.is_file():
                    targets.add(resolved)
                    break
    return sorted(targets)


def _pipeline_config(data: object) -> list[dict]:
    if isinstance(data, dict):
        pipelines = data.get("pipelines")
        if isinstance(pipelines, list):
            return [pipeline for pipeline in pipelines if isinstance(pipeline, dict) and isinstance(pipeline.get("tasks"), list)]
        if isinstance(data.get("tasks"), list):
            return [{"name": data.get("name", "pipeline"), "tasks": data["tasks"]}]
    return []


def _file_signature(path: Path, kind: str) -> str:
    stat = path.stat()
    if kind in {"csv", "parquet"}:
        return f"{stat.st_size}:{stat.st_mtime_ns}"
    digest = hashlib.blake2b(path.read_bytes(), digest_size=16).hexdigest()
    return digest


def _scan_file(path: Path, base: Path, dialect: str) -> FileContribution:
    rel = path.relative_to(base).as_posix()
    kind = KINDS[path.suffix.lower()]
    asset_kind = kind
    contribution = FileContribution(asset=Asset(path=rel, kind=kind, size_bytes=path.stat().st_size))
    config_data = None
    if kind == "yaml":
        try:
            config_data = yaml.safe_load(path.read_text(encoding="utf-8", errors="replace"))
            if _pipeline_config(config_data):
                asset_kind = "pipeline"
        except yaml.YAMLError as exc:
            mark = getattr(exc, "problem_mark", None)
            contribution.findings.append(Finding(
                code="config.yaml_invalid", severity="warning", message=f"Invalid YAML: {exc.problem or 'parse error'}",
                file=rel, line=mark.line + 1 if mark else 1, evidence="", suggestion="Correct the YAML syntax.",
            ))
    elif kind == "json":
        try:
            json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except json.JSONDecodeError as exc:
            contribution.findings.append(Finding(code="config.json_invalid", severity="warning", message="Invalid JSON configuration.", file=rel, line=exc.lineno, evidence=exc.msg, suggestion="Correct the JSON syntax."))
    contribution.asset.kind = asset_kind

    if kind == "sql":
        result = analyze_sql(path.read_text(encoding="utf-8", errors="replace"), rel, dialect)
        contribution.findings.extend(result.findings)
        transform_id = f"file:{rel}"
        contribution.nodes[transform_id] = GraphNode(id=transform_id, label=path.name, kind="sql", file=rel)
        for table in result.reads:
            table_id = f"table:{table.lower()}"
            contribution.nodes.setdefault(table_id, GraphNode(id=table_id, label=table, kind="table"))
            contribution.edges[(table_id, transform_id, "reads")] = GraphEdge(source=table_id, target=transform_id, relationship="reads")
        for table in result.writes:
            table_id = f"table:{table.lower()}"
            contribution.nodes.setdefault(table_id, GraphNode(id=table_id, label=table, kind="table"))
            contribution.edges[(transform_id, table_id, "writes")] = GraphEdge(source=transform_id, target=table_id, relationship="writes")
        for name, dependencies in result.cte_dependencies.items():
            cte_id = f"cte:{rel}#{name}"
            contribution.nodes[cte_id] = GraphNode(id=cte_id, label=name, kind="cte", file=rel)
            for dependency in dependencies:
                dependency_id = f"cte:{rel}#{dependency}"
                contribution.nodes.setdefault(dependency_id, GraphNode(id=dependency_id, label=dependency, kind="cte", file=rel))
                contribution.edges[(dependency_id, cte_id, "cte_depends_on")] = GraphEdge(source=dependency_id, target=cte_id, relationship="cte_depends_on", confidence="parsed")
    elif kind == "python":
        source = path.read_text(encoding="utf-8", errors="replace")
        transform_id = f"file:{rel}"
        contribution.nodes[transform_id] = GraphNode(id=transform_id, label=path.name, kind="python", file=rel)
        try:
            syntax_tree = ast.parse(source)
        except SyntaxError as exc:
            contribution.findings.append(Finding(code="python.parse", severity="error", message=exc.msg, file=rel, line=exc.lineno or 1, evidence=(exc.text or "").strip(), suggestion="Fix the Python syntax before relying on static dependency extraction."))
            contribution.unresolved.add(f"{rel}: Python syntax error; static dependencies may be incomplete")
            syntax_tree = None
        if syntax_tree is not None:
            for imported in _python_imports(syntax_tree, path, base):
                imported_rel = imported.relative_to(base).as_posix()
                imported_id = f"file:{imported_rel}"
                contribution.nodes.setdefault(imported_id, GraphNode(id=imported_id, label=imported.name, kind="python", file=imported_rel))
                contribution.edges[(imported_id, transform_id, "imports")] = GraphEdge(source=imported_id, target=transform_id, relationship="imports", confidence="static-import")
        for target in _python_paths(source):
            resolved = (path.parent / target).resolve()
            if resolved.is_relative_to(base) and resolved.exists():
                file_rel = resolved.relative_to(base).as_posix()
                dataset_id = f"file:{file_rel}"
                contribution.nodes.setdefault(dataset_id, GraphNode(id=dataset_id, label=resolved.name, kind=KINDS.get(resolved.suffix.lower(), "dataset"), file=file_rel))
                contribution.edges[(dataset_id, transform_id, "reads")] = GraphEdge(source=dataset_id, target=transform_id, relationship="reads", confidence="static-literal")
            else:
                contribution.unresolved.add(f"{rel}: dynamic or unresolved input path {target!r}")
        if _has_dynamic_python_reads(source):
            contribution.unresolved.add(f"{rel}: recognized dataframe reader uses a non-literal path; input dependency was not inferred")
        for target in _python_output_paths(source)[0]:
            resolved = (path.parent / target).resolve()
            if resolved.is_relative_to(base):
                output_rel = resolved.relative_to(base).as_posix()
                output_id = f"file:{output_rel}"
                output_kind = KINDS.get(resolved.suffix.lower(), "dataset")
                contribution.nodes.setdefault(output_id, GraphNode(id=output_id, label=resolved.name, kind=output_kind, file=output_rel))
                contribution.edges[(transform_id, output_id, "writes")] = GraphEdge(source=transform_id, target=output_id, relationship="writes", confidence="static-literal")
            else:
                contribution.unresolved.add(f"{rel}: output path {target!r} resolves outside the workspace and was not linked")
        if _python_output_paths(source)[1]:
            contribution.unresolved.add(f"{rel}: recognized dataframe writer uses a non-literal path; output dependency was not inferred")
    elif kind in {"csv", "parquet"}:
        asset_id = f"file:{rel}"
        contribution.nodes[asset_id] = GraphNode(id=asset_id, label=path.name, kind=kind, file=rel)
    elif kind == "yaml":
        for pipeline_index, pipeline in enumerate(_pipeline_config(config_data)):
            pipeline_name = str(pipeline.get("name") or f"pipeline-{pipeline_index + 1}")
            tasks = [task for task in pipeline.get("tasks", []) if isinstance(task, dict) and task.get("name")]
            known_tasks = {str(task["name"]) for task in tasks}
            for task in tasks:
                task_name = str(task["name"])
                task_id = f"pipeline:{rel}#{pipeline_name}/{task_name}"
                contribution.nodes[task_id] = GraphNode(id=task_id, label=task_name, kind="pipeline_task", file=rel)
                dependencies = task.get("depends_on", [])
                if isinstance(dependencies, str): dependencies = [dependencies]
                for dependency in dependencies if isinstance(dependencies, list) else []:
                    dep_id = f"pipeline:{rel}#{pipeline_name}/{dependency}"
                    if str(dependency) in known_tasks:
                        contribution.nodes.setdefault(dep_id, GraphNode(id=dep_id, label=str(dependency), kind="pipeline_task", file=rel))
                        contribution.edges[(dep_id, task_id, "executes_before")] = GraphEdge(source=dep_id, target=task_id, relationship="executes_before", confidence="explicit-config")
                    else:
                        contribution.unresolved.add(f"{rel}: pipeline task {task_name!r} depends on unknown task {dependency!r}")
                script = task.get("file") or task.get("script")
                if isinstance(script, str):
                    script_path = (path.parent / script).resolve()
                    if script_path.is_file() and script_path.is_relative_to(base):
                        script_rel = script_path.relative_to(base).as_posix()
                        script_id = f"file:{script_rel}"
                        contribution.nodes.setdefault(script_id, GraphNode(id=script_id, label=script_path.name, kind=KINDS.get(script_path.suffix.lower(), "transform"), file=script_rel))
                        contribution.edges[(task_id, script_id, "executes")] = GraphEdge(source=task_id, target=script_id, relationship="executes", confidence="explicit-config")
                    else:
                        contribution.unresolved.add(f"{rel}: task {task_name!r} references missing script {script!r}")
                for table in task.get("inputs", []) if isinstance(task.get("inputs", []), list) else []:
                    table_id = f"table:{str(table).lower()}"
                    contribution.nodes.setdefault(table_id, GraphNode(id=table_id, label=str(table), kind="table"))
                    contribution.edges[(table_id, task_id, "configured_input")] = GraphEdge(source=table_id, target=task_id, relationship="configured_input", confidence="explicit-config")
                for table in task.get("outputs", []) if isinstance(task.get("outputs", []), list) else []:
                    table_id = f"table:{str(table).lower()}"
                    contribution.nodes.setdefault(table_id, GraphNode(id=table_id, label=str(table), kind="table"))
                    contribution.edges[(task_id, table_id, "configured_output")] = GraphEdge(source=task_id, target=table_id, relationship="configured_output", confidence="explicit-config")
    return contribution


def _scan_workspace(root: str, dialect: str = "") -> ScanResult:
    base = Path(root).expanduser().resolve()
    if not base.is_dir():
        raise ValueError("Workspace root must be an existing directory")
    files = sorted(path for path in base.rglob("*")
                   if path.is_file() and path.suffix.lower() in KINDS
                   and not any(part in IGNORED_DIRS for part in path.relative_to(base).parts))
    key = (str(base), dialect)
    old_cache = _CACHE.get(key, {})
    current_paths = {path.relative_to(base).as_posix() for path in files}
    files_changed = current_paths != set(old_cache)
    current_cache: dict[str, tuple[str, FileContribution]] = {}
    scanned = reused = 0
    parsed_paths: list[str] = []
    for path in files:
        rel = path.relative_to(base).as_posix()
        kind = KINDS[path.suffix.lower()]
        signature = _file_signature(path, kind)
        cached = old_cache.get(rel)
        must_reparse_references = files_changed and kind in {"python", "yaml"}
        if cached and cached[0] == signature and not must_reparse_references:
            current_cache[rel] = cached
            reused += 1
        else:
            current_cache[rel] = (signature, _scan_file(path, base, dialect))
            scanned += 1
            parsed_paths.append(rel)
    _CACHE[key] = current_cache
    if len(_CACHE) > 16:
        oldest = next(iter(_CACHE))
        if oldest != key:
            _CACHE.pop(oldest, None)

    assets: list[Asset] = []
    nodes: dict[str, GraphNode] = {}
    edges: dict[tuple[str, str, str], GraphEdge] = {}
    findings: list[Finding] = []
    unresolved: set[str] = set()
    for rel in sorted(current_cache):
        contribution = current_cache[rel][1]
        assets.append(contribution.asset)
        for node_id, node in contribution.nodes.items():
            nodes.setdefault(node_id, node)
        edges.update(contribution.edges)
        findings.extend(contribution.findings)
        unresolved.update(contribution.unresolved)
    declared_table_names: set[str] = set()
    written_table_names: set[str] = set()
    name_match_edges: dict[tuple[str, str, str], GraphEdge] = {}
    for edge in list(edges.values()):
        if edge.relationship in {"writes", "configured_output", "configured_input"}:
            node = nodes.get(edge.target if edge.relationship != "configured_input" else edge.source)
            if node and node.kind == "table":
                name = node.label.casefold().rsplit(".", 1)[-1]
                declared_table_names.add(name)
                if edge.relationship in {"writes", "configured_output"}:
                    written_table_names.add(name)
    datasets_by_name: dict[str, list[str]] = {}
    for asset in assets:
        if asset.kind in {"csv", "parquet"}:
            datasets_by_name.setdefault(Path(asset.path).stem.casefold(), []).append(asset.path)
    for edge in edges.values():
        if edge.relationship != "reads":
            continue
        table = nodes.get(edge.source)
        if not table or table.kind != "table":
            continue
        local_name = table.label.casefold().rsplit(".", 1)[-1].strip('`"[]')
        candidates = datasets_by_name.get(local_name, [])
        if local_name not in written_table_names and len(candidates) == 1:
            dataset_id = f"file:{candidates[0]}"
            name_match_edges[(dataset_id, edge.source, "name_matches_table")] = GraphEdge(
                source=dataset_id, target=edge.source, relationship="name_matches_table", confidence="name-match"
            )
        elif len(candidates) > 1:
            unresolved.add(f"{table.label}: matches multiple workspace datasets by filename; add explicit pipeline configuration to disambiguate")
        elif local_name not in declared_table_names:
            unresolved.add(f"{table.label}: SQL dependency is not mapped to a workspace dataset or declared output; it may be an external database table")
    edges.update(name_match_edges)
    return ScanResult(root=str(base), assets=assets, nodes=list(nodes.values()), edges=list(edges.values()),
                      findings=findings, unresolved=sorted(unresolved),
                      scan_stats={"discovered_files": len(files), "parsed_files": scanned,
                                  "reused_files": reused, "removed_files": len(set(old_cache) - current_paths),
                                  "changed_files": sorted(parsed_paths)})


def scan_workspace(root: str, dialect: str = "") -> ScanResult:
    with _SCAN_LOCK:
        return _scan_workspace(root, dialect)
