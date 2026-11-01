import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { SESSION_STATUS_URL, useSignedIn } from '@/components/site/use-signed-in';

const fetchMock = vi.fn();

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status });
}

describe('useSignedIn', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', fetchMock);
  });

  afterEach(() => {
    fetchMock.mockReset();
    vi.unstubAllGlobals();
  });

  it('starts signed out and asks the cosmetic route once, uncached, with the abort signal', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ signedIn: false }));
    const { result } = renderHook(() => useSignedIn());

    expect(result.current).toBe(false);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(SESSION_STATUS_URL);
    expect(init.cache).toBe('no-store');
    expect(init.credentials).toBe('same-origin');
    expect(init.signal).toBeInstanceOf(AbortSignal);
    expect(result.current).toBe(false);
  });

  it('becomes true when the route says signedIn', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ signedIn: true }));
    const { result } = renderHook(() => useSignedIn());
    await waitFor(() => expect(result.current).toBe(true));
  });

  it.each([
    ['a network error', () => fetchMock.mockRejectedValue(new TypeError('offline'))],
    ['a 429', () => fetchMock.mockResolvedValue(jsonResponse({ error: 'slow down' }, 429))],
    ['a 500', () => fetchMock.mockResolvedValue(jsonResponse({ signedIn: true }, 500))],
    ['a non-JSON body', () => fetchMock.mockResolvedValue(new Response('<html>', { status: 200 }))],
    [
      'a truthy but non-boolean value',
      () => fetchMock.mockResolvedValue(jsonResponse({ signedIn: 'yes' })),
    ],
    ['a missing field', () => fetchMock.mockResolvedValue(jsonResponse({}))],
    ['a null body', () => fetchMock.mockResolvedValue(jsonResponse(null))],
  ])('stays signed out on %s', async (_name, arrange) => {
    arrange();
    const { result } = renderHook(() => useSignedIn());
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(result.current).toBe(false);
  });

  it('aborts the request on unmount and ignores a late answer', async () => {
    let resolveFetch: (response: Response) => void = () => {};
    fetchMock.mockReturnValue(new Promise<Response>((resolve) => (resolveFetch = resolve)));
    const { result, unmount } = renderHook(() => useSignedIn());
    const signal = fetchMock.mock.calls[0][1].signal as AbortSignal;

    expect(signal.aborted).toBe(false);
    unmount();
    expect(signal.aborted).toBe(true);

    resolveFetch(jsonResponse({ signedIn: true }));
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(result.current).toBe(false);
  });
});
