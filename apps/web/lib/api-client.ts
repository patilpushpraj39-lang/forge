export type ApiAuthentication = {
  status: "loading" | "signed-out" | "signed-in" | "unconfigured";
  sessionKey?: string;
  accessDenied?: 401 | 403;
  getAccessToken?: (forceRefresh?: boolean) => Promise<string | null>;
};

export type RunEvent = {
  event_id: string;
  sequence: number;
  event_type: string;
  payload: Record<string, unknown>;
};

export class ApiAccessError extends Error {
  readonly status: number;
  constructor(status: number) {
    super(status === 401 ? "Sign in again to continue; your session is unavailable or expired."
      : status === 403 ? "This account is not authorized to access Forge."
      : status === 404 ? "The requested Forge evidence was not found."
      : status === 503 ? "Forge is temporarily unavailable. Try again shortly."
      : `Forge request failed (${status}).`);
    this.status = status;
  }
}

class StreamProtocolError extends Error {
  constructor() { super("Forge sent an invalid event stream. Reload to try again."); }
}

function abortIfNeeded(signal?: AbortSignal | null) {
  if (signal?.aborted) throw new DOMException("Request cancelled", "AbortError");
}

function pause(delay: number, signal: AbortSignal): Promise<void> {
  abortIfNeeded(signal);
  return new Promise((resolve, reject) => {
    const finish = () => { signal.removeEventListener("abort", stop); resolve(); };
    const timer = setTimeout(finish, delay);
    const stop = () => {
      clearTimeout(timer);
      signal.removeEventListener("abort", stop);
      reject(new DOMException("Request cancelled", "AbortError"));
    };
    signal.addEventListener("abort", stop, { once: true });
  });
}

function decodeEvent(data: string): RunEvent {
  try {
    const event = JSON.parse(data) as RunEvent;
    if (!event || typeof event.event_id !== "string" || !event.event_id
      || !Number.isSafeInteger(event.sequence) || event.sequence <= 0
      || typeof event.event_type !== "string" || !event.event_type
      || !event.payload || typeof event.payload !== "object" || Array.isArray(event.payload)) {
      throw new StreamProtocolError();
    }
    return event;
  } catch { throw new StreamProtocolError(); }
}

// Incremental UTF-8 / SSE framing: comments, multiline data and CR/LF/CRLF.
async function readEvents(body: ReadableStream<Uint8Array>, signal: AbortSignal,
  receive: (event: RunEvent) => void): Promise<void> {
  const reader = body.getReader();
  const decoder = new TextDecoder("utf-8", { fatal: true });
  let buffer = "";
  let data: string[] = [];
  let frameSize = 0;
  const stop = () => { void reader.cancel().catch(() => {}); };
  signal.addEventListener("abort", stop, { once: true });
  try {
    while (true) {
      abortIfNeeded(signal);
      const { done, value } = await reader.read();
      abortIfNeeded(signal);
      // A trailing CR is itself a line ending once EOF proves no LF follows.
      // Other incomplete frames are deliberately replayed on reconnect.
      if (done && !buffer.endsWith("\r")) return;
      try { buffer += done ? "\n" : decoder.decode(value, { stream: true }); }
      catch { throw new StreamProtocolError(); }
      let index: number;
      while ((index = buffer.search(/[\r\n]/)) >= 0) {
        if (buffer[index] === "\r" && index === buffer.length - 1) break;
        const line = buffer.slice(0, index);
        const width = buffer[index] === "\r" && buffer[index + 1] === "\n" ? 2 : 1;
        buffer = buffer.slice(index + width);
        frameSize += line.length;
        if (frameSize > 1_048_576) throw new StreamProtocolError();
        if (!line) {
          if (data.length) receive(decodeEvent(data.join("\n")));
          data = [];
          frameSize = 0;
        } else if (line.startsWith("data:")) {
          const value = line.slice(5);
          data.push(value.startsWith(" ") ? value.slice(1) : value);
        }
      }
      if (buffer.length + frameSize > 1_048_576) throw new StreamProtocolError();
      if (done) return;
    }
  } finally {
    signal.removeEventListener("abort", stop);
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}

export function createApiClient(base: string, authentication: ApiAuthentication,
  mode: string = "development", fetcher: typeof fetch = fetch) {
  const root = new URL(base);
  if (!["development", "controlled"].includes(mode) || root.username || root.password
    || root.search || root.hash || root.pathname !== "/"
    || (root.protocol !== "https:" && !(mode === "development" && root.protocol === "http:"
      && ["localhost", "127.0.0.1", "[::1]"].includes(root.hostname)))) {
    throw new Error("Invalid Forge API URL or web access profile.");
  }
  const ready = !authentication.accessDenied && (authentication.status === "signed-in"
    || (mode === "development" && authentication.status === "unconfigured"));

  async function request(path: string, init: RequestInit = {}, requireSession = false): Promise<Response> {
    if (!path.startsWith("/") || path.startsWith("//") || path.includes("\\")) {
      throw new Error("Invalid Forge API path.");
    }
    const url = new URL(path, root);
    if (url.origin !== root.origin) throw new Error("Invalid Forge API path.");
    const headers = new Headers(init.headers);
    headers.delete("authorization"); // Only the session provider may choose credentials.
    const needsToken = mode === "controlled" || requireSession || authentication.status !== "unconfigured";
    async function send(forceRefresh: boolean) {
      abortIfNeeded(init.signal);
      if (authentication.accessDenied) throw new ApiAccessError(authentication.accessDenied);
      if (needsToken) {
        if (authentication.status !== "signed-in" || !authentication.getAccessToken) throw new ApiAccessError(401);
        let token: string | null;
        try { token = await authentication.getAccessToken(forceRefresh); }
        catch { throw new ApiAccessError(401); }
        abortIfNeeded(init.signal);
        if (!token) throw new ApiAccessError(401);
        headers.set("authorization", `Bearer ${token}`);
      }
      return fetcher(url.toString(), { ...init, headers, cache: "no-store", credentials: "omit", redirect: "error" });
    }
    let response = await send(false);
    // Retry only an explicit rejection of a GET, never a possibly executed mutation.
    if (response.status === 401 && needsToken && (init.method ?? "GET").toUpperCase() === "GET") {
      await response.body?.cancel().catch(() => {});
      response = await send(true);
    }
    if (!response.ok) {
      await response.body?.cancel().catch(() => {});
      throw new ApiAccessError(response.status);
    }
    return response;
  }

  async function stream(runId: string, options: {
    signal: AbortSignal;
    onEvent: (event: RunEvent) => void;
    onStatus?: (message: string | null) => void;
    retryDelayMs?: number;
    rotationMs?: number;
  }): Promise<void> {
    let cursor = 0;
    let failures = 0;
    while (!options.signal.aborted) {
      const connection = new AbortController();
      const stop = () => connection.abort();
      options.signal.addEventListener("abort", stop, { once: true });
      let rotated = false;
      // Re-authenticate periodically, rather than leave a once-authorized stream open indefinitely.
      const timer = setTimeout(() => { rotated = true; connection.abort(); }, options.rotationMs ?? 45_000);
      try {
        const response = await request(`/runs/${encodeURIComponent(runId)}/events/stream?after=${cursor}`, {
          signal: connection.signal, headers: { accept: "text/event-stream" }
        });
        if (!response.body || response.headers.get("content-type")?.split(";")[0].trim() !== "text/event-stream") {
          await response.body?.cancel().catch(() => {});
          throw new StreamProtocolError();
        }
        options.onStatus?.(null);
        await readEvents(response.body, connection.signal, (event) => {
          if (event.sequence <= cursor) return;
          if (event.sequence !== cursor + 1) throw new StreamProtocolError();
          options.onEvent(event);
          cursor = event.sequence; // Advance only after a complete, accepted event.
          failures = 0;
        });
        if (!rotated) failures += 1;
      } catch (caught) {
        if (options.signal.aborted) return;
        if (!rotated) {
          if (caught instanceof StreamProtocolError
            || (caught instanceof ApiAccessError && ![429, 503].includes(caught.status) && caught.status < 500)) {
            throw caught;
          }
          failures += 1;
        }
      } finally {
        clearTimeout(timer);
        options.signal.removeEventListener("abort", stop);
        connection.abort();
      }
      if (options.signal.aborted) return;
      if (failures >= 5) throw new Error("Live progress is unavailable. Reload to reconnect.");
      if (!rotated) options.onStatus?.("Live progress disconnected; reconnecting from the last received event…");
      try { await pause(rotated ? 0 : Math.min(15_000, (options.retryDelayMs ?? 1000) * 2 ** Math.max(0, failures - 1)), options.signal); }
      catch { return; }
    }
  }

  return { ready, request, stream };
}
