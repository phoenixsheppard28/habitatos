import { askAssistant } from './api';
import { button, element, formatMonth, node, sourceLink } from './dom';
import { ConversationHistory, type ConversationMessage, type MessageDetails } from './history';
import { renderMarkdown } from './markdown';
import type { WorkspacePanels } from './panels';
import type { WorkspaceStore } from './store';
import type { ChatMessage, PipelineProgress } from './types';

export class WorkspaceAssistant {
  private history = new ConversationHistory();
  private busy = false;
  private field = element<HTMLTextAreaElement>('question');
  private form = element<HTMLFormElement>('ask');
  private thread = element('thread');

  constructor(
    private store: WorkspaceStore,
    private panels: WorkspacePanels,
  ) {
    element('chat-history-toggle').addEventListener('click', () => {
      const panel = element('chat-history');
      panel.hidden = !panel.hidden;
      element('chat-history-toggle').setAttribute('aria-expanded', String(!panel.hidden));
      this.renderHistory();
    });
    element('chat-new').addEventListener('click', () => {
      if (this.busy) return;

      this.history.start();
      this.field.value = '';
      this.renderConversation();
      this.hideHistory();
      this.field.focus();
    });
    this.store.addAnalyses(
      this.history.conversations
        .slice()
        .reverse()
        .flatMap((conversation) =>
          conversation.messages.flatMap((message) => message.details?.analyses ?? []),
        ),
      false,
    );
    this.renderConversation();

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
          : 'Set OPENAI_API_KEY on the backend to use Dora.';
      }
    });
  }

  async ask(question: string): Promise<void> {
    this.panels.showChat();
    if (this.busy) return;

    this.busy = true;
    element<HTMLButtonElement>('send-question').disabled = true;
    const userMessage = this.history.append('user', question);
    this.appendSavedMessage(userMessage);
    this.renderHistory();
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
      ...this.history.contextMessages.slice(-16),
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
      this.appendSavedMessage(this.history.complete(userMessage.id, response));
      if (response.updated) await this.store.refresh(response.retrieved_dataset_ids?.[0]);
      if (response.analyses?.length) {
        this.store.addAnalyses(response.analyses);
      }
    } catch (error) {
      pending.remove();
      this.appendSavedMessage(
        this.history.append(
          'assistant',
          error instanceof Error ? error.message : 'The Dora request failed.',
        ),
      );
      this.field.value = question;
    } finally {
      window.clearInterval(timer);
      this.busy = false;
      this.renderHistory();
      element<HTMLButtonElement>('send-question').disabled = false;
      element('assistant-status').textContent = 'Local imports remain on this device.';
      this.field.focus();
    }
  }

  notify(message: string): void {
    this.panels.showChat();
    this.appendSavedMessage(this.history.append('assistant', message));
    this.renderHistory();
  }

  private hideHistory(): void {
    element('chat-history').hidden = true;
    element('chat-history-toggle').setAttribute('aria-expanded', 'false');
  }

  private renderHistory(): void {
    const conversations = this.history.conversations;
    element('chat-history-count').textContent = String(conversations.length);
    element('chat-conversation-title').textContent =
      this.history.current?.title ?? 'New conversation';
    element('chat-storage-status').textContent = this.history.saved
      ? 'Saved on this device'
      : 'History is not saved';
    element<HTMLButtonElement>('chat-new').disabled = this.busy;
    const list = element('chat-history-list');
    list.replaceChildren();
    if (!conversations.length)
      list.append(node('p', 'No previous conversations yet.', 'content-note'));

    for (const conversation of conversations) {
      const control = button(
        '',
        () => {
          if (this.busy) return;

          this.history.open(conversation.id);
          this.field.value = '';
          this.renderConversation();
          this.hideHistory();
          this.field.focus();
        },
        'conversation-item',
      );
      control.dataset.conversationId = conversation.id;
      control.disabled = this.busy;
      control.title = conversation.title;
      if (conversation.id === this.history.current?.id)
        control.setAttribute('aria-current', 'true');
      const time = node('time', new Date(conversation.updated_at).toLocaleString());
      time.dateTime = conversation.updated_at;
      control.append(
        node('strong', conversation.title),
        time,
        node('small', `${conversation.messages.length} messages`),
      );
      list.append(control);
    }
  }

  private renderConversation(): void {
    this.thread.replaceChildren();
    const messages = this.history.current?.messages ?? [];
    if (!messages.length) {
      this.thread.append(
        node(
          'p',
          'Ask about dataset coverage, analyze observations, or request data for a region and date range.',
          'assistant-intro',
        ),
      );
    }
    for (const message of messages) this.appendSavedMessage(message);
    this.renderHistory();
  }

  private appendSavedMessage(message: ConversationMessage): HTMLElement {
    return this.appendMessage(message.role, message.content, message.details, message.created_at);
  }

  private appendMessage(
    role: ChatMessage['role'],
    text: string,
    details: MessageDetails = {},
    createdAt?: string,
  ): HTMLElement {
    this.thread.querySelector('.assistant-intro')?.remove();
    const article = node('article', undefined, 'exchange');
    const heading = node('div', undefined, 'message-heading');
    heading.append(
      node('div', role === 'user' ? 'YOU' : 'Dora', role === 'user' ? 'label' : 'answer-brand'),
    );
    if (createdAt) {
      const time = node(
        'time',
        new Date(createdAt).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' }),
        'message-time',
      );
      time.dateTime = createdAt;
      time.title = new Date(createdAt).toLocaleString();
      heading.append(time);
    }
    article.append(heading);
    article.append(role === 'user' ? node('p', text, 'question') : renderMarkdown(text));
    const urls = new Set<string>();
    for (const citation of details.citations ?? []) {
      const url = citation.source?.url;
      if (!url || urls.has(url)) continue;

      urls.add(url);
      const paragraph = node('p', undefined, 'source-citation');
      paragraph.append(sourceLink(citation.source?.name ?? 'Source', url));
      article.append(paragraph);
    }
    if (details.timings?.length) {
      const timingsDetails = node('details', undefined, 'pipeline-timings');
      timingsDetails.append(
        node('summary', `Pipeline timings · ${details.elapsed_seconds?.toFixed(1) ?? '?'}s`),
      );
      const timings = node('ol');
      for (const timing of details.timings)
        timings.append(
          node(
            'li',
            `${timing.message} · ${timing.duration_seconds?.toFixed(1) ?? '?'}s${timing.status === 'error' ? ' · failed' : ''}`,
          ),
        );
      timingsDetails.append(timings);
      article.append(timingsDetails);
    }
    if (details.analyses?.length) {
      article.append(
        button('View analysis in Charts', () => this.store.addAnalyses(details.analyses!)),
      );
    }
    this.thread.append(article);
    this.thread.scrollTop = this.thread.scrollHeight;

    return article;
  }
}
