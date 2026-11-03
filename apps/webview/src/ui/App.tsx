import { useEffect, useMemo, useState } from 'react';
import { Background, Controls, MiniMap, ReactFlow, ReactFlowProvider, Handle, Position, type NodeProps } from '@xyflow/react';
import type { DashboardResult, FlowLensApi, GraphNodeData, WorkspaceScan } from '../contracts';
import { layoutGraph, type FlowNode } from '../graphLayout';

const api: FlowLensApi = window.acquireVsCodeApi?.() ?? { postMessage: () => undefined };

function PipelineNode({ data, selected }: NodeProps<FlowNode>) {
  return <div className={`pipeline-node kind-${safeKind(data.kind)}${selected ? ' selected' : ''}`}>
    <Handle type="target" position={Position.Left} />
    <span className="node-kind">{data.kind.replaceAll('_', ' ')}</span>
    <strong title={data.label}>{data.label}</strong>
    {data.file && <small title={data.file}>{data.file}</small>}
    <Handle type="source" position={Position.Right} />
  </div>;
}

const nodeTypes = { pipelineNode: PipelineNode };

export function App() {
  const [scan, setScan] = useState<WorkspaceScan>();
  const [result, setResult] = useState<DashboardResult>();
  const [selected, setSelected] = useState<GraphNodeData>();
  const [filter, setFilter] = useState('all');
  const [query, setQuery] = useState('');
  useEffect(() => {
    const onMessage = (event: MessageEvent) => {
      if (event.data?.type === 'scan') { setScan(event.data.scan as WorkspaceScan); setSelected(undefined); }
      if (event.data?.type === 'result') setResult({ kind: event.data.kind as string, result: event.data.result as Record<string, unknown> });
    };
    window.addEventListener('message', onMessage);
    api.postMessage({ command: 'ready' });
    return () => window.removeEventListener('message', onMessage);
  }, []);

  const visible = useMemo(() => {
    if (!scan) return { nodes: [], edges: [] };
    const term = query.trim().toLowerCase();
    const nodes = scan.nodes.filter(node => (filter === 'all' || node.kind === filter) &&
      (!term || node.label.toLowerCase().includes(term) || node.file?.toLowerCase().includes(term)));
    const ids = new Set(nodes.map(node => node.id));
    const edges = scan.edges.filter(edge => ids.has(edge.source) && ids.has(edge.target));
    return layoutGraph(nodes, edges);
  }, [scan, filter, query]);

  const findings = scan?.findings.filter(item => !query || `${item.file} ${item.message} ${item.code}`.toLowerCase().includes(query.toLowerCase())) ?? [];
  const filters = ['all', ...new Set(scan?.nodes.map(node => node.kind) ?? [])];
  const onNodeClick = (_: unknown, node: FlowNode) => {
    const original = scan?.nodes.find(item => item.id === node.id);
    setSelected(original);
  };

  if (!scan) return <main className="empty-shell">
    <section className="empty-state">
      <div className="brand-mark">FL</div><h1>Workspace intelligence</h1>
      <p>Analyze SQL, statically identifiable Python dependencies, and configured pipeline relationships in this workspace.</p>
      <button className="primary" onClick={() => api.postMessage({ command: 'analyze' })}>Analyze workspace</button>
      <p className="subtle">Analysis runs locally. No project code or datasets are sent to an external service.</p>
    </section>
    {result && <ResultPanel value={result} onDismiss={() => setResult(undefined)} />}
  </main>;

  return <main className="app-shell">
    <header className="topbar">
      <div className="brand-mark small">FL</div><div><h1>FlowLens</h1><span className="subtle">Workspace intelligence</span></div>
      <div className="top-actions">
        <button onClick={() => api.postMessage({ command: 'profile' })}>Profile</button>
        <button onClick={() => api.postMessage({ command: 'validate' })}>Validate</button>
        <button onClick={() => api.postMessage({ command: 'metrics' })}>KPIs</button>
        <button onClick={() => api.postMessage({ command: 'impact' })}>Impact</button>
        <button onClick={() => api.postMessage({ command: 'investigate' })}>Investigate</button>
        <button onClick={() => api.postMessage({ command: 'schema' })}>Schema</button>
        <button onClick={() => api.postMessage({ command: 'history' })}>History</button>
        <button className="primary" onClick={() => api.postMessage({ command: 'analyze' })}>Re-analyze</button>
      </div>
    </header>
    <section className="summary-grid" aria-label="Workspace summary">
      <Metric label="Assets" value={scan.assets.length} /><Metric label="Dependencies" value={scan.edges.length} />
      <Metric label="Findings" value={scan.findings.length} tone={scan.findings.some(f => f.severity === 'error') ? 'danger' : undefined} />
      <Metric label="Unresolved" value={scan.unresolved.length} tone={scan.unresolved.length ? 'warning' : undefined} />
    </section>
    {result && <ResultPanel value={result} onDismiss={() => setResult(undefined)} />}
    <section className="toolbar">
      <div className="filter-row" role="group" aria-label="Filter lineage node types">
        {filters.map(item => <button key={item} className={filter === item ? 'active' : ''} onClick={() => setFilter(item)}>{item === 'all' ? 'All nodes' : item.replaceAll('_', ' ')}</button>)}
      </div>
      <input aria-label="Filter workspace results" placeholder="Filter assets and findings" value={query} onChange={event => setQuery(event.target.value)} />
    </section>
    <section className="content-grid">
      <div className="lineage-panel">
        <div className="section-heading"><div><h2>Pipeline lineage</h2><p>Select a node to inspect it; double-click opens its source.</p></div><span className="count-label">{visible.nodes.length} nodes · {visible.edges.length} edges</span></div>
        <div className="flow-canvas">
          <ReactFlow nodes={visible.nodes} edges={visible.edges} nodeTypes={nodeTypes} onNodeClick={onNodeClick}
            onNodeDoubleClick={(_, node) => node.data.file && api.postMessage({ command: 'openFile', file: node.data.file })}
            fitView fitViewOptions={{ padding: 0.2 }} minZoom={0.15} maxZoom={1.5} nodesConnectable={false}>
            <Background color="var(--vscode-editorWidget-border, #454545)" gap={20} />
            <MiniMap pannable zoomable nodeColor={node => `var(--vscode-${node.data.kind === 'table' ? 'charts-blue' : 'charts-green'}, #568b6a)`} />
            <Controls />
          </ReactFlow>
          {!visible.nodes.length && <div className="canvas-empty">No nodes match this filter.</div>}
        </div>
      </div>
      <aside className="detail-panel">
        {selected ? <NodeDetails node={selected} scan={scan} onOpen={() => selected.file && api.postMessage({ command: 'openFile', file: selected.file })} /> : <>
          <h2>Inspection</h2><p className="subtle">Select a lineage node to view its metadata and connected dependencies.</p>
          <div className="detail-divider" /><h3>Unresolved dependencies</h3>
          {scan.unresolved.length ? <ul>{scan.unresolved.slice(0, 8).map(value => <li key={value}>{value}</li>)}</ul> : <p className="subtle">No unresolved static inputs reported.</p>}
        </>}
      </aside>
    </section>
    <section className="findings-panel">
      <div className="section-heading"><div><h2>Analysis findings</h2><p>Static findings are signals to inspect, not runtime failure claims.</p></div><span className="count-label">{findings.length}</span></div>
      {findings.length ? <div className="finding-list">{findings.map((finding, index) => <button className="finding" key={`${finding.file}:${finding.code}:${index}`} onClick={() => api.postMessage({ command: 'openFile', file: finding.file, line: finding.line })}>
        <span className={`severity severity-${finding.severity}`}>{finding.severity}</span><span className="finding-body"><strong>{finding.message}</strong><small>{finding.file}:{finding.line} · {finding.code}</small>{finding.suggestion && <small>{finding.suggestion}</small>}</span><span aria-hidden="true">›</span>
      </button>)}</div> : <div className="no-findings">No findings matched. This is not a guarantee of runtime correctness.</div>}
    </section>
    <footer>Local static analysis · parsed {scan.scan_stats?.parsed_files ?? '—'} changed file(s), reused {scan.scan_stats?.reused_files ?? '—'} · dependencies are labeled by inference/configuration confidence</footer>
  </main>;
}

function Metric({ label, value, tone }: { label: string; value: number; tone?: string }) {
  return <div className={`metric ${tone ?? ''}`}><span>{label}</span><strong>{value}</strong></div>;
}

function NodeDetails({ node, scan, onOpen }: { node: GraphNodeData; scan: WorkspaceScan; onOpen: () => void }) {
  const incoming = scan.edges.filter(edge => edge.target === node.id);
  const outgoing = scan.edges.filter(edge => edge.source === node.id);
  const relatedFindings = scan.findings.filter(finding => finding.file === node.file);
  return <>
    <span className="node-kind">{node.kind.replaceAll('_', ' ')}</span><h2 className="detail-title">{node.label}</h2>
    {node.file && <><p className="subtle file-path">{node.file}</p><button onClick={onOpen}>Open source</button></>}
    <div className="detail-divider" /><h3>Upstream dependencies</h3>
    {incoming.length ? <ul>{incoming.map((edge, i) => <li key={i}><code>{edge.source}</code><small>{edge.relationship} · {edge.confidence}</small></li>)}</ul> : <p className="subtle">No incoming dependency edges.</p>}
    <h3>Downstream dependencies</h3>
    {outgoing.length ? <ul>{outgoing.map((edge, i) => <li key={i}><code>{edge.target}</code><small>{edge.relationship} · {edge.confidence}</small></li>)}</ul> : <p className="subtle">No outgoing dependency edges.</p>}
    {relatedFindings.length > 0 && <><div className="detail-divider" /><h3>Findings ({relatedFindings.length})</h3>{relatedFindings.map((finding, index) => <p key={index}>{finding.message}<small>{finding.evidence}</small></p>)}</>}
  </>;
}

function ResultPanel({ value, onDismiss }: { value: DashboardResult; onDismiss: () => void }) {
  const data = value.result as Record<string, any>;
  const title = ({ profile: 'Dataset profile', validate: 'Data quality validation', metrics: 'Configured KPI observations', impact: 'Pipeline impact', investigate: 'Root-cause investigation', schema: 'Schema comparison', history: 'Analysis history' } as Record<string, string>)[value.kind] ?? 'FlowLens result';
  return <section className="result-panel">
    <div className="section-heading"><div><h2>{title}{typeof data.dataset === 'string' ? ` · ${data.dataset}` : ''}</h2><p>Result from a local FlowLens engine run.</p></div><button onClick={onDismiss} aria-label="Close result">Close</button></div>
    <div className="result-content">
      {value.kind === 'profile' && <>
        <div className="result-summary">{data.rows} rows · {data.columns} columns · {data.duplicate_rows} duplicate rows ({Number(data.duplicate_percent).toFixed(2)}%){data.sampled ? ` within first ${data.sample_limit} rows` : ''} · {data.statistics}{data.sampled ? ` (first ${data.sample_limit} rows for column statistics)` : ''}</div>
        <div className="result-table-wrap"><table><thead><tr><th>Column</th><th>Type</th><th>Missing</th><th>Distinct</th><th>Min</th><th>Max</th><th>Mean</th><th>Median</th><th>Q25–Q75</th><th>Outliers</th><th>Outlier %</th></tr></thead><tbody>{data.profiles?.map((column: any) => <tr key={column.name}><td>{column.name}</td><td>{column.data_type}</td><td>{column.null_count} ({Number(column.null_percent).toFixed(2)}%)</td><td>{column.distinct_count}</td><td>{column.minimum ?? '—'}</td><td>{column.maximum ?? '—'}</td><td>{column.mean ?? '—'}</td><td>{column.median ?? '—'}</td><td>{column.quantiles?.q25 ?? '—'}–{column.quantiles?.q75 ?? '—'}</td><td>{column.outlier_count ?? '—'}</td><td>{column.outlier_percent?.toFixed(2) ?? '—'}</td></tr>)}</tbody></table></div>
        {data.profiles?.some((column: any) => column.top_values?.length) && <div className="frequency-grid">{data.profiles.filter((column: any) => column.top_values?.length).map((column: any) => <div key={column.name}><h3>{column.name} · top values</h3>{column.top_values.map((item: any) => <div className="frequency-row" key={String(item.value)}><span>{String(item.value)}</span><span>{item.count}</span></div>)}</div>)}</div>}
        <h3>Anomaly signals</h3>{data.anomalies?.length ? <ul>{data.anomalies.map((item: any, index: number) => <li key={index}><strong>{item.metric}</strong> · {item.explanation}</li>)}</ul> : <p className="subtle">No profile deviation exceeded the available baseline threshold.</p>}
      </>}
      {value.kind === 'validate' && <>
        <div className={`result-summary ${data.passed ? 'pass' : 'fail'}`}>{data.passed ? 'Passed' : 'Failed'} · {data.total_checks} checks · {data.failed_checks} failed</div>
        {data.comparison?.has_baseline && <p className="subtle">Compared with {data.comparison.previous_at}: {data.comparison.delta_failed_checks > 0 ? '+' : ''}{data.comparison.delta_failed_checks} failed check(s) versus the previous run.</p>}
        <button onClick={() => api.postMessage({ command: 'exportValidation' })}>Export JSON report</button>
        {data.results?.map((item: any, index: number) => <details className="check-result" key={`${item.rule}-${index}`} open={!item.passed}><summary><span className={item.passed ? 'pass' : 'fail'}>{item.passed ? 'PASS' : 'FAIL'}</span> {item.rule}{item.column ? ` · ${item.column}` : ''} · {item.failed_rows} failing row(s)</summary><p>{item.message}</p>{item.samples?.length > 0 && <pre>{JSON.stringify(item.samples, null, 2)}</pre>}</details>)}
      </>}
      {value.kind === 'metrics' && <>
        <p>Read-only metric queries evaluated at {data.timestamp}. Deviations are statistical signals, not confirmed failures.</p>
        {data.values?.map((metric: any) => <div className="check-result" key={metric.name}><strong>{metric.name}</strong> · {metric.value}<p className="subtle">{metric.baseline_observations >= 5 ? `${metric.baseline_observations} baseline observations · median ${metric.baseline_median}` : `${metric.baseline_observations} baseline observations; at least 5 are needed for a robust signal.`}</p>{metric.anomaly && <p>{metric.anomaly.explanation}</p>}</div>)}
      </>}
      {value.kind === 'impact' && <><p>Potential blast radius from static/configured dependencies; this is not runtime execution proof.</p><h3>Downstream nodes</h3><ul>{data.impacted_nodes?.map((node: any) => <li key={node.id}><strong>{node.kind}</strong> · {node.label}</li>)}</ul><h3>Dependency paths</h3><ul>{data.paths?.map((nodes: string[], index: number) => <li key={index}><code>{nodes.join(' → ')}</code></li>)}</ul>{data.circular_dependencies?.length > 0 && <><h3>Cycles detected</h3><ul>{data.circular_dependencies.map((cycle: string[], index: number) => <li key={index}><code>{cycle.join(' → ')}</code></li>)}</ul></>}</>}
      {value.kind === 'investigate' && <><p>{data.statement}</p>{data.hypotheses?.map((hypothesis: any) => <details className="check-result" key={hypothesis.rank} open><summary>#{hypothesis.rank} · {hypothesis.description} <span className="subtle">{hypothesis.confidence}</span></summary><p>{hypothesis.rationale}</p><h3>Evidence</h3><ul>{hypothesis.evidence.map((entry: string, index: number) => <li key={index}>{entry}</li>)}</ul><h3>Suggested investigation</h3><ul>{hypothesis.next_steps.map((entry: string, index: number) => <li key={index}>{entry}</li>)}</ul></details>)}<p className="subtle">{data.evidence_scope}</p></>}
      {value.kind === 'schema' && <><p>{data.has_baseline ? `Compared with ${data.previous_at}.` : 'First snapshot saved; no previous baseline is available.'}</p>{data.changes?.length ? <ul>{data.changes.map((change: any, index: number) => <li key={index}><strong>{change.kind}</strong> · {change.column}{change.before ? ` · ${change.before}` : ''}{change.after ? ` → ${change.after}` : ''}</li>)}</ul> : <p className="subtle">No schema differences detected.</p>}<p className="subtle">Nullability changes refer to observed values, not declared constraints.</p></>}
      {value.kind === 'history' && <div className="result-table-wrap"><table><thead><tr><th>Time</th><th>Event</th><th>Workspace-local summary</th></tr></thead><tbody>{(data.events as any[] ?? []).map(event => <tr key={event.id}><td>{event.created_at}</td><td>{event.kind}</td><td>{JSON.stringify(event.payload)}</td></tr>)}</tbody></table>{!(data.events as any[] ?? []).length && <p className="subtle">No local history yet.</p>}</div>}
    </div>
  </section>;
}

function safeKind(kind: string): string { return kind.toLowerCase().replace(/[^a-z0-9_-]/g, '-'); }
