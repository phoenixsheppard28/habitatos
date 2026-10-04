import type { Chart, WorkspaceAnalysis } from '../../src/workspace/types';
import charts from './charts.json';

export const movementAnalysis: WorkspaceAnalysis = {
  analysis_id: 'movement-analysis',
  prepared_id: 'a'.repeat(32),
  status: 'ok',
  warnings: [],
  charts: [charts[0] as Chart],
  result: {
    result_id: 'movement-result',
    question: 'Chart daily movement for the tracked animals',
    created_at: '2026-10-04T00:00:00Z',
    status: 'complete',
    findings: ['Median daily displacement was 7 km.'],
    metrics: {},
    evidence: { datasets: [{ dataset_id: 'tracking', version: 3 }] },
    limitations: ['Tracked animals do not represent the entire population.'],
    artifact_versions: { method: 'movement_summary@1' },
  },
};
