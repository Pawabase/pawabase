// Calls from the browser go to Studio's bridge, never to the services
// directly: /studio/api/platform → API, /auth → Akountz, /realtime → Angula.
import { useCallback, useEffect, useState } from "react";

function xsrf() {
  const match = document.cookie.match(/(?:^|; )XSRF-TOKEN=([^;]*)/);
  return match ? decodeURIComponent(match[1]) : "";
}

function workingBranch(path, service) {
  if (service !== "platform" || !path.startsWith("/envs/")) return undefined;
  const match = path.match(/^\/envs\/([^/]+)/);
  if (!match) return undefined;
  try { return localStorage.getItem(`pawabase.branch.${match[1]}`) || "main"; } catch { return "main"; }
}

export class ApiError extends Error {
  constructor(status, body) {
    super(describe(body) || `Request failed (${status})`);
    this.status = status;
    this.body = body;
  }
}

function describe(body) {
  if (!body) return "";
  if (typeof body === "string") return body;
  // A failure the API explains: what broke and where in your code, what to try, and the request to look up. Never a traceback.
  if (body.failure) {
    const f = body.failure;
    const where = f.where && f.where[0] ? ` at ${f.where[0]}` : "";
    return [`${f.kind}: ${f.message}${where}.`, f.hint, body.request_id && `Request ${body.request_id}.`].filter(Boolean).join(" ");
  }
  if (body.hint && body.message) return `${body.message} ${body.hint}`;
  const detail = body.detail ?? body.message ?? body.error;
  if (Array.isArray(detail)) return detail.map((d) => d.msg || JSON.stringify(d)).join("; ");
  if (detail && typeof detail === "object") return JSON.stringify(detail);
  return detail || "";
}

export async function api(method, path, body, { service = "platform", params } = {}) {
  const allParams = { ...(params || {}), ...(workingBranch(path, service) ? { branch: workingBranch(path, service) } : {}) };
  const query = Object.keys(allParams).length ? "?" + new URLSearchParams(Object.entries(allParams).filter(([, v]) => v !== undefined && v !== null && v !== "")) : "";
  const response = await fetch(`/studio/api/${service}${path}${query}`, {
    method,
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", Accept: "application/json", "X-XSRF-TOKEN": xsrf() },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await response.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = text;
  }
  if (!response.ok) throw new ApiError(response.status, data);
  return data;
}

export const get = (path, opts) => api("GET", path, undefined, opts);
export const post = (path, body, opts) => api("POST", path, body ?? {}, opts);
export const put = (path, body, opts) => api("PUT", path, body, opts);
export const patch = (path, body, opts) => api("PATCH", path, body, opts);
export const del = (path, opts) => api("DELETE", path, undefined, opts);

/** Load something from the bridge; `reload()` fetches it again. */
export function useApi(path, { service, params, interval } = {}) {
  const [state, setState] = useState({ data: null, error: null, loading: true });
  const key = JSON.stringify([path, service, params]);
  const load = useCallback(async () => {
    if (!path) return;
    try {
      const data = await get(path, { service, params });
      setState({ data, error: null, loading: false });
    } catch (error) {
      setState((s) => ({ ...s, error, loading: false }));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  useEffect(() => {
    setState((s) => ({ ...s, loading: true }));
    load();
    if (!interval) return undefined;
    const timer = setInterval(load, interval);
    return () => clearInterval(timer);
  }, [load, interval]);
  return { ...state, reload: load };
}

export function envPath(env, rest = "") {
  return `/envs/${env}${rest}`;
}
