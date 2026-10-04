import { afterEach, describe, expect, it, vi } from 'vitest';
import { askAssistant } from '../src/workspace/api';
import type { PipelineProgress } from '../src/workspace/types';

afterEach(() => vi.unstubAllGlobals());

function streamResponse(text: string, chunkSize = 7): Response {
  const bytes = new TextEncoder().encode(text);
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (let offset = 0; offset < bytes.length; offset += chunkSize) {
        controller.enqueue(bytes.slice(offset, offset + chunkSize));
      }
      controller.close();
    },
  });

  return new Response(stream, { headers: { 'Content-Type': 'application/x-ndjson' } });
}

describe('assistant progress stream', () => {
  it('decodes split lines and multibyte text and delivers progress before the result', async () => {
    const event: PipelineProgress = {
      id: 'step-1',
      request_id: 'request-1',
      stage: 'planning.recipe',
      message: 'Prepare rainfall · movement',
      status: 'running',
      elapsed_seconds: 1,
    };
    const result = { answer: 'Rainfall: 5 mm.', updated: false };
    const events = [
      JSON.stringify({ type: 'progress', ...event }),
      JSON.stringify({ type: 'result', response: result }),
    ];
    const fetch = vi.fn().mockResolvedValue(streamResponse(events.join('\n')));
    vi.stubGlobal('fetch', fetch);
    const onProgress = vi.fn();

    const response = await askAssistant(
      [{ role: 'user', content: 'Rainfall?' }],
      undefined,
      undefined,
      onProgress,
    );

    expect(response).toEqual(result);
    expect(onProgress).toHaveBeenCalledWith({ type: 'progress', ...event });
    expect(fetch.mock.calls[0][1].headers.Accept).toBe('application/x-ndjson');
  });

  it('reports stream failures and incomplete responses', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(streamResponse('')));
    await expect(askAssistant([], undefined, undefined)).rejects.toThrow(
      'before an answer arrived',
    );

    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValue(
          streamResponse(JSON.stringify({ type: 'error', error: 'Preparation failed.' })),
        ),
    );
    await expect(askAssistant([], undefined, undefined)).rejects.toThrow('Preparation failed.');
  });

  it('keeps compatibility with JSON responses and pre-stream validation failures', async () => {
    const result = { answer: 'No data.', updated: false };
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json(result)));
    await expect(askAssistant([], undefined, undefined)).resolves.toEqual(result);

    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(Response.json({ error: 'Invalid request.' }, { status: 400 })),
    );
    await expect(askAssistant([], undefined, undefined)).rejects.toThrow('Invalid request.');
  });
});
