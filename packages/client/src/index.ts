export type Json = string | number | boolean | null | Json[] | { [key: string]: Json };
export type Database = Record<string, { Row: Record<string, unknown>; Insert: Record<string, unknown>; Update: Record<string, unknown> }>;
export type Row<D extends Database, T extends keyof D> = D[T]["Row"];
export type Insert<D extends Database, T extends keyof D> = D[T]["Insert"];
export type Update<D extends Database, T extends keyof D> = D[T]["Update"];

export class PawabaseError extends Error {
  constructor(public readonly status: number, public readonly code: string | undefined, public readonly details: unknown, message?: string) { super(message ?? `Pawabase request failed (${status})`); this.name = "PawabaseError"; }
}
export interface Session { access_token: string; refresh_token: string; expires_in: number; user: Record<string, unknown>; [key: string]: unknown }
export interface TokenStore { get(): Session | null | Promise<Session | null>; set(session: Session): void | Promise<void>; clear(): void | Promise<void> }
export interface ClientOptions { url: string; apiKey: string; fetch?: typeof fetch; tokenStore?: TokenStore; headers?: HeadersInit }
export interface ListOptions { filter?: Record<string, string | number | boolean>; sort?: string | string[]; limit?: number; offset?: number; expand?: string | string[] }
export interface Page<T> { data: T[]; total: number; limit: number; offset: number }

const memoryStore = (): TokenStore => { let value: Session | null = null; return { get: () => value, set: (v) => { value = v; }, clear: () => { value = null; } }; };
const path = (value: string) => value.split("/").map(encodeURIComponent).join("/");
const query = (options: ListOptions = {}) => { const p = new URLSearchParams(); for (const [key, value] of Object.entries(options.filter ?? {})) p.append(`filter[${key}]`, String(value)); for (const value of ([] as string[]).concat(options.sort ?? [])) p.append("sort", value); for (const value of ([] as string[]).concat(options.expand ?? [])) p.append("expand", value); if (options.limit !== undefined) p.set("limit", String(options.limit)); if (options.offset !== undefined) p.set("offset", String(options.offset)); const result = p.toString(); return result ? `?${result}` : ""; };

export function createClient<D extends Database = Database>(options: ClientOptions) {
  const base = options.url.replace(/\/$/, ""); const store = options.tokenStore ?? memoryStore(); const transport = options.fetch ?? globalThis.fetch;
  if (!transport) throw new Error("Provide fetch when it is not available globally.");
  let refresh: Promise<Session> | undefined;
  async function request<T>(method: string, target: string, body?: unknown, retry = true, extra?: HeadersInit): Promise<T> {
    const session = await store.get(); const headers = new Headers(options.headers); headers.set("apikey", options.apiKey); headers.set("accept", "application/json");
    if (body !== undefined) headers.set("content-type", "application/json"); if (session?.access_token) headers.set("authorization", `Bearer ${session.access_token}`); new Headers(extra).forEach((v, k) => headers.set(k, v));
    let response = await transport(`${base}${target}`, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
    if (response.status === 401 && retry && session?.refresh_token && !target.startsWith("/auth/v1/token")) { await auth.refresh(); return request(method, target, body, false, extra); }
    if (!response.ok) { let detail: any; try { detail = await response.json(); } catch { detail = await response.text(); } throw new PawabaseError(response.status, detail?.code, detail, detail?.message ?? detail?.detail); }
    if (response.status === 204) return undefined as T; const text = await response.text(); return (text ? JSON.parse(text) : undefined) as T;
  }
  const auth = {
    async signUp(email: string, password: string) { const s = await request<Session>("POST", "/auth/v1/signup", { email, password }); await store.set(s); return s; },
    async signIn(email: string, password: string) { const s = await request<Session>("POST", "/auth/v1/token?grant_type=password", { email, password }); await store.set(s); return s; },
    async refresh() { if (!refresh) refresh = (async () => { const current = await store.get(); if (!current?.refresh_token) throw new PawabaseError(401, "no_session", null, "No refresh token is available."); try { const s = await request<Session>("POST", "/auth/v1/token", { grant_type: "refresh_token", refresh_token: current.refresh_token }, false); await store.set(s); return s; } catch (error) { await store.clear(); throw error; } finally { refresh = undefined; } })(); return refresh; },
    async signOut(scope: "local" | "others" | "global" = "local") { await request<void>("POST", "/auth/v1/logout", { scope }, false); await store.clear(); },
    getSession: () => store.get()
  };
  return {
    auth,
    from<T extends keyof D & string>(table: T) { const root = `/rest/v1/${path(table)}`; return {
      list: (options?: ListOptions) => request<Page<Row<D, T>>>("GET", root + query(options)),
      get: (id: string | number, options?: Pick<ListOptions, "expand">) => request<Row<D, T>>("GET", `${root}/${encodeURIComponent(String(id))}` + query(options)),
      create: (value: Insert<D, T>, opts?: { idempotencyKey?: string }) => request<Row<D, T>>("POST", root, value, true, opts?.idempotencyKey ? { "Idempotency-Key": opts.idempotencyKey } : undefined),
      update: (id: string | number, value: Update<D, T>) => request<Row<D, T>>("PATCH", `${root}/${encodeURIComponent(String(id))}`, value),
      remove: (id: string | number) => request<void>("DELETE", `${root}/${encodeURIComponent(String(id))}`)
    }; },
    flow<TOutput = unknown, TInput = Record<string, unknown>>(name: string, input: TInput) { return request<TOutput>("POST", `/flows/v1/${path(name)}`, input); },
    function<TOutput = unknown, TInput = Record<string, unknown>>(name: string, input: TInput) { return request<{ data: TOutput }>("POST", `/functions/v1/${path(name)}`, input).then((x) => x.data); },
    storage: {
      download: async (bucket: string, key: string) => { const session = await store.get(); const headers = new Headers({ apikey: options.apiKey }); if (session) headers.set("authorization", `Bearer ${session.access_token}`); const response = await transport(`${base}/storage/v1/${path(bucket)}/${path(key)}`, { headers }); if (!response.ok) throw new PawabaseError(response.status, undefined, await response.text()); return response.blob(); },
      publicUrl: (bucket: string, key: string) => `${base}/storage/v1/${path(bucket)}/${path(key)}`
    }
  };
}
