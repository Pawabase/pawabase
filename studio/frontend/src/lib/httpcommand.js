// The terminal's `http` command: try an endpoint of your own API from where you manage it.
//
//   http GET /rest/v1/todos --query {"limit":5} --headers {"x-trace":"1"} --expect 200
//   http POST /rest/v1/todos --body {"title":"x"} --as user --confirm
//   http /health --save ping        then        http run ping
//   login ada@example.com secret    whoami    logout
//
// Parsing and formatting are pure functions, so they can be checked without a browser; `runHttp` is the only part that talks to Studio.

export const METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"];
const SAFE = new Set(["GET"]);
const PUBLIC_PREFIXES = ["/functions/v1/", "/flows/v1/", "/auth/v1/", "/storage/v1/", "/health/v1", "/docs/v1/", "/hooks/v1/"];
const VALUE_FLAGS = new Set(["query", "headers", "body", "as", "expect", "save"]);
const SWITCHES = new Set(["raw", "confirm", "verbose", "v"]);
const STATUS = { 200: "OK", 201: "Created", 202: "Accepted", 204: "No Content", 301: "Moved", 302: "Found", 304: "Not Modified", 400: "Bad Request", 401: "Unauthorized", 403: "Forbidden", 404: "Not Found", 405: "Method Not Allowed", 409: "Conflict", 413: "Payload Too Large", 422: "Unprocessable", 429: "Too Many Requests", 500: "Server Error", 502: "Bad Gateway", 503: "Unavailable", 504: "Timeout" };

/** Split a command line the way a person types it: quotes group words, and a JSON object or array is one argument even with spaces in it. */
export function tokenize(line) {
  const tokens = [];
  let current = "";
  let quote = null;
  let depth = 0;
  let started = false;
  for (let i = 0; i < line.length; i += 1) {
    const ch = line[i];
    if (quote) {
      if (ch === "\\" && i + 1 < line.length && depth === 0) { current += line[++i]; continue; }
      if (ch === quote) { quote = null; if (depth > 0) current += ch; continue; }
      current += ch;
      continue;
    }
    if (ch === '"' || ch === "'") { quote = ch; started = true; if (depth > 0) current += ch; continue; }
    if (ch === "{" || ch === "[") depth += 1;
    if (ch === "}" || ch === "]") depth = Math.max(0, depth - 1);
    if (/\s/.test(ch) && depth === 0) {
      if (started || current) { tokens.push(current); current = ""; started = false; }
      continue;
    }
    started = true;
    current += ch;
  }
  if (quote) throw new Error("A quote was opened and never closed.");
  if (depth > 0) throw new Error("A { or [ was opened and never closed.");
  if (started || current) tokens.push(current);
  return tokens;
}

/** `{"a":1}`, or `a=1,b=two` / `a=1&b=two`, as an object. */
export function parseMap(text, what) {
  const value = String(text ?? "").trim();
  if (!value) return {};
  if (value.startsWith("{")) {
    let parsed;
    try { parsed = JSON.parse(value); } catch (error) { throw new Error(`${what} is not valid JSON: ${error.message}`); }
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error(`${what} must be an object.`);
    return parsed;
  }
  const out = {};
  for (const pair of value.split(/[,&]/).filter(Boolean)) {
    const at = pair.indexOf("=");
    if (at < 1) throw new Error(`${what}: "${pair}" is not key=value.`);
    out[decodeURIComponent(pair.slice(0, at).trim())] = decodeURIComponent(pair.slice(at + 1).trim());
  }
  return out;
}

/** What a person types as a path, as the public path it means. `/api/health` and `/health` are the health check; a bare `/todos` is a resource. */
export function normalizeEndpoint(path) {
  let value = path.trim();
  if (!value.startsWith("/")) value = `/${value}`;
  if (value.startsWith("/api/")) value = value.slice(4);
  const [bare, query] = value.split("?", 2);
  let out = bare;
  if (out === "/health" || out === "/health/") out = "/health/v1";
  else if (/^\/rest\/v\d+(\/|$)/.test(out) || PUBLIC_PREFIXES.some((p) => out.startsWith(p))) out = bare;
  else if (out === "/rest" || out.startsWith("/rest/")) out = `/rest/v1${out.slice(5)}`;
  else out = `/rest/v1${out}`;
  return { endpoint: out, query: query ? parseMap(query, "the query string") : {} };
}

const ITEM_KEY = "[A-Za-z0-9_.\\-\\[\\]]+";

/** An httpie-style item after the path: `title=hi` (body field), `done:=true` (JSON value), `q==x` (query), `X-Trace:1` (header). */
function parseItem(word) {
  let m = word.match(new RegExp(`^(${ITEM_KEY}):=(.*)$`, "s"));
  if (m) { try { return ["body", m[1], JSON.parse(m[2])]; } catch (error) { throw new Error(`${m[1]}:= is not valid JSON: ${error.message}`); } }
  m = word.match(new RegExp(`^(${ITEM_KEY})==(.*)$`, "s"));
  if (m) return ["query", m[1], m[2]];
  m = word.match(new RegExp(`^(${ITEM_KEY})=(.*)$`, "s"));
  if (m) return ["body", m[1], m[2]];
  m = word.match(/^([A-Za-z][A-Za-z0-9-]*):(.*)$/s);
  if (m) return ["header", m[1], m[2].trim()];
  throw new Error(`"${word}" is not a request item. Use key=value, key:=json, key==query or Header:value.`);
}

/**
 * Parse `http` the way curl or httpie is typed:
 *   http get /todos limit==5
 *   http post /todos title="Buy milk" done:=false
 *   http -X PATCH /todos/1 -H "x-trace: 1" -d '{"done":true}'
 * Method words and flags work in any order; with a body and no method the request is a POST.
 * @returns {{method, endpoint, query, headers, body, as, expect, raw, verbose, save, confirm}}
 */
export function parseHttp(line) {
  const tokens = tokenize(line.replace(/^\s*http\s*/i, ""));
  const flags = {};
  const words = [];
  const headers = {};
  let data;
  let method = "";
  for (let i = 0; i < tokens.length; i += 1) {
    const token = tokens[i];
    const next = (name) => { const value = tokens[++i]; if (value === undefined) throw new Error(`${name} needs a value.`); return value; };
    if (token === "-v") { flags.verbose = true; continue; }
    if (token === "-X") { method = next("-X").toUpperCase(); continue; }
    if (token === "-H") { Object.assign(headers, parseHeader(next("-H"))); continue; }
    if (token === "-d") { data = next("-d"); continue; }
    if (!token.startsWith("--")) { words.push(token); continue; }
    const [rawName, inline] = token.slice(2).split(/=(.*)/s, 2);
    const name = rawName.toLowerCase();
    if (SWITCHES.has(name)) { flags[name === "v" ? "verbose" : name] = true; continue; }
    const alias = { header: "-H", data: "-d", method: "-X", json: "-d" }[name];
    if (alias) {
      const value = inline ?? next(`--${name}`);
      if (alias === "-H") Object.assign(headers, parseHeader(value));
      else if (alias === "-d") data = value;
      else method = value.toUpperCase();
      continue;
    }
    if (!VALUE_FLAGS.has(name)) throw new Error(`Unknown option --${name}. Options: --query --headers --body --as --expect --save --raw --verbose --confirm, or curl's -X -H -d`);
    flags[name] = inline ?? next(`--${name}`);
  }
  if (words.length && METHODS.includes(words[0].toUpperCase())) { if (!method) method = words[0].toUpperCase(); words.shift(); }
  if (!words.length) throw new Error("Usage: http [get|post|put|patch|delete] <path> [key=value …] [-H 'Name: value'] [-d '{json}'] [--query {…}] [--as anon|user] [--expect 200] [--save name] [--confirm]");
  const { endpoint, query: inPath } = normalizeEndpoint(words.shift());
  const query = { ...inPath };
  const fields = {};
  for (const word of words) {
    const [kind, key, value] = parseItem(word);
    if (kind === "query") query[key] = value;
    else if (kind === "header") headers[key] = value;
    else fields[key] = value;
  }
  let body;
  const raw = data ?? flags.body;
  if (raw !== undefined) {
    try { body = JSON.parse(raw); } catch (error) { throw new Error(`The body is not valid JSON: ${error.message}`); }
  }
  if (Object.keys(fields).length) {
    if (body !== undefined && (typeof body !== "object" || Array.isArray(body))) throw new Error("key=value items need an object body.");
    body = { ...(body || {}), ...fields };
  }
  if (!method) method = body !== undefined ? "POST" : "GET";
  if (!METHODS.includes(method)) throw new Error(`Unknown method ${method}. Use ${METHODS.join(", ")}.`);
  if (SAFE.has(method) && body !== undefined) throw new Error("GET has no body. Use post, put, patch or delete.");
  const expect = flags.expect === undefined ? undefined : Number(flags.expect);
  if (flags.expect !== undefined && !(expect >= 100 && expect <= 599)) throw new Error("--expect takes a status code, such as 200.");
  return {
    method, endpoint, body, expect,
    query: { ...query, ...parseMap(flags.query, "--query") },
    headers: { ...parseMap(flags.headers, "--headers"), ...headers },
    as: flags.as || "", raw: !!flags.raw, verbose: !!flags.verbose, save: flags.save || "", confirm: !!flags.confirm,
  };
}

/** `Name: value` (curl's -H) or a JSON object. */
function parseHeader(text) {
  if (text.trim().startsWith("{")) return parseMap(text, "-H");
  const at = text.indexOf(":");
  if (at < 1) throw new Error(`Header "${text}" should look like Name: value.`);
  return { [text.slice(0, at).trim()]: text.slice(at + 1).trim() };
}

const human = (bytes) => (bytes < 1024 ? `${bytes} B` : `${(bytes / 1024).toFixed(1)} KB`);

export function formatResponse(result, request, { elapsed } = {}) {
  const status = result.status;
  const head = [`${status} ${STATUS[status] || ""}`.trim(), `${Math.round(result.duration_ms ?? elapsed ?? 0)} ms`];
  if (result.size) head.push(human(result.size));
  const lines = [head.join("  ·  "), `${request.method} ${request.endpoint}${Object.keys(request.query).length ? `?${new URLSearchParams(request.query)}` : ""}`];
  const headers = result.headers || {};
  const shown = request.verbose ? Object.entries(headers) : Object.entries(headers).filter(([k]) => ["content-type", "x-request-id", "retry-after", "location"].includes(k.toLowerCase()));
  for (const [key, value] of shown) lines.push(`${key}: ${value}`);
  lines.push("");
  const body = result.body;
  if (body === null || body === undefined || body === "") lines.push("(empty body)");
  else if (typeof body === "string") lines.push(body);
  else {
    const text = request.raw ? JSON.stringify(body) : JSON.stringify(body, null, 2);
    lines.push(text.length > 6000 ? `${text.slice(0, 6000)}\n… ${text.length - 6000} more characters (use --raw, or query less)` : text);
  }
  return lines.join("\n");
}

/** The payload of a JWT, without verifying it: enough to say who a stored token is for and when it ends. */
export function tokenInfo(token) {
  try {
    const payload = JSON.parse(atob(token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/")));
    return { email: payload.email, id: payload.sub, expires: payload.exp ? new Date(payload.exp * 1000) : null };
  } catch {
    return null;
  }
}

function store(kind, env) {
  const key = `pawabase.terminal.${kind}.${env}`;
  const area = kind === "token" ? sessionStorage : localStorage;
  return {
    read: (fallback) => { try { const v = area.getItem(key); return v === null ? fallback : JSON.parse(v); } catch { return fallback; } },
    write: (value) => { try { area.setItem(key, JSON.stringify(value)); } catch { /* kept for this page only */ } },
    clear: () => { try { area.removeItem(key); } catch { /* nothing to clear */ } },
  };
}

/**
 * Run `http`, `login`, `logout` or `whoami`. `post` is Studio's bridge client. Returns text for the terminal; throws an Error to show as a failure.
 */
export async function runHttp(line, { env, post }) {
  const tokens = tokenize(line);
  const command = tokens[0].toLowerCase();
  const tokenStore = store("token", env);
  const saved = store("saved", env);

  if (command === "logout") { tokenStore.clear(); return "Signed out of the terminal's user."; }
  if (command === "whoami") {
    const token = tokenStore.read("");
    if (!token) return "Not signed in. Requests are made as an anonymous caller. Use: login <email> <password>";
    const info = tokenInfo(token);
    return info ? `Signed in as ${info.email || info.id}${info.expires ? `, token valid until ${info.expires.toLocaleTimeString()}` : ""}` : "A token is stored (it could not be read).";
  }
  if (command === "login") {
    const [, email, password] = tokens;
    if (!email || !password) throw new Error("Usage: login <email> <password>");
    const result = await post("/sign-in", { env, email, password }, { service: "explorer" });
    if (result.mfa_required) throw new Error("This account has two-step verification. Sign in through the API Explorer, which handles the code.");
    if (!result.access_token) throw new Error("Sign-in did not return a token.");
    tokenStore.write(result.access_token);
    return `Signed in as ${email}. Requests use this user until you run logout.`;
  }

  // http ...
  if (tokens[1]?.toLowerCase() === "saved") {
    const all = saved.read({});
    const names = Object.keys(all);
    return names.length ? names.map((n) => `${n.padEnd(18)} ${all[n].method} ${all[n].endpoint}`).join("\n") : "Nothing saved. Add --save <name> to a request.";
  }
  let request;
  if (tokens[1]?.toLowerCase() === "run") {
    const name = tokens[2];
    const all = saved.read({});
    if (!all[name]) throw new Error(`No saved request "${name}". Run: http saved`);
    request = { ...all[name], confirm: tokens.includes("--confirm"), verbose: tokens.includes("--verbose") || tokens.includes("-v") };
  } else {
    request = parseHttp(line);
  }
  if (!SAFE.has(request.method) && !request.confirm) throw new Error(`${request.method} changes data. Run it again with --confirm.`);

  let accessToken = "";
  if (request.as === "anon") accessToken = "";
  else if (request.as.startsWith("user:")) accessToken = request.as.slice(5);
  else if (request.as && request.as !== "user") accessToken = request.as; // a bare token
  else accessToken = tokenStore.read("");
  if (request.as === "user" && !accessToken) throw new Error("--as user needs a signed-in user: login <email> <password>");

  if (request.save) {
    const all = saved.read({});
    all[request.save] = { method: request.method, endpoint: request.endpoint, query: request.query, headers: request.headers, body: request.body, as: request.as === "user" || request.as === "anon" ? request.as : "", expect: request.expect, raw: request.raw };
    saved.write(all);
  }

  const result = await post("/request", {
    env, method: request.method, endpoint: request.endpoint, query: request.query, headers: request.headers, body: request.body, access_token: accessToken,
  }, { service: "explorer" });
  let text = formatResponse(result, request);
  if (request.save) text += `\n\nSaved as "${request.save}". Run it again with: http run ${request.save}`;
  if (request.expect !== undefined) {
    const ok = result.status === request.expect;
    text += `\n${ok ? "✓" : "✗"} expected ${request.expect}, got ${result.status}`;
    if (!ok) throw new Error(text);
  }
  return text;
}
