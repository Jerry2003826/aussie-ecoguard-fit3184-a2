import { beforeEach, describe, expect, it, vi } from 'vitest';
import { request, setCredentials, ApiError } from '../api/client';
describe('authenticated API transport', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn());
    setCredentials(async () => ({ token: 'access-token-example' }));
  });
  it('sends access bearer identity and unwraps data without client owner attributes', async () => {
    vi.mocked(fetch).mockResolvedValue(
      new Response(JSON.stringify({ data: { items: [] } }), { status: 200 }),
    );
    expect(await request('/media')).toEqual({ items: [] });
    const [, init] = vi.mocked(fetch).mock.calls[0];
    expect(new Headers(init?.headers).get('Authorization')).toBe('Bearer access-token-example');
    expect(new Headers(init?.headers).get('X-Ecoguard-Dev-User')).toBeNull();
  });
  it('does not turn a dependency failure into an empty result', async () => {
    vi.mocked(fetch).mockResolvedValue(
      new Response(
        JSON.stringify({
          error: { code: 'LEX_UNAVAILABLE', message: 'Lex unavailable', retryable: true },
          requestId: 'request-test',
        }),
        { status: 503 },
      ),
    );
    await expect(request('/chat/messages')).rejects.toMatchObject({
      status: 503,
      code: 'LEX_UNAVAILABLE',
      requestId: 'request-test',
      retryable: true,
    });
  });
  it('invalidates the session on a rejected identity', async () => {
    const listener = vi.fn();
    window.addEventListener('ecoguard:unauthorized', listener);
    vi.mocked(fetch).mockResolvedValue(
      new Response(JSON.stringify({ error: { message: 'Expired' } }), { status: 401 }),
    );
    await expect(request('/media')).rejects.toBeInstanceOf(ApiError);
    expect(listener).toHaveBeenCalledOnce();
    window.removeEventListener('ecoguard:unauthorized', listener);
  });
  it('adds idempotency keys to writes', async () => {
    vi.mocked(fetch).mockResolvedValue(new Response('{"data":{}}', { status: 202 }));
    await request('/media/delete', { method: 'POST', body: '{"urls":[]}' });
    const [, init] = vi.mocked(fetch).mock.calls[0];
    expect(new Headers(init?.headers).get('Idempotency-Key')).toBeTruthy();
  });
});
