// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import workspaceHTML from '../../index.html?raw';
import { movementAnalysis } from '../../tests/fixtures/analysis';
import { WorkspaceAssistant } from './assistant';
import type { WorkspacePanels } from './panels';
import { WorkspaceStore } from './store';
import { WorkspaceViews } from './views';

beforeEach(() => {
  document.body.innerHTML = new DOMParser().parseFromString(
    workspaceHTML,
    'text/html',
  ).body.innerHTML;
});

afterEach(() => vi.unstubAllGlobals());

function workspace() {
  const store = new WorkspaceStore();
  const panels = { showChat: vi.fn() } as unknown as WorkspacePanels;
  const assistant = new WorkspaceAssistant(store, panels);
  new WorkspaceViews(store, (question) => void assistant.ask(question));

  return { store, assistant };
}

describe('assistant analysis workflow', () => {
  it('opens Charts from a streamed analysis and retains results after navigation and refresh', async () => {
    const response = {
      answer: 'Movement analysis is ready.',
      updated: false,
      analyses: [movementAnalysis],
    };
    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue(
          new TextEncoder().encode(JSON.stringify({ type: 'result', response }) + '\n'),
        );
        controller.close();
      },
    });
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValueOnce(
          new Response(stream, { headers: { 'Content-Type': 'application/x-ndjson' } }),
        )
        .mockResolvedValueOnce(Response.json({ datasets: [], assistant_available: true })),
    );
    const { store, assistant } = workspace();

    await assistant.ask('Analyze movement and show a chart');

    expect(store.state.view).toBe('chart');
    expect(document.getElementById('object-content')?.hidden).toBe(false);
    expect(document.getElementById('workspace-notice')?.hidden).toBe(true);
    expect(document.querySelector('[data-analysis-id] h2')?.textContent).toBe(
      movementAnalysis.result.question,
    );
    expect(document.querySelector('.analysis-chart')).not.toBeNull();
    store.setView('map');
    const link = [...document.querySelectorAll<HTMLButtonElement>('#thread button')].find(
      (button) => button.textContent === 'View analysis in Charts',
    )!;
    link.click();
    expect(store.state.view).toBe('chart');
    await store.refresh();
    expect(document.querySelector('.analysis-chart')).not.toBeNull();
    expect(store.state.analyses).toHaveLength(1);
  });

  it('keeps previous charts when a later request returns no analysis', async () => {
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValue(
          Response.json({ answer: 'No rainfall evidence.', updated: false, analyses: [] }),
        ),
    );
    const { store, assistant } = workspace();
    store.addAnalyses([movementAnalysis]);
    store.setView('table');

    await assistant.ask('Analyze rainfall');

    expect(store.state.view).toBe('table');
    expect(store.state.analyses).toEqual([movementAnalysis]);
  });

  it('shows all analyses with the latest first and preserves their requested dates', () => {
    const { store } = workspace();
    const second = {
      ...movementAnalysis,
      analysis_id: 'second-analysis',
      result: { ...movementAnalysis.result, question: 'Compare date windows' },
    };
    store.addAnalyses([movementAnalysis, second]);
    store.setMonth(0);

    expect(
      [...document.querySelectorAll('.analysis-result h2')].map((heading) => heading.textContent),
    ).toEqual([second.result.question, movementAnalysis.result.question]);
    expect(document.querySelector('.analysis-result tbody')?.textContent).toContain('2024-01-07');
    store.addAnalyses([second]);
    expect(store.state.analyses).toHaveLength(2);
  });
});
