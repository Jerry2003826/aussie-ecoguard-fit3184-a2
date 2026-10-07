import { config } from '../config';
export class ApiError extends Error {
  code: string;
  status: number;
  requestId?: string;
  retryable: boolean;
  constructor(
    message: string,
    code: string,
    status: number,
    requestId?: string,
    retryable = false,
  ) {
    super(message);
    this.name = 'ApiError';
    this.code = code;
    this.status = status;
    this.requestId = requestId;
    this.retryable = retryable;
  }
}
export type CredentialProvider = () => Promise<{ token?: string; devUser?: string }>;
let credentials: CredentialProvider = async () => ({});
export function setCredentials(provider: CredentialProvider) {
  credentials = provider;
}
export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const identity = await credentials();
  const headers = new Headers(init.headers);
  headers.set('Accept', 'application/json');
  if (init.body && !(init.body instanceof FormData))
    headers.set('Content-Type', 'application/json');
  if (identity.token) headers.set('Authorization', `Bearer ${identity.token}`);
  if (config.devMode && identity.devUser) headers.set('X-Ecoguard-Dev-User', identity.devUser);
  if (init.method && !['GET', 'HEAD'].includes(init.method) && !headers.has('Idempotency-Key'))
    headers.set('Idempotency-Key', crypto.randomUUID());
  let response: Response;
  try {
    response = await fetch(`${config.apiBase}${path}`, { ...init, headers });
  } catch (e) {
    if (e instanceof DOMException && e.name === 'AbortError') throw e;
    throw new ApiError(
      'We could not reach the service. Check your connection and try again.',
      'NETWORK_ERROR',
      0,
      undefined,
      true,
    );
  }
  const text = await response.text();
  let body: any;
  try {
    body = text ? JSON.parse(text) : {};
  } catch {
    throw new ApiError(
      'The service returned an unexpected response. Please try again.',
      'INVALID_RESPONSE',
      response.status,
    );
  }
  if (!response.ok) {
    const error = body.error || body;
    if (response.status === 401) window.dispatchEvent(new Event('ecoguard:unauthorized'));
    throw new ApiError(
      error.message || `Request failed (${response.status}).`,
      error.code || 'REQUEST_FAILED',
      response.status,
      body.requestId || error.requestId,
      Boolean(error.retryable),
    );
  }
  return (Object.prototype.hasOwnProperty.call(body, 'data') ? body.data : body) as T;
}
export const api = {
  get: <T>(path: string, signal?: AbortSignal) => request<T>(path, { signal }),
  post: <T>(path: string, body: unknown = {}) =>
    request<T>(path, { method: 'POST', body: JSON.stringify(body) }),
  patch: <T>(path: string, body: unknown) =>
    request<T>(path, { method: 'PATCH', body: JSON.stringify(body) }),
  delete: <T>(path: string) => request<T>(path, { method: 'DELETE' }),
};
export function uploadFile(
  url: string,
  file: File,
  headers: Record<string, string>,
  onProgress: (value: number) => void,
  signal?: AbortSignal,
  method = 'PUT',
): Promise<void> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const abort = () => xhr.abort();
    const finish = () => signal?.removeEventListener('abort', abort);
    xhr.open(method, url);
    Object.entries(headers).forEach(([key, value]) => xhr.setRequestHeader(key, value));
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(Math.round((event.loaded / event.total) * 100));
    };
    xhr.onload = () => {
      finish();
      if (xhr.status >= 200 && xhr.status < 300) {
        onProgress(100);
        resolve();
      } else
        reject(
          new ApiError(
            'The upload could not finish. Retry to request a fresh upload link.',
            'UPLOAD_FAILED',
            xhr.status,
          ),
        );
    };
    xhr.onerror = () => {
      finish();
      reject(
        new ApiError(
          'Upload interrupted. Check your connection and retry.',
          'UPLOAD_NETWORK_ERROR',
          0,
        ),
      );
    };
    xhr.onabort = () => {
      finish();
      reject(new DOMException('Upload cancelled', 'AbortError'));
    };
    signal?.addEventListener('abort', abort, { once: true });
    if (signal?.aborted) {
      finish();
      reject(new DOMException('Upload cancelled', 'AbortError'));
      return;
    }
    xhr.send(file);
  });
}
