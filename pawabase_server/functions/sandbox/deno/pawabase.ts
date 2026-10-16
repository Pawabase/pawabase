/**
 * The Pawabase function kit for TypeScript: what a deployed `functions/*.ts` imports as `"pawabase"`.
 *
 *   import { fn, FunctionError } from "pawabase";
 *
 *   export const create = fn("orders.create", { policy: "authenticated" }, async (ctx) => {
 *     return await ctx.runtime.resourceCreate("orders", ctx.input);
 *   });
 *
 * It mirrors the Python kit: a name, a policy, an input, `auth`, and `ctx.runtime` for data, events, secrets, storage and outbound HTTP —
 * every runtime call goes back to the platform, which answers it with the caller's identity (this process has no network of its own).
 */

export type Policy = string | Record<string, unknown> | null;

export interface FunctionOptions {
  description?: string;
  policy?: Policy;
  timeout?: number;
  input_fields?: Record<string, unknown>[];
  tags?: string[];
}

export interface Context {
  input: any;
  auth: Record<string, any>;
  env: string;
  project: string;
  branch: string;
  trigger: string;
  request: any;
  runtime: Runtime;
  /** Lines recorded with the run, shown in Studio. */
  log: (...parts: unknown[]) => void;
}

export type Handler = (ctx: Context) => Promise<unknown> | unknown;

export interface FunctionSpec extends Required<Omit<FunctionOptions, "input_fields">> {
  name: string;
  /** Where `fn(...)` was called: the file URL and line, for Studio's code view. */
  location: { url: string; line: number } | null;
  input_fields: Record<string, unknown>[] | null;
  handler: Handler;
}

/** A failure the function chooses: the caller receives this status, code and message. */
export class FunctionError extends Error {
  status: number;
  code: string;
  details: unknown;
  constructor(message: string, options: { status?: number; code?: string; details?: unknown } = {}) {
    super(message);
    this.status = options.status ?? 400;
    this.code = options.code ?? "error";
    this.details = options.details ?? null;
  }
}

const NAME = /^[A-Za-z][A-Za-z0-9_.:-]{0,127}$/;
const registry: Map<string, FunctionSpec> = ((globalThis as any).__pawabase_registry ??= new Map());

/** Register a function. `fn(name, handler)` or `fn(name, options, handler)`. Returns the handler. */
export function fn(name: string, optionsOrHandler: FunctionOptions | Handler, maybeHandler?: Handler): Handler {
  const options = typeof optionsOrHandler === "function" ? {} : optionsOrHandler;
  const handler = typeof optionsOrHandler === "function" ? optionsOrHandler : maybeHandler;
  if (!NAME.test(name)) throw new Error(`${JSON.stringify(name)} is not a valid function name`);
  if (typeof handler !== "function") throw new Error(`function ${name} needs a handler`);
  if (registry.has(name)) throw new Error(`function ${name} is defined twice`);
  registry.set(name, {
    name,
    location: caller(),
    handler,
    description: options.description ?? "",
    policy: options.policy === undefined ? "authenticated" : options.policy,
    timeout: options.timeout ?? 30,
    input_fields: options.input_fields ?? null,
    tags: options.tags ?? [],
  });
  return handler;
}

/** The file and line that called `fn` (the first stack frame outside this kit). */
function caller(): { url: string; line: number } | null {
  const here = import.meta.url;
  for (const frame of (new Error().stack || "").split("\n").slice(1)) {
    const match = frame.match(/(file:\/\/[^\s()]+):(\d+):\d+/);
    if (match && match[1] !== here) return { url: match[1], line: Number(match[2]) };
  }
  return null;
}

// ── values across the boundary (the Python kit's tagged JSON) ─────────────

export function encode(value: unknown): unknown {
  if (value === null || value === undefined) return null;
  if (value instanceof Date) return { $dt: value.toISOString() };
  if (value instanceof Uint8Array) return { $b64: btoa(String.fromCharCode(...value)) };
  if (value instanceof Set) return { $set: [...value].map(encode) };
  if (Array.isArray(value)) return value.map(encode);
  if (typeof value === "object") return Object.fromEntries(Object.entries(value as object).map(([k, v]) => [k, encode(v)]));
  if (typeof value === "bigint") return value.toString();
  return value;
}

export function decode(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(decode);
  if (value && typeof value === "object") {
    const entries = Object.entries(value as object);
    if (entries.length === 1) {
      const [tag, inner] = entries[0];
      if (tag === "$b64") return Uint8Array.from(atob(inner as string), (c) => c.charCodeAt(0));
      if (tag === "$dt" || tag === "$date" || tag === "$dec") return inner; // ISO text, exact decimal text
      if (tag === "$set") return (inner as unknown[]).map(decode);
    }
    return Object.fromEntries(entries.map(([k, v]) => [k, decode(v)]));
  }
  return value;
}

// ── ctx.runtime ───────────────────────────────────────────────────────────

export type Call = (method: string, args: unknown[], kwargs: Record<string, unknown>) => Promise<unknown>;

/** `ctx.runtime`: one method per platform capability. `call` reaches any runtime method by its Python name. */
export class Runtime {
  #call: Call;
  constructor(call: Call) {
    this.#call = call;
  }
  call(method: string, args: unknown[] = [], kwargs: Record<string, unknown> = {}) {
    return this.#call(method, args, kwargs);
  }
  // data
  resourceList(resource: string, options: { filters?: unknown; sort?: string; limit?: number; offset?: number } = {}) {
    return this.call("resource_list", [resource], options);
  }
  resourceGet(resource: string, id: unknown) { return this.call("resource_get", [resource, id]); }
  resourceCreate(resource: string, data: Record<string, unknown>) { return this.call("resource_create", [resource, data]); }
  resourceUpdate(resource: string, id: unknown, data: Record<string, unknown>) { return this.call("resource_update", [resource, id, data]); }
  resourceDelete(resource: string, id: unknown) { return this.call("resource_delete", [resource, id]); }
  dbQuery(sql: string, params: unknown[] = []) { return this.call("db_query", [sql, params]); }
  dbTransaction(operations: Record<string, unknown>[]) { return this.call("db_transaction", [operations]); }
  // cache
  cacheGet(key: string) { return this.call("cache_get", [key]); }
  cacheSet(key: string, value: unknown, options: { ttl?: number; tags?: string[] } = {}) { return this.call("cache_set", [key, value], options); }
  cacheDelete(key: string) { return this.call("cache_delete", [key]); }
  cacheInvalidate(tags: string[]) { return this.call("cache_invalidate", [tags]); }
  // automation
  emit(name: string, payload: unknown = {}) { return this.call("emit", [name, payload]); }
  callFlow(flow: string, input: unknown = {}) { return this.call("call_flow", [flow, input]); }
  dispatchFlow(flow: string, input: unknown = {}, options: { delay?: number; queue?: string } = {}) { return this.call("dispatch_flow", [flow, input], options); }
  callFunction(name: string, input: unknown = {}) { return this.call("call_function", [name, input]); }
  dispatchFunction(name: string, input: unknown = {}, options: { delay?: number; queue?: string } = {}) { return this.call("dispatch_function", [name, input], options); }
  webhookSend(event: string, payload: unknown = {}) { return this.call("webhook_send", [event, payload]); }
  publish(channel: string, event: string, payload: unknown = {}) { return this.call("publish", [channel, event, payload]); }
  sendMail(to: string | string[], subject: string, options: { text?: string; html?: string; template?: string; data?: unknown } = {}) {
    return this.call("send_mail", [to, subject], options);
  }
  // storage
  storagePut(bucket: string, key: string, content: Uint8Array | string, contentType = "") {
    return this.call("storage_put", [bucket, key, typeof content === "string" ? new TextEncoder().encode(content) : content, contentType]);
  }
  storageRead(bucket: string, key: string, limit?: number) { return this.call("storage_read", limit ? [bucket, key, limit] : [bucket, key]); }
  storageSignedUrl(bucket: string, key: string, method = "GET", expiresIn = 300) { return this.call("storage_signed_url", [bucket, key, method, expiresIn]); }
  storageDelete(bucket: string, key: string) { return this.call("storage_delete", [bucket, key]); }
  // platform
  secret(name: string) { return this.call("secret", [name]) as Promise<string | null>; }
  identityUser(userId: string) { return this.call("identity_user", [userId]); }
  checkPolicy(ref: unknown, context: Record<string, unknown>) { return this.call("check_policy", [ref, context]); }
  metric(name: string, value = 1, tags?: Record<string, string>) { return this.call("metric", [name, value, tags ?? null]); }
  /** Outbound HTTP, made by the platform through its guard (private addresses are refused). */
  httpRequest(method: string, url: string, options: { headers?: Record<string, string>; json?: unknown; params?: Record<string, string>; timeout?: number; retries?: number } = {}) {
    return this.call("http_request", [method, url], options);
  }
}

export { registry };
