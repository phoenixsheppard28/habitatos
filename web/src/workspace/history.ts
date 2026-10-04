import type { ChatMessage, ChatResponse } from './types';

export const conversationStorageKey = 'dora.conversations.v1';

export type MessageDetails = Pick<
  ChatResponse,
  'citations' | 'timings' | 'elapsed_seconds' | 'analyses'
>;

export interface ConversationMessage extends ChatMessage {
  id: string;
  created_at: string;
  context: boolean;
  details?: MessageDetails;
}

export interface Conversation {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  messages: ConversationMessage[];
}

interface ConversationArchive {
  version: 1;
  active_id: string | null;
  conversations: Conversation[];
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function isMessage(value: unknown): value is ConversationMessage {
  return (
    isRecord(value) &&
    typeof value.id === 'string' &&
    (value.role === 'user' || value.role === 'assistant') &&
    typeof value.content === 'string' &&
    typeof value.created_at === 'string' &&
    Number.isFinite(Date.parse(value.created_at)) &&
    typeof value.context === 'boolean' &&
    (value.details === undefined || isRecord(value.details))
  );
}

function isConversation(value: unknown): value is Conversation {
  return (
    isRecord(value) &&
    typeof value.id === 'string' &&
    typeof value.title === 'string' &&
    typeof value.created_at === 'string' &&
    Number.isFinite(Date.parse(value.created_at)) &&
    typeof value.updated_at === 'string' &&
    Number.isFinite(Date.parse(value.updated_at)) &&
    Array.isArray(value.messages) &&
    value.messages.every(isMessage)
  );
}

export class ConversationHistory {
  private archive: ConversationArchive = { version: 1, active_id: null, conversations: [] };
  saved = true;

  constructor() {
    try {
      const stored = localStorage.getItem(conversationStorageKey);
      if (!stored) return;

      const archive: unknown = JSON.parse(stored);
      if (!isRecord(archive) || archive.version !== 1 || !Array.isArray(archive.conversations))
        return;

      this.archive.conversations = archive.conversations.filter(isConversation);
      this.archive.active_id =
        typeof archive.active_id === 'string' &&
        this.archive.conversations.some((conversation) => conversation.id === archive.active_id)
          ? archive.active_id
          : null;
    } catch {
      this.saved = false;
    }
  }

  get conversations(): Conversation[] {
    return this.archive.conversations
      .slice()
      .sort((first, second) => second.updated_at.localeCompare(first.updated_at));
  }

  get current(): Conversation | undefined {
    return this.archive.conversations.find(
      (conversation) => conversation.id === this.archive.active_id,
    );
  }

  get contextMessages(): ChatMessage[] {
    return (
      this.current?.messages
        .filter((message) => message.context)
        .map(({ role, content }) => ({ role, content })) ?? []
    );
  }

  start(): void {
    this.archive.active_id = null;
    this.save();
  }

  open(id: string): void {
    if (!this.archive.conversations.some((conversation) => conversation.id === id)) return;

    this.archive.active_id = id;
    this.save();
  }

  append(role: ChatMessage['role'], content: string): ConversationMessage {
    const createdAt = new Date().toISOString();
    let conversation = this.current;
    if (!conversation) {
      conversation = {
        id: crypto.randomUUID(),
        title: 'Workspace notes',
        created_at: createdAt,
        updated_at: createdAt,
        messages: [],
      };
      this.archive.conversations.push(conversation);
      this.archive.active_id = conversation.id;
    }
    if (role === 'user' && !conversation.messages.some((message) => message.role === 'user')) {
      conversation.title = content.replace(/\s+/g, ' ').trim().slice(0, 80);
    }

    const message: ConversationMessage = {
      id: crypto.randomUUID(),
      role,
      content,
      created_at: createdAt,
      context: false,
    };
    conversation.messages.push(message);
    conversation.updated_at = createdAt;
    this.save();

    return message;
  }

  complete(userMessageId: string, response: ChatResponse): ConversationMessage {
    const question = this.current?.messages.find((message) => message.id === userMessageId);
    if (question) question.context = true;

    const message = this.append('assistant', response.answer);
    message.context = true;
    message.details = {
      citations: response.citations,
      timings: response.timings,
      elapsed_seconds: response.elapsed_seconds,
      analyses: response.analyses,
    };
    this.save();

    return message;
  }

  private save(): void {
    try {
      localStorage.setItem(conversationStorageKey, JSON.stringify(this.archive));
      this.saved = true;
    } catch {
      this.saved = false;
    }
  }
}
