import type { Edge, Node } from '@xyflow/react';
import type { GraphEdgeData, GraphNodeData } from './contracts';

export type FlowNode = Node<{ label: string; kind: string; file?: string }>;
export type FlowEdge = Edge<{ relationship: string; confidence: string }>;

/** Deterministic layered layout; cycles are retained and positioned in a fallback layer. */
export function layoutGraph(sourceNodes: GraphNodeData[], sourceEdges: GraphEdgeData[]): { nodes: FlowNode[]; edges: FlowEdge[] } {
  const known = new Map(sourceNodes.map(node => [node.id, node]));
  const usableEdges = sourceEdges.filter(edge => known.has(edge.source) && known.has(edge.target));
  const indegree = new Map(sourceNodes.map(node => [node.id, 0]));
  const outgoing = new Map(sourceNodes.map(node => [node.id, [] as string[]]));
  for (const edge of usableEdges) {
    indegree.set(edge.target, (indegree.get(edge.target) ?? 0) + 1);
    outgoing.get(edge.source)?.push(edge.target);
  }
  const queue = sourceNodes.filter(node => indegree.get(node.id) === 0).map(node => node.id);
  const levels = new Map<string, number>(queue.map(id => [id, 0]));
  while (queue.length) {
    const current = queue.shift()!;
    for (const next of outgoing.get(current) ?? []) {
      levels.set(next, Math.max(levels.get(next) ?? 0, (levels.get(current) ?? 0) + 1));
      indegree.set(next, (indegree.get(next) ?? 1) - 1);
      if (indegree.get(next) === 0) queue.push(next);
    }
  }
  let fallback = Math.max(0, ...levels.values()) + 1;
  for (const node of sourceNodes) if (!levels.has(node.id)) levels.set(node.id, fallback++);
  const buckets = new Map<number, GraphNodeData[]>();
  for (const node of sourceNodes) {
    const level = levels.get(node.id) ?? 0;
    const bucket = buckets.get(level) ?? [];
    bucket.push(node); buckets.set(level, bucket);
  }
  const nodes = sourceNodes.map(node => {
    const level = levels.get(node.id) ?? 0;
    const bucket = buckets.get(level) ?? [];
    return {
      id: node.id,
      type: 'pipelineNode',
      position: { x: level * 250, y: bucket.indexOf(node) * 110 },
      data: { label: node.label, kind: node.kind, file: node.file }
    };
  });
  const edges = usableEdges.map((edge, index) => ({
    id: `${edge.source}-${edge.relationship}-${edge.target}-${index}`,
    source: edge.source,
    target: edge.target,
    label: edge.relationship,
    type: 'smoothstep',
    animated: edge.confidence === 'explicit-config',
    markerEnd: { type: 'arrowclosed' as const },
    data: { relationship: edge.relationship, confidence: edge.confidence }
  }));
  return { nodes, edges };
}
