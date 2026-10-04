import type {
  CatalogResponse,
  ChatMessage,
  ChatResponse,
  DatasetSnapshot,
  PipelineProgress,
} from './types';

async function fetchResponse(path: string, options: RequestInit): Promise<Response> {
  try {
    return await fetch(path, options);
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error;

    throw new Error('Cannot reach the backend. Start the Dora API and retry.');
  }
}

async function readJSON<T>(response: Response): Promise<T> {
  if (!response.headers.get('content-type')?.includes('application/json')) {
    throw new Error('The API returned no data. Check the backend and API proxy.');
  }

  const body: unknown = await response.json();
  if (!response.ok) {
    const message =
      body && typeof body === 'object' && 'error' in body
        ? String(body.error)
        : 'The request failed.';
    throw new Error(message);
  }

  return body as T;
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  return readJSON<T>(await fetchResponse(path, options));
}

export function fetchCatalog(signal?: AbortSignal): Promise<CatalogResponse> {
  return request('/api/catalog', { signal });
}

export function fetchDataset(datasetId: string, signal?: AbortSignal): Promise<DatasetSnapshot> {
  const parameters = new URLSearchParams({ dataset_id: datasetId });

  return request(`/api/features?${parameters}`, { signal });
}

export async function askAssistant(
  messages: ChatMessage[],
  datasetId: string | undefined,
  through: string | undefined,
  onProgress?: (event: PipelineProgress) => void,
): Promise<ChatResponse> {
  const response = await fetchResponse('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/x-ndjson' },
    body: JSON.stringify({ messages, dataset_id: datasetId, through }),
  });
  if (!response.ok || !response.headers.get('content-type')?.includes('application/x-ndjson')) {
    return readJSON<ChatResponse>(response);
  }
  if (!response.body) throw new Error('The assistant returned no response stream.');

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let result: ChatResponse | undefined;
  const consume = (line: string) => {
    if (!line.trim()) return;

    const event = JSON.parse(line) as
      | ({ type: 'progress' } & PipelineProgress)
      | { type: 'result'; response: ChatResponse }
      | { type: 'error'; error: string };
    if (event.type === 'progress') onProgress?.(event);
    else if (event.type === 'result') result = event.response;
    else if (event.type === 'error') throw new Error(event.error);
    else throw new Error('The assistant returned an invalid stream event.');
  };

  try {
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      const lines = buffer.split('\n');
      buffer = lines.pop() ?? '';
      for (const line of lines) consume(line);
      if (done) break;
    }
    consume(buffer);
  } catch (error) {
    await reader.cancel().catch(() => {});
    throw error;
  } finally {
    reader.releaseLock();
  }

  if (!result) throw new Error('The assistant stream ended before an answer arrived.');

  return result;
}
