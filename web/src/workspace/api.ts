import type { CatalogResponse, ChatMessage, ChatResponse, DatasetSnapshot } from './types';

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, options);
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error;

    throw new Error('Cannot reach the backend. Start the Habitat Watch API and retry.');
  }

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

export function fetchCatalog(signal?: AbortSignal): Promise<CatalogResponse> {
  return request('/api/catalog', { signal });
}

export function fetchDataset(datasetId: string, signal?: AbortSignal): Promise<DatasetSnapshot> {
  const parameters = new URLSearchParams({ dataset_id: datasetId });

  return request(`/api/features?${parameters}`, { signal });
}

export function askAssistant(
  messages: ChatMessage[],
  datasetId: string | undefined,
  through: string | undefined,
): Promise<ChatResponse> {
  return request('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ messages, dataset_id: datasetId, through }),
  });
}
