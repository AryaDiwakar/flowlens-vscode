export type Finding = {
  code: string;
  severity: 'error' | 'warning' | 'info' | string;
  message: string;
  file: string;
  line: number;
  evidence: string;
  suggestion?: string;
};

export type Asset = { path: string; kind: string; size_bytes: number };
export type GraphNodeData = { id: string; label: string; kind: string; file?: string };
export type GraphEdgeData = { source: string; target: string; relationship: string; confidence: string };

export type WorkspaceScan = {
  root: string;
  assets: Asset[];
  nodes: GraphNodeData[];
  edges: GraphEdgeData[];
  findings: Finding[];
  unresolved: string[];
  scan_stats?: { discovered_files: number; parsed_files: number; reused_files: number; removed_files: number };
};

export type DashboardResult = { kind: string; result: Record<string, unknown> };

export interface FlowLensApi {
  postMessage(message: { command: string; [key: string]: unknown }): void;
}

declare global {
  interface Window { acquireVsCodeApi?: () => FlowLensApi }
}
