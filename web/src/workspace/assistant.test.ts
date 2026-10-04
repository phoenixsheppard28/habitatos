// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import workspaceHTML from '../../index.html?raw';
import { movementAnalysis } from '../../tests/fixtures/analysis';
import { WorkspaceAssistant } from './assistant';
import type { WorkspacePanels } from './panels';
import { WorkspaceStore } from './store';
import { WorkspaceViews } from './views';

beforeEach(() => {
  localStorage.clear();
  document.body.innerHTML = new DOMParser().parseFromString(
    workspaceHTML,
    'text/html',
  ).body.innerHTML;
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

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

describe('conversation history controls', () => {
  it('restores the thread, source links, chart actions, and request context after a page reload', async () => {
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(
        Response.json({
          answer: '**Movement** is ready.',
          updated: false,
          analyses: [movementAnalysis],
          citations: [
            { source: { name: 'Source', url: 'https://example.org/data' }, rights: null },
          ],
        }),
      )
      .mockResolvedValueOnce(Response.json({ answer: 'Follow-up answer.', updated: false }));
    vi.stubGlobal('fetch', fetch);
    const first = workspace();
    await first.assistant.ask('Chart movement');
    document.body.innerHTML = new DOMParser().parseFromString(
      workspaceHTML,
      'text/html',
    ).body.innerHTML;

    const restored = workspace();

    expect(document.querySelectorAll('#thread .exchange')).toHaveLength(2);
    expect(document.querySelector('#thread strong')?.textContent).toBe('Movement');
    expect(document.querySelector<HTMLAnchorElement>('#thread .source-citation a')?.href).toBe(
      'https://example.org/data',
    );
    expect(restored.store.state.analyses).toEqual([movementAnalysis]);
    expect(restored.store.state.view).toBe('map');
    const chartButton = [...document.querySelectorAll<HTMLButtonElement>('#thread button')].find(
      (button) => button.textContent === 'View analysis in Charts',
    )!;
    chartButton.click();
    expect(document.querySelector('.analysis-chart')).not.toBeNull();
    await restored.assistant.ask('What does that mean?');
    expect(JSON.parse(fetch.mock.calls[1][1].body).messages).toEqual([
      { role: 'user', content: 'Chart movement' },
      { role: 'assistant', content: '**Movement** is ready.' },
      { role: 'user', content: 'What does that mean?' },
    ]);
  });

  it('starts a clean conversation and reopens an older conversation with its own context', async () => {
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(Response.json({ answer: 'First answer', updated: false }))
      .mockResolvedValueOnce(Response.json({ answer: 'Second answer', updated: false }))
      .mockResolvedValueOnce(Response.json({ answer: 'First follow-up', updated: false }));
    vi.stubGlobal('fetch', fetch);
    const { assistant } = workspace();
    await assistant.ask('First question');
    document.getElementById('chat-new')!.click();
    expect(document.querySelectorAll('#thread .exchange')).toHaveLength(0);
    await assistant.ask('Second question');
    expect(JSON.parse(fetch.mock.calls[1][1].body).messages).toEqual([
      { role: 'user', content: 'Second question' },
    ]);
    document.getElementById('chat-history-toggle')!.click();
    const firstConversation = [
      ...document.querySelectorAll<HTMLButtonElement>('.conversation-item'),
    ].find((button) => button.title === 'First question')!;
    expect(document.querySelectorAll('.conversation-item')).toHaveLength(2);

    firstConversation.click();

    expect(document.getElementById('chat-history')?.hidden).toBe(true);
    expect(document.getElementById('thread')?.textContent).toContain('First answer');
    expect(document.getElementById('thread')?.textContent).not.toContain('Second answer');
    await assistant.ask('Continue the first question');
    expect(JSON.parse(fetch.mock.calls[2][1].body).messages).toEqual([
      { role: 'user', content: 'First question' },
      { role: 'assistant', content: 'First answer' },
      { role: 'user', content: 'Continue the first question' },
    ]);
  });

  it('blocks conversation switching while a response is pending', async () => {
    let resolve!: (response: Response) => void;
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(
        () =>
          new Promise((finish) => {
            resolve = finish;
          }),
      ),
    );
    const { assistant } = workspace();
    const pending = assistant.ask('Wait for the analysis');

    document.getElementById('chat-new')!.click();
    expect(document.querySelector<HTMLButtonElement>('#chat-new')?.disabled).toBe(true);
    expect(document.getElementById('thread')?.textContent).toContain('Wait for the analysis');
    expect(document.querySelector<HTMLButtonElement>('.conversation-item')?.disabled).toBe(true);
    resolve(Response.json({ answer: 'Finished.', updated: false }));
    await pending;
    expect(document.querySelector<HTMLButtonElement>('#chat-new')?.disabled).toBe(false);
  });
});
