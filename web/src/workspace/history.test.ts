// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { movementAnalysis } from '../../tests/fixtures/analysis';
import { ConversationHistory, conversationStorageKey } from './history';

beforeEach(() => localStorage.clear());
afterEach(() => vi.restoreAllMocks());

describe('saved conversations', () => {
  it('restores complete messages, citations, chart results, and the active conversation', () => {
    const history = new ConversationHistory();
    const question = history.append('user', 'Chart movement');
    const citation = {
      source: { name: 'Tracking', url: 'https://example.org/tracking' },
      rights: null,
    };
    history.complete(question.id, {
      answer: 'The chart is ready.',
      updated: false,
      citations: [citation],
      analyses: [movementAnalysis],
    });

    const restored = new ConversationHistory();

    expect(restored.current?.title).toBe('Chart movement');
    expect(restored.current?.messages[1].details?.citations).toEqual([citation]);
    expect(restored.current?.messages[1].details?.analyses).toEqual([movementAnalysis]);
    expect(restored.contextMessages).toEqual([
      { role: 'user', content: 'Chart movement' },
      { role: 'assistant', content: 'The chart is ready.' },
    ]);
  });

  it('retains old threads and isolates context when a new conversation starts', () => {
    const history = new ConversationHistory();
    const first = history.append('user', 'First question');
    history.complete(first.id, { answer: 'First answer', updated: false });
    const firstConversationId = history.current!.id;
    history.start();
    expect(history.contextMessages).toEqual([]);
    const second = history.append('user', 'Second question');
    history.complete(second.id, { answer: 'Second answer', updated: false });

    const restored = new ConversationHistory();
    expect(restored.conversations).toHaveLength(2);
    expect(restored.contextMessages.map((message) => message.content)).toEqual([
      'Second question',
      'Second answer',
    ]);
    restored.open(firstConversationId);
    expect(restored.contextMessages.map((message) => message.content)).toEqual([
      'First question',
      'First answer',
    ]);
    expect(new ConversationHistory().current?.id).toBe(firstConversationId);
  });

  it('logs interrupted requests and local notices without sending them as completed model context', () => {
    const history = new ConversationHistory();
    history.append('user', 'Interrupted question');
    history.append('assistant', 'The request failed.');
    history.append('assistant', 'Imported a local file.');

    const restored = new ConversationHistory();

    expect(restored.current?.messages).toHaveLength(3);
    expect(restored.contextMessages).toEqual([]);
  });

  it('remains usable with malformed history or unavailable browser storage', () => {
    localStorage.setItem(conversationStorageKey, '{invalid');
    const malformed = new ConversationHistory();
    expect(malformed.conversations).toEqual([]);
    malformed.append('user', 'Recover the conversation');
    expect(new ConversationHistory().current?.title).toBe('Recover the conversation');
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('Storage full', 'QuotaExceededError');
    });
    const history = new ConversationHistory();

    history.append('assistant', 'Still available in this session.');

    expect(history.saved).toBe(false);
    expect(history.current?.messages.at(-1)?.content).toBe('Still available in this session.');
  });
});
