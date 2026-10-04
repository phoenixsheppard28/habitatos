import { askAssistant } from './api';
import { element, formatMonth, node, sourceLink } from './dom';
import type { WorkspacePanels } from './panels';
import type { WorkspaceStore } from './store';
import type { ChatMessage, SourceCitation } from './types';

export class WorkspaceAssistant {
  private messages: ChatMessage[] = [];
  private busy = false;
  private field = element<HTMLTextAreaElement>('question');
  private form = element<HTMLFormElement>('ask');
  private thread = element('thread');

  constructor(
    private store: WorkspaceStore,
    private panels: WorkspacePanels,
  ) {
    this.form.addEventListener('submit', (event) => {
      event.preventDefault();
      const question = this.field.value.trim();
      if (!question || this.busy) return;

      this.field.value = '';
      void this.ask(question);
    });
    this.field.addEventListener('keydown', (event) => {
      if (event.key !== 'Enter' || event.shiftKey || event.isComposing) return;

      event.preventDefault();
      this.form.requestSubmit();
    });
    store.subscribe((state) => {
      element('chat-context').textContent = state.selected
        ? `${state.selected.source_id} · ${formatMonth(store.through)}`
        : 'Public catalog';
      if (!this.busy) {
        element('assistant-status').textContent = state.assistantAvailable
          ? 'Connected assistant · local imports remain on this device.'
          : 'Set ANTHROPIC_API_KEY on the backend to use the assistant.';
      }
    });
  }

  async ask(question: string): Promise<void> {
    this.panels.showChat();
    if (this.busy) return;

    this.busy = true;
    element<HTMLButtonElement>('send-question').disabled = true;
    this.appendMessage('user', question);
    const pending = this.appendMessage(
      'assistant',
      'Reading the catalog and processing your question…',
    );
    pending.classList.add('pending-response');
    element('assistant-status').textContent = 'The backend may retrieve data or execute analysis.';
    const messages: ChatMessage[] = [
      ...this.messages.slice(-16),
      { role: 'user', content: question },
    ];

    try {
      const response = await askAssistant(
        messages,
        this.store.state.selected?.dataset_id,
        this.store.through,
      );
      pending.remove();
      this.appendMessage('assistant', response.answer, response.citations);
      this.messages = [...messages, { role: 'assistant', content: response.answer }];
      if (response.updated) await this.store.refresh();
    } catch (error) {
      pending.remove();
      this.appendMessage(
        'assistant',
        error instanceof Error ? error.message : 'The assistant request failed.',
      );
      this.field.value = question;
    } finally {
      this.busy = false;
      element<HTMLButtonElement>('send-question').disabled = false;
      element('assistant-status').textContent = 'Local imports remain on this device.';
      this.field.focus();
    }
  }

  notify(message: string): void {
    this.panels.showChat();
    this.appendMessage('assistant', message);
  }

  private appendMessage(
    role: ChatMessage['role'],
    text: string,
    citations: SourceCitation[] = [],
  ): HTMLElement {
    this.thread.querySelector('.assistant-intro')?.remove();
    const article = node('article', undefined, 'exchange');
    article.append(
      node(
        'div',
        role === 'user' ? 'YOU' : 'Habitat Watch',
        role === 'user' ? 'label' : 'answer-brand',
      ),
    );
    article.append(node('p', text, role === 'user' ? 'question' : 'finding'));
    const urls = new Set<string>();
    for (const citation of citations) {
      const url = citation.source?.url;
      if (!url || urls.has(url)) continue;

      urls.add(url);
      const paragraph = node('p', undefined, 'source-citation');
      paragraph.append(sourceLink(citation.source?.name ?? 'Source', url));
      article.append(paragraph);
    }
    this.thread.append(article);
    this.thread.scrollTop = this.thread.scrollHeight;

    return article;
  }
}
