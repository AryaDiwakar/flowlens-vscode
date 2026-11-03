from __future__ import annotations

from pathlib import Path

import networkx as nx

from flowlens.models import ImpactResult, ScanResult


def impact_analysis(scan: ScanResult, selected_file: str) -> ImpactResult:
    graph = nx.DiGraph()
    graph.add_nodes_from(node.id for node in scan.nodes)
    graph.add_edges_from((edge.source, edge.target) for edge in scan.edges)
    selected_id = f"file:{Path(selected_file).as_posix()}"
    if selected_id not in graph:
        raise ValueError(f"No statically inferred pipeline node found for {selected_file}")
    reachable = nx.descendants(graph, selected_id)
    lookup = {node.id: node for node in scan.nodes}
    impacted = [lookup[node] for node in reachable if node in lookup]
    impacted.sort(key=lambda node: (node.kind, node.label.lower()))
    paths: list[list[str]] = []
    shortest_paths = nx.single_source_shortest_path(graph, selected_id)
    for node_id in sorted(reachable):
        if node_id not in lookup:
            continue
        path = shortest_paths.get(node_id)
        if path:
            paths.append(path)
    cycles = []
    for component in nx.strongly_connected_components(graph):
        if len(component) == 1:
            only = next(iter(component))
            if not graph.has_edge(only, only):
                continue
        subgraph = graph.subgraph(component)
        try:
            cycle = nx.find_cycle(subgraph)
            if cycle:
                ordered = [cycle[0][0], *(edge[1] for edge in cycle)]
                cycles.append(ordered)
        except nx.NetworkXNoCycle:
            continue
        if len(cycles) >= 50:
            break
    return ImpactResult(file=selected_file, impacted_nodes=impacted, paths=paths, circular_dependencies=cycles)
