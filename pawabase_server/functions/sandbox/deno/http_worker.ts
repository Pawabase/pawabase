/**
 * The worker of one HTTP function: a Deno process that holds the function's module and answers many requests at once.
 *
 * It never opens a socket. Requests arrive from the runtime as messages on standard input, and the response goes back the same way, as a
 * head and then body chunks, so a response can stream (server-sent events, large downloads) and a request body is read as it arrives.
 * Standard output is the protocol and nothing else: `console.*` is recorded per request and sent as log messages.
 *
 * host → worker
 *   init        { artifact, function, entry, env, branch, deployment, network }
 *   request     { id, method, url, headers, body: bool, context }     a new request; its body follows as `body` messages when `body` is true
 *   body        { id, data }  /  body_end { id }                       request body chunks (base64)
 *   abort       { id, reason }                                         the caller went away or the time ran out
 *   reply       { cid, ok, status, body }                              the answer to a runtime call
 *   fetch_head  { fid, status, status_text, headers, url, redirected }
 *   fetch_body  { fid, data } / fetch_end { fid } / fetch_error { fid, message, code }
 *   stop        {}
 *
 * worker → host
 *   ready       { function, style, config, errors, runtime }
 *   head        { id, status, headers }                                then chunk { id, data } … end { id }
 *   failed      { id, kind, message, where }                           the handler threw (before or after the head)
 *   log         { id, level, message }
 *   call        { cid, id, method, args, kwargs }                      a ctx.runtime call, made for request `id`
 *   fetch       { fid, id, method, url, headers, body: bool, redirect }  then fetch_body { fid, data } … fetch_body_end { fid }
 *   fetch_abort { fid }
 *   background  { id, pending }                                       waitUntil work still running for request `id` (0: all settled)
 *   stats       { heap, rss, active }                                 every few seconds
 */

import { AsyncLocalStorage } from "node:async_hooks";
import { __contexts as contexts, __http as http, decode, encode, FunctionError, Runtime, type HttpContext } from "./pawabase.ts";
import { installServeShim } from "./http_support.ts";

installServeShim();

const encoder = new TextEncoder();
const decoder = new TextDecoder();

function writeAll(data: Uint8Array) {
  let offset = 0;
  while (offset < data.length) offset += Deno.stdout.writeSync(data.subarray(offset));
}

function send(message: Record<string, unknown>) {
  writeAll(encoder.encode(JSON.stringify(message) + "\n"));
}

// ── base64, without spreading large arrays onto the stack ─────────────────

function toBase64(bytes: Uint8Array): string {
  let binary = "";
  const step = 0x8000;
  for (let i = 0; i < bytes.length; i += step) binary += String.fromCharCode(...bytes.subarray(i, i + step));
  return btoa(binary);
}

function fromBase64(text: string): Uint8Array {
  const binary = atob(text);
  const out = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) out[i] = binary.charCodeAt(i);
  return out;
}

const CHUNK = 64 * 1024;

// ── per-request state ────────────────────────────────────────────────────

interface Active {
  id: string;
  controller: AbortController;
  /** Feeds the request body as `body` messages arrive. */
  body: ReadableStreamDefaultController<Uint8Array> | null;
  background: Set<Promise<unknown>>;
  responded: boolean;
}

const store = new AsyncLocalStorage<string>();
const active = new Map<string, Active>();

function current(): string | null {
  return store.getStore() ?? null;
}

// console.* is recorded with the request that printed it; output outside a request (module load) is recorded without one.
function record(level: string) {
  return (...parts: unknown[]) => {
    const message = parts.map((p) => (typeof p === "string" ? p : Deno.inspect(p, { depth: 4, colors: false }))).join(" ");
    send({ t: "log", id: current(), level, message: message.slice(0, 16_000) });
  };
}
for (const [name, level] of [["log", "info"], ["info", "info"], ["debug", "debug"], ["warn", "warning"], ["error", "error"]] as const) {
  (console as any)[name] = record(level);
}

// ── ctx.runtime: calls answered by the platform as the request's caller ────

let callCounter = 0;
const calls = new Map<number, (reply: any) => void>();

function runtimeFor(id: string): Runtime {
  return new Runtime((method, args, kwargs) => {
    const cid = ++callCounter;
    return new Promise((resolve, reject) => {
      calls.set(cid, (reply) => {
        if (reply.ok) resolve(decode(reply.body?.result));
        else {
          const body = reply.body || {};
          reject(
            new FunctionError(body.message || body.detail || `${method} failed`, {
              status: reply.status || 500,
              code: body.error || body.code || "runtime_error",
              details: body.details,
            }),
          );
        }
      });
      send({ t: "call", cid, id, method, args: encode(args), kwargs: encode(kwargs) });
    });
  });
}

// ── fetch(), made by the platform ─────────────────────────────────────────

interface PendingFetch {
  resolve: (response: Response) => void;
  reject: (error: unknown) => void;
  stream: ReadableStreamDefaultController<Uint8Array> | null;
  settled: boolean;
}

let fetchCounter = 0;
const fetches = new Map<number, PendingFetch>();

async function guardedFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const request = new Request(input, init);
  const id = current();
  if (!id) throw new TypeError("fetch() is only available while a request is being answered (not when the module loads)");
  if (!/^https?:$/.test(new URL(request.url).protocol)) throw new TypeError(`fetch() supports http and https URLs, not ${request.url}`);
  const fid = ++fetchCounter;
  const hasBody = request.body !== null;
  const headers: [string, string][] = [];
  request.headers.forEach((value, key) => headers.push([key, value]));
  const response = new Promise<Response>((resolve, reject) => {
    fetches.set(fid, { resolve, reject, stream: null, settled: false });
  });
  const onAbort = () => {
    const pending = fetches.get(fid);
    send({ t: "fetch_abort", fid });
    if (pending && !pending.settled) {
      pending.settled = true;
      pending.reject(request.signal.reason ?? new DOMException("The request was aborted", "AbortError"));
    }
    pending?.stream?.error(new DOMException("The request was aborted", "AbortError"));
    fetches.delete(fid);
  };
  if (request.signal.aborted) throw request.signal.reason ?? new DOMException("The request was aborted", "AbortError");
  request.signal.addEventListener("abort", onAbort, { once: true });
  send({ t: "fetch", fid, id, method: request.method, url: request.url, headers, body: hasBody, redirect: request.redirect });
  if (hasBody) {
    const reader = request.body!.getReader();
    (async () => {
      try {
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          for (let at = 0; at < value.length; at += CHUNK) send({ t: "fetch_body", fid, data: toBase64(value.subarray(at, at + CHUNK)) });
        }
        send({ t: "fetch_body_end", fid });
      } catch (error) {
        send({ t: "fetch_abort", fid });
        fetches.get(fid)?.reject(error);
      }
    })();
  }
  return response;
}

function onFetchMessage(message: any) {
  const pending = fetches.get(message.fid);
  if (!pending) return;
  if (message.t === "fetch_head") {
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        pending.stream = controller;
      },
      cancel() {
        send({ t: "fetch_abort", fid: message.fid });
        fetches.delete(message.fid);
      },
    });
    const nullBody = [101, 204, 205, 304].includes(message.status);
    const response = new Response(nullBody ? null : body, { status: message.status, statusText: message.status_text || "", headers: message.headers });
    Object.defineProperty(response, "url", { value: message.url || "" });
    Object.defineProperty(response, "redirected", { value: Boolean(message.redirected) });
    pending.settled = true;
    pending.resolve(response);
    if (nullBody) fetches.delete(message.fid);
  } else if (message.t === "fetch_body") {
    pending.stream?.enqueue(fromBase64(message.data));
  } else if (message.t === "fetch_end") {
    try {
      pending.stream?.close();
    } catch {
      // already cancelled by the reader
    }
    fetches.delete(message.fid);
  } else if (message.t === "fetch_error") {
    const error = new TypeError(message.message || "fetch failed");
    (error as any).code = message.code;
    if (!pending.settled) {
      pending.settled = true;
      pending.reject(error);
    } else {
      pending.stream?.error(error);
    }
    fetches.delete(message.fid);
  }
}

// ── EdgeRuntime, for code written for Supabase ───────────────────────────

function waitUntilFor(state: Active) {
  return (promise: Promise<unknown>) => {
    const tracked = Promise.resolve(promise)
      .catch((error) => console.error("waitUntil task failed:", error))
      .finally(() => {
        state.background.delete(tracked);
        send({ t: "background", id: state.id, pending: state.background.size });
        if (!state.background.size && state.responded) active.delete(state.id);
      });
    state.background.add(tracked);
    send({ t: "background", id: state.id, pending: state.background.size });
  };
}

(globalThis as any).EdgeRuntime = {
  waitUntil(promise: Promise<unknown>) {
    const id = current();
    const state = id ? active.get(id) : undefined;
    if (!state) throw new Error("EdgeRuntime.waitUntil() must be called while a request is being answered");
    waitUntilFor(state)(promise);
  },
};

// ── answering a request ──────────────────────────────────────────────────

let loaded: { artifact: string; function: string; env: string; branch: string } | null = null;

function where(error: unknown) {
  const stack = error instanceof Error && error.stack ? error.stack.split("\n") : [];
  const frames = [];
  const artifact = loaded?.artifact ?? "\u0000";
  for (const line of stack) {
    const match = line.match(/(?:at (?:(.+?) \()?)file:\/\/(.+?):(\d+):\d+\)?$/);
    if (match && match[2].startsWith(artifact)) {
      frames.push({ file: match[2].slice(artifact.length + 1), line: Number(match[3]), function: match[1] || "<module>", code: null });
    }
  }
  return frames;
}

function failure(id: string, error: unknown) {
  if (error instanceof FunctionError) {
    // A failure the function chose: answer with its status and the platform's error shape.
    const body = encoder.encode(JSON.stringify({ error: error.code, message: error.message, details: error.details ?? null }));
    send({ t: "head", id, status: error.status, headers: [["content-type", "application/json"]] });
    send({ t: "chunk", id, data: toBase64(body) });
    send({ t: "end", id });
    return;
  }
  const err = error instanceof Error ? error : new Error(String(error));
  send({ t: "failed", id, kind: err.name || "Error", message: err.message, where: where(err) });
}

async function answer(message: any) {
  const id: string = message.id;
  const controller = new AbortController();
  const state: Active = { id, controller, body: null, background: new Set(), responded: false };
  active.set(id, state);
  const registration = http.current;
  if (!registration || !loaded) {
    send({ t: "failed", id, kind: "FunctionMissing", message: "this worker holds no HTTP function", where: [] });
    active.delete(id);
    return;
  }
  const body = message.body
    ? new ReadableStream<Uint8Array>({
        start(c) {
          state.body = c;
        },
      })
    : null;
  const init: RequestInit & { duplex?: string } = { method: message.method, headers: message.headers, signal: controller.signal };
  if (body && !["GET", "HEAD"].includes(message.method)) {
    init.body = body;
    init.duplex = "half";
  }
  const request = new Request(message.url, init);
  const context = message.context || {};
  const url = new URL(message.url);
  const ctx: HttpContext = {
    auth: context.auth || {},
    env: loaded.env,
    branch: loaded.branch,
    project: context.project || "",
    function: loaded.function,
    requestId: context.request_id || id,
    path: context.path || "/",
    query: url.searchParams,
    runtime: runtimeFor(id),
    log: (...parts: unknown[]) => record("info")(...parts),
    waitUntil: waitUntilFor(state),
    signal: controller.signal,
  };
  contexts.set(request, ctx);
  await store.run(id, async () => {
    let response: Response;
    try {
      const result = await (registration.kind === "pawabase"
        ? registration.handler(request, ctx)
        : registration.handler(request, { remoteAddr: { transport: "tcp", hostname: context.client_ip || "0.0.0.0", port: 0 }, completed: Promise.resolve() }));
      if (!(result instanceof Response)) {
        throw new TypeError(`the handler must return a Response, not ${result === undefined ? "nothing" : typeof result}`);
      }
      response = result;
    } catch (error) {
      failure(id, error);
      state.responded = true;
      if (!state.background.size) active.delete(id);
      return;
    }
    const headers: [string, string][] = [];
    response.headers.forEach((value, key) => headers.push([key, value]));
    // Headers.forEach joins set-cookie; send each cookie on its own.
    const cookies = typeof (response.headers as any).getSetCookie === "function" ? (response.headers as any).getSetCookie() : [];
    const plain = headers.filter(([key]) => key !== "set-cookie");
    for (const cookie of cookies) plain.push(["set-cookie", cookie]);
    send({ t: "head", id, status: response.status, headers: plain });
    try {
      if (response.body && message.method !== "HEAD") {
        const reader = response.body.getReader();
        controller.signal.addEventListener("abort", () => reader.cancel(controller.signal.reason).catch(() => {}), { once: true });
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          for (let at = 0; at < value.length; at += CHUNK) send({ t: "chunk", id, data: toBase64(value.subarray(at, at + CHUNK)) });
        }
      }
      send({ t: "end", id });
    } catch (error) {
      failure(id, error);
    } finally {
      state.responded = true;
      if (!state.background.size) active.delete(id);
    }
  });
}

// ── loading ──────────────────────────────────────────────────────────────

async function init(message: any) {
  loaded = { artifact: message.artifact, function: message.function, env: message.env, branch: message.branch };
  if (message.network === "guarded") {
    Object.defineProperty(globalThis, "fetch", { value: guardedFetch, configurable: true, writable: true });
  }
  const errors: string[] = [];
  let config: Record<string, unknown> = {};
  try {
    const module = await import(`file://${message.artifact}/${message.entry}`);
    if (module.config && typeof module.config === "object") config = JSON.parse(JSON.stringify(module.config));
    if (!http.current) errors.push(`${message.entry}: it never calls serve() or Deno.serve(), so it answers nothing.`);
  } catch (error) {
    errors.push(`${message.entry}: ${error instanceof Error ? `${error.name}: ${error.message}` : String(error)}`);
  }
  send({
    t: "ready",
    function: message.function,
    style: http.current?.kind ?? null,
    config,
    errors,
    runtime: { deno: Deno.version.deno, v8: Deno.version.v8, typescript: Deno.version.typescript, confinement: Deno.env.get("PAWABASE_CONFINED") || "off" },
  });
}

setInterval(() => {
  const memory = Deno.memoryUsage();
  send({ t: "stats", heap: memory.heapUsed, rss: memory.rss, active: active.size });
}, 5000);

// ── the loop ─────────────────────────────────────────────────────────────

let buffer = "";
for await (const chunk of Deno.stdin.readable) {
  buffer += decoder.decode(chunk, { stream: true });
  let newline;
  while ((newline = buffer.indexOf("\n")) >= 0) {
    const line = buffer.slice(0, newline);
    buffer = buffer.slice(newline + 1);
    if (!line.trim()) continue;
    const message = JSON.parse(line);
    switch (message.t) {
      case "init":
        await init(message);
        break;
      case "request":
        answer(message);
        break;
      case "body": {
        const state = active.get(message.id);
        try {
          state?.body?.enqueue(fromBase64(message.data));
        } catch {
          // the handler cancelled its body
        }
        break;
      }
      case "body_end": {
        const state = active.get(message.id);
        try {
          state?.body?.close();
        } catch {
          // already closed or cancelled
        }
        break;
      }
      case "abort": {
        const state = active.get(message.id);
        if (state) {
          state.controller.abort(new DOMException(message.reason || "The request was aborted", "AbortError"));
          try {
            state.body?.error(new DOMException("The request was aborted", "AbortError"));
          } catch {
            // the body was already finished
          }
        }
        break;
      }
      case "reply": {
        const resolve = calls.get(message.cid);
        calls.delete(message.cid);
        resolve?.(message);
        break;
      }
      case "fetch_head":
      case "fetch_body":
      case "fetch_end":
      case "fetch_error":
        onFetchMessage(message);
        break;
      case "stop":
        Deno.exit(0);
    }
  }
}
Deno.exit(0);
