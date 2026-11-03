import { describe, expect, it } from 'vitest';
import { layoutGraph } from './graphLayout';

describe('layoutGraph', () => {
  it('layers upstream and downstream nodes and preserves confidence metadata', () => {
    const graph = layoutGraph(
      [{ id: 'raw', label: 'raw', kind: 'csv' }, { id: 'model', label: 'model', kind: 'sql' }, { id: 'out', label: 'out', kind: 'table' }],
      [{ source: 'raw', target: 'model', relationship: 'reads', confidence: 'inferred' }, { source: 'model', target: 'out', relationship: 'writes', confidence: 'explicit-config' }]
    );
    expect(graph.nodes.map(node => node.id)).toEqual(['raw', 'model', 'out']);
    expect(graph.nodes[0].position.x).toBeLessThan(graph.nodes[1].position.x);
    expect(graph.nodes[1].position.x).toBeLessThan(graph.nodes[2].position.x);
    expect(graph.edges[1].animated).toBe(true);
  });

  it('handles cyclic and partially invalid input graphs without throwing', () => {
    const graph = layoutGraph([{ id: 'a', label: 'a', kind: 'table' }, { id: 'b', label: 'b', kind: 'sql' }], [
      { source: 'a', target: 'b', relationship: 'reads', confidence: 'inferred' },
      { source: 'b', target: 'a', relationship: 'writes', confidence: 'inferred' },
      { source: 'missing', target: 'b', relationship: 'invalid', confidence: 'inferred' }
    ]);
    expect(graph.nodes).toHaveLength(2);
    expect(graph.edges).toHaveLength(2);
    expect(graph.nodes.every(node => Number.isFinite(node.position.x) && Number.isFinite(node.position.y))).toBe(true);
  });
});
