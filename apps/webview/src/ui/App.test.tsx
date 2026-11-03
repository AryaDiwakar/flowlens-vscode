import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

afterEach(() => cleanup());

describe('FlowLens dashboard', () => {
  it('shows a truthful empty state and an analyze action before scan results exist', async () => {
    const postMessage = vi.fn();
    window.acquireVsCodeApi = () => ({ postMessage });
    const { App } = await import('./App');
    render(<App />);
    expect(screen.getByRole('heading', { name: 'Workspace intelligence' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Analyze workspace' }));
    expect(postMessage).toHaveBeenCalledWith({ command: 'analyze' });
  });

  it('renders a validation result received from the extension bridge', async () => {
    vi.resetModules();
    const postMessage = vi.fn();
    window.acquireVsCodeApi = () => ({ postMessage });
    const { App } = await import('./App');
    render(<App />);
    await waitFor(() => expect(postMessage).toHaveBeenCalledWith({ command: 'ready' }));
    window.dispatchEvent(new MessageEvent('message', { data: {
      type: 'result', kind: 'validate', result: { passed: false, total_checks: 1, failed_checks: 1, results: [{ rule: 'not_null', column: 'id', passed: false, failed_rows: 2, message: 'Null ids detected', checked_rows: 10, samples: [] }] }
    } }));
    expect(await screen.findByText(/Data quality validation/)).toBeInTheDocument();
    expect(screen.getByText('Null ids detected')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Export JSON report' }));
    expect(postMessage).toHaveBeenCalledWith({ command: 'exportValidation' });
  });

  it('renders configured KPI values and their baseline evidence', async () => {
    vi.resetModules();
    const postMessage = vi.fn();
    window.acquireVsCodeApi = () => ({ postMessage });
    const { App } = await import('./App');
    render(<App />);
    await waitFor(() => expect(postMessage).toHaveBeenCalledWith({ command: 'ready' }));
    window.dispatchEvent(new MessageEvent('message', { data: {
      type: 'result', kind: 'metrics', result: { dataset: 'sales.csv', timestamp: 'now', values: [{ name: 'revenue', value: 900, baseline_observations: 5, baseline_median: 100, anomaly: { explanation: 'KPI is outside baseline' } }] }
    } }));
    expect(await screen.findByText(/Configured KPI observations/)).toBeInTheDocument();
    expect(screen.getByText('KPI is outside baseline')).toBeInTheDocument();
  });
});
