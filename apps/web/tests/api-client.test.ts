import assert from "node:assert/strict";
import { test } from "node:test";
import { ApiAccessError, createApiClient } from "../lib/api-client.ts";
import type { ApiAuthentication, RunEvent } from "../lib/api-client.ts";

const signedIn: ApiAuthentication = { status: "signed-in", getAccessToken: async () => "fixture-token" };
const event = (sequence: number, label = "fixture") => ({ event_id: `event-${sequence}`, sequence,
  event_type: "state_changed", payload: { to_state: "EXECUTING", label } });
const frame = (sequence: number, label?: string) => `data: ${JSON.stringify(event(sequence, label))}\n\n`;
const response = (chunks: Uint8Array[], fail = false) => new Response(new ReadableStream({
  pull(controller) {
    const next = chunks.shift();
    if (next) controller.enqueue(next);
    else if (fail) controller.error(new Error("fixture disconnect"));
    else controller.close();
  }
}), { headers: { "content-type": "text/event-stream; charset=utf-8" } });
const bytes = (value: string) => new TextEncoder().encode(value);

test("all protected reads and commands attach bearer headers, not URL credentials", async () => {
  const calls: string[] = [];
  const client = createApiClient("https://api.forge.test", signedIn, "controlled", async (input, init) => {
    calls.push(String(input));
    assert.equal(new Headers(init?.headers).get("authorization"), "Bearer fixture-token");
    assert.equal(init?.cache, "no-store");
    assert.equal(init?.credentials, "omit");
    assert.equal(init?.redirect, "error");
    return Response.json({ ok: true });
  });
  for (const path of ["/runs/id", "/runs/id/review", "/benchmarks/latest?category=bug", "/github/installations", "/auth/me"]) {
    await client.request(path);
  }
  for (const path of ["/runs/id/cancel", "/runs/id/approvals", "/runs/id/publish", "/github/runs"]) {
    await client.request(path, { method: "POST" });
  }
  assert.equal(calls.length, 9);
  assert.ok(calls.every(url => !url.includes("fixture-token")));
});

test("controlled loading, signed-out and unconfigured sessions never send a request", async () => {
  for (const status of ["loading", "signed-out", "unconfigured"] as const) {
    const client = createApiClient("https://api.forge.test", { status }, "controlled", async () => {
      assert.fail("unauthenticated request escaped");
    });
    assert.equal(client.ready, false);
    await assert.rejects(client.request("/runs/id"), ApiAccessError);
  }
});

test("trusted development reads remain unauthenticated, but approval still requires a session", async () => {
  let calls = 0;
  const client = createApiClient("http://127.0.0.1:8000", { status: "unconfigured" }, "development", async (_url, init) => {
    calls += 1;
    assert.equal(new Headers(init?.headers).get("authorization"), null);
    return Response.json({});
  });
  assert.equal(client.ready, true);
  await client.request("/runs/id", { headers: { authorization: "Bearer caller-supplied" } });
  await assert.rejects(client.request("/runs/id/approvals", { method: "POST" }, true), ApiAccessError);
  assert.equal(calls, 1);
});

test("an expired GET refreshes once using a fresh token", async () => {
  const refresh: boolean[] = [];
  let calls = 0;
  const client = createApiClient("https://api.forge.test", { status: "signed-in", getAccessToken: async force => {
    refresh.push(Boolean(force)); return force ? "fixture-fresh" : "fixture-expired";
  } }, "controlled", async (_url, init) => {
    calls += 1;
    assert.equal(new Headers(init?.headers).get("authorization"), calls === 1 ? "Bearer fixture-expired" : "Bearer fixture-fresh");
    return calls === 1 ? new Response("", { status: 401 }) : Response.json({});
  });
  await client.request("/runs/id");
  assert.deepEqual(refresh, [false, true]);
});

test("401 refresh is bounded and never echoes provider rejection text", async () => {
  let calls = 0;
  const client = createApiClient("https://api.forge.test", signedIn, "controlled", async () => {
    calls += 1; return new Response("fixture-secret-provider-message", { status: 401 });
  });
  await assert.rejects(client.request("/runs/id"), error => error instanceof ApiAccessError
    && error.status === 401 && !error.message.includes("fixture-secret"));
  assert.equal(calls, 2);
});

test("mutations and forbidden GETs are never automatically retried", async () => {
  for (const [method, status] of [["POST", 401], ["GET", 403]] as const) {
    let calls = 0;
    const client = createApiClient("https://api.forge.test", signedIn, "controlled", async () => {
      calls += 1; return new Response("", { status });
    });
    await assert.rejects(client.request("/runs/id", { method }), ApiAccessError);
    assert.equal(calls, 1);
  }
});

test("null tokens and SDK errors fail closed without exposing SDK details", async () => {
  for (const getter of [async () => null, async () => { throw new Error("fixture-secret-sdk-error"); }]) {
    const client = createApiClient("https://api.forge.test", { status: "signed-in", getAccessToken: getter }, "controlled", async () => assert.fail("request escaped"));
    await assert.rejects(client.request("/runs/id"), error => error instanceof ApiAccessError && !error.message.includes("fixture-secret"));
  }
});

test("aborting while token retrieval is pending prevents the old-session request", async () => {
  const controller = new AbortController();
  let resolve!: (token: string) => void;
  const client = createApiClient("https://api.forge.test", { status: "signed-in", getAccessToken: () => new Promise(done => { resolve = done; }) }, "controlled", async () => assert.fail("stale session escaped"));
  const pending = client.request("/runs/id", { signal: controller.signal });
  controller.abort(); resolve("fixture-old-session");
  await assert.rejects(pending, { name: "AbortError" });
});

test("API origins and paths cannot redirect bearer credentials", async () => {
  for (const base of ["http://api.forge.test", "https://user:pass@api.forge.test", "https://api.forge.test/path", "https://api.forge.test?token=fixture"]) {
    assert.throws(() => createApiClient(base, signedIn, "controlled"));
  }
  assert.throws(() => createApiClient("https://api.forge.test", signedIn, "typo"));
  const client = createApiClient("https://api.forge.test", signedIn, "controlled", async () => assert.fail("path escaped"));
  for (const path of ["https://other.test", "//other.test", "/\\other.test"]) await assert.rejects(client.request(path));
});

test("stream parses split UTF-8, comments, CRLF and multiline data", async () => {
  const controller = new AbortController();
  const data = JSON.stringify(event(1, "café"));
  const at = data.indexOf(',"event_type"');
  const source = bytes(`: heartbeat\r\n\r\ndata: ${data.slice(0, at)}\r\ndata: ${data.slice(at)}\r\n\r\n`);
  const received: RunEvent[] = [];
  const client = createApiClient("https://api.forge.test", signedIn, "controlled", async () => response(Array.from(source, byte => new Uint8Array([byte]))));
  await client.stream("id", { signal: controller.signal, onEvent: item => { received.push(item); controller.abort(); } });
  assert.deepEqual(received, [event(1, "café")]);
});

test("disconnect replays only complete events and refreshes credentials on reconnect", async () => {
  const controller = new AbortController();
  const urls: string[] = [];
  let tokens = 0;
  const received: number[] = [];
  const client = createApiClient("https://api.forge.test", { status: "signed-in", getAccessToken: async () => `fixture-${++tokens}` }, "controlled", async (input, init) => {
    urls.push(String(input));
    assert.equal(new Headers(init?.headers).get("authorization"), `Bearer fixture-${urls.length}`);
    return urls.length === 1 ? response([bytes(frame(1) + 'data: {"partial":')], true)
      : response([bytes(frame(1) + frame(2))]);
  });
  await client.stream("id", { signal: controller.signal, retryDelayMs: 0, onEvent: item => {
    received.push(item.sequence); if (item.sequence === 2) controller.abort();
  } });
  assert.deepEqual(received, [1, 2]);
  assert.ok(urls[0].endsWith("after=0"));
  assert.ok(urls[1].endsWith("after=1"));
});

test("stream stops on forbidden access and invalid framing", async () => {
  for (const make of [() => new Response("", { status: 403 }), () => response([bytes("data: nope\n\n")]),
    () => Response.json({}), () => response([bytes("data: " + "x".repeat(1_048_577))])]) {
    let calls = 0;
    const client = createApiClient("https://api.forge.test", signedIn, "controlled", async () => { calls += 1; return make(); });
    await assert.rejects(client.stream("id", { signal: new AbortController().signal, onEvent: () => assert.fail("invalid event"), retryDelayMs: 0 }));
    assert.equal(calls, 1);
  }
});

test("empty or failed streams stop after bounded reconnects", async () => {
  let calls = 0;
  const client = createApiClient("https://api.forge.test", signedIn, "controlled", async () => { calls += 1; return response([]); });
  await assert.rejects(client.stream("id", { signal: new AbortController().signal, onEvent: () => {}, retryDelayMs: 0 }), /Reload to reconnect/);
  assert.equal(calls, 5);
});

test("aborting an open stream cancels its reader without reconnecting", async () => {
  const controller = new AbortController();
  let calls = 0;
  let cancelled = false;
  const client = createApiClient("https://api.forge.test", signedIn, "controlled", async () => {
    calls += 1;
    return new Response(new ReadableStream({ start() { setTimeout(() => controller.abort(), 5); }, cancel() { cancelled = true; } }),
      { headers: { "content-type": "text/event-stream" } });
  });
  await client.stream("id", { signal: controller.signal, onEvent: () => {} });
  assert.equal(calls, 1); assert.equal(cancelled, true);
});

test("periodic stream rotation re-authenticates even while the server stays open", async () => {
  const controller = new AbortController();
  let calls = 0;
  const received: number[] = [];
  const client = createApiClient("https://api.forge.test", signedIn, "controlled", async (_url) => {
    calls += 1;
    if (calls === 2) { assert.ok(String(_url).endsWith("after=1")); return response([bytes(frame(2))]); }
    return new Response(new ReadableStream({ start(stream) { stream.enqueue(bytes(frame(1))); } }),
      { headers: { "content-type": "text/event-stream" } });
  });
  await client.stream("id", { signal: controller.signal, rotationMs: 10, onEvent: item => {
    received.push(item.sequence); if (item.sequence === 2) controller.abort();
  } });
  assert.deepEqual(received, [1, 2]); assert.equal(calls, 2);
});

test("a CR-only event boundary at EOF is dispatched", async () => {
  const controller = new AbortController();
  const received: RunEvent[] = [];
  const client = createApiClient("https://api.forge.test", signedIn, "controlled", async () => response([bytes(frame(1).replaceAll("\n", "\r"))]));
  await client.stream("id", { signal: controller.signal, onEvent: item => { received.push(item); controller.abort(); } });
  assert.deepEqual(received, [event(1)]);
});

test("sequence gaps and invalid event payloads stop instead of advancing the replay cursor", async () => {
  for (const source of [frame(1) + frame(3), 'data: {"event_id":"x","sequence":1,"event_type":"x","payload":null}\n\n']) {
    let calls = 0;
    const received: number[] = [];
    const client = createApiClient("https://api.forge.test", signedIn, "controlled", async () => { calls += 1; return response([bytes(source)]); });
    await assert.rejects(client.stream("id", { signal: new AbortController().signal, onEvent: item => received.push(item.sequence), retryDelayMs: 0 }), /invalid event stream/);
    assert.equal(calls, 1);
    assert.deepEqual(received, source.startsWith(frame(1)) ? [1] : []);
  }
});

test("session expiry during reconnect stops after one refresh", async () => {
  let calls = 0;
  const client = createApiClient("https://api.forge.test", signedIn, "controlled", async () => {
    calls += 1; return calls === 1 ? response([bytes(frame(1))], true) : new Response("", { status: 401 });
  });
  await assert.rejects(client.stream("id", { signal: new AbortController().signal, onEvent: () => {}, retryDelayMs: 0 }), error => error instanceof ApiAccessError && error.status === 401);
  assert.equal(calls, 3);
});

test("temporary stream rejection uses bounded retry without leaking response text", async () => {
  let calls = 0;
  const client = createApiClient("https://api.forge.test", signedIn, "controlled", async () => {
    calls += 1; return new Response("fixture-private-detail", { status: 503 });
  });
  await assert.rejects(client.stream("id", { signal: new AbortController().signal, onEvent: () => {}, retryDelayMs: 0 }), /Reload to reconnect/);
  assert.equal(calls, 5);
});

test("a rejected session remains blocked until the session scope changes", async () => {
  for (const accessDenied of [401, 403] as const) {
    const client = createApiClient("https://api.forge.test", { ...signedIn, accessDenied,
      getAccessToken: async () => assert.fail("blocked token retrieval") }, "controlled", async () => assert.fail("blocked request"));
    assert.equal(client.ready, false);
    await assert.rejects(client.request("/runs/id"), error => error instanceof ApiAccessError && error.status === accessDenied);
  }
});
