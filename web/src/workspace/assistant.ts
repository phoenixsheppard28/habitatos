import { askAssistant } from './api';
import { element, formatMonth, node, sourceLink } from './dom';
import { renderMarkdown } from './markdown';
import type { WorkspacePanels } from './panels';
import type { WorkspaceStore } from './store';
import type { ChatMessage, PipelineProgress, SourceCitation } from './types';

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
          ? 'Dora is connected · local imports remain on this device.'
          : 'Set ANTHROPIC_API_KEY on the backend to use Dora.';
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
    pending.setAttribute('role', 'status');
    const progressText = pending.querySelector('p') ?? pending.appendChild(node('p'));
    const progressList = node('ol', undefined, 'pipeline-progress');
    pending.append(progressList);
    const progressRows = new Map<string, HTMLElement>();
    const started = performance.now();
    let currentStage = 'Read the workspace catalog';
    const updateElapsed = () => {
      const seconds = Math.floor((performance.now() - started) / 1000);
      progressText.textContent = `${currentStage} · ${seconds}s elapsed`;
      element('assistant-status').textContent = progressText.textContent;
    };
    const onProgress = (event: PipelineProgress) => {
      currentStage = event.message;
      let row = progressRows.get(event.id);
      if (!row) {
        row = node('li');
        progressRows.set(event.id, row);
        progressList.append(row);
      }
      row.dataset.status = event.status;
      const duration =
        event.duration_seconds === undefined ? '' : ` · ${event.duration_seconds.toFixed(1)}s`;
      const status =
        event.status === 'error' ? 'Failed: ' : event.status === 'complete' ? 'Done: ' : '';
      row.textContent = `${status}${event.message}${duration}`;
      updateElapsed();
      this.thread.scrollTop = this.thread.scrollHeight;
    };
    updateElapsed();
    const timer = window.setInterval(updateElapsed, 1000);
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
        onProgress,
      );
      pending.remove();
      const article = this.appendMessage('assistant', response.answer, response.citations);
      if (response.timings?.length) {
        const details = node('details', undefined, 'pipeline-timings');
        details.append(
          node('summary', `Pipeline timings · ${response.elapsed_seconds?.toFixed(1) ?? '?'}s`),
        );
        const timings = node('ol');
        for (const timing of response.timings) {
          timings.append(
            node(
              'li',
              `${timing.message} · ${timing.duration_seconds?.toFixed(1) ?? '?'}s${timing.status === 'error' ? ' · failed' : ''}`,
            ),
          );
        }
        details.append(timings);
        article.append(details);
      }
      this.messages = [...messages, { role: 'assistant', content: response.answer }];
      if (response.updated) await this.store.refresh(response.retrieved_dataset_ids?.[0]);
    } catch (error) {
      pending.remove();
      this.appendMessage(
        'assistant',
        error instanceof Error ? error.message : 'The Dora request failed.',
      );
      this.field.value = question;
    } finally {
      window.clearInterval(timer);
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
      node('div', role === 'user' ? 'YOU' : 'Dora', role === 'user' ? 'label' : 'answer-brand'),
    );
    article.append(role === 'user' ? node('p', text, 'question') : renderMarkdown(text));
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
