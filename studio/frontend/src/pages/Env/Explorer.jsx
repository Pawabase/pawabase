import { useEffect, useMemo, useState } from "react";
import { usePage } from "@inertiajs/react";
import Layout from "../../components/Layout";
import { Badge, Button, Card, Empty, ErrorNote, Field, Json, JsonInput, Loading, PageHead } from "../../components/ui";
import { Icon } from "../../components/icons";
import { api, envPath, post, useApi } from "../../lib/api";

const METHODS = ["get", "post", "put", "patch", "delete", "head"];
const METHOD_TONES = { get: "green", head: "green", post: "blue", put: "yellow", patch: "yellow", delete: "red" };
/** The groups a person picks from, in order: what each holds, and its icon. */
const GROUPS = [
  ["Resources", "database", "Read and write your data: list, create, update and delete records."],
  ["Application routes", "routes", "Your own HTTP routes and the flows behind them."],
  ["Authentication", "users", "Sign up, sign in, tokens, sessions and second factors for your users."],
  ["Functions", "functions", "Call your deployed functions over HTTP."],
  ["Flows", "flows", "Run flows directly."],
  ["Storage", "storage", "Upload, download, sign and share files in buckets."],
  ["Realtime", "realtime", "Publish to channels and read their history."],
  ["Webhooks", "webhooks", "Inbound hooks other services call."],
  ["Events", "events", "Emit and inspect events."],
  ["User administration", "keys", "Manage users with a secret key: create, update, disable, roles."],
  ["Management", "settings", "Configure this environment: resources, policies, keys, schedules, webhooks and more."],
  ["System", "status", "Health and status."],
  ["Other", "braces", "Everything else."],
];
const GROUP_ORDER = GROUPS.map(([title]) => title);
const GROUP_META = Object.fromEntries(GROUPS.map(([title, icon, about]) => [title, { icon, about }]));
/** Path parameters Studio fills from where you are, so nobody has to type them. */
const CONTEXT_PARAMETERS = new Set(["env", "environment", "version"]);
const isContext = (parameter) => parameter.in === "path" && CONTEXT_PARAMETERS.has(parameter.name);

/** The part of a group an endpoint belongs to, for sub-headings: the resource, the auth area, the management section. */
function areaFor(path) {
  const parts = path.split("/").filter(Boolean);
  if (parts[0] === "platform") {
    const at = parts.indexOf("{env}");
    return at >= 0 ? (parts[at + 1] || "environment").replace(/-/g, " ") : "installation";
  }
  const rest = parts.slice(2).filter((part) => !part.startsWith("{"));
  return (rest[0] || parts[0] || "general").replace(/-/g, " ");
}

const titleCase = (text) => text.charAt(0).toUpperCase() + text.slice(1);

function resolve(document, value) {
  if (!value?.$ref) return value || {};
  return value.$ref.replace(/^#\//, "").split("/").reduce((current, key) => current?.[key.replace(/~1/g, "/").replace(/~0/g, "~")], document) || {};
}

function schemaOf(document, raw) {
  const schema = resolve(document, raw);
  if (schema.allOf?.length) return Object.assign({}, ...schema.allOf.map((item) => schemaOf(document, item)));
  if (schema.oneOf?.length || schema.anyOf?.length) return schemaOf(document, (schema.oneOf || schema.anyOf)[0]);
  return schema;
}

function sampleFor(document, raw, depth = 0) {
  if (depth > 6) return null;
  const schema = schemaOf(document, raw);
  if (schema.example !== undefined) return schema.example;
  if (schema.examples?.length) return schema.examples[0];
  if (schema.default !== undefined) return schema.default;
  if (schema.enum?.length) return schema.enum[0];
  if (schema.type === "array") return [sampleFor(document, schema.items, depth + 1)];
  if (schema.type === "object" || schema.properties) return Object.fromEntries(Object.entries(schema.properties || {}).map(([key, value]) => [key, sampleFor(document, value, depth + 1)]));
  if (schema.type === "integer" || schema.type === "number") return schema.minimum ?? 0;
  if (schema.type === "boolean") return false;
  if (schema.format === "email") return "user@example.com";
  if (schema.format === "date-time") return new Date().toISOString();
  if (schema.format === "date") return new Date().toISOString().slice(0, 10);
  if (schema.format === "uuid") return "00000000-0000-4000-8000-000000000000";
  return "";
}

function categoryFor(path, operation, source) {
  const tags = (operation.tags || []).map((tag) => tag.toLowerCase());
  if (path.startsWith("/rest/")) return tags.some((tag) => tag === "routes") || String(operation.operationId || "").startsWith("route.") ? "Application routes" : "Resources";
  if (path.startsWith("/auth/")) return "Authentication";
  if (path.startsWith("/functions/")) return "Functions";
  if (path.startsWith("/flows/")) return "Flows";
  if (path.startsWith("/storage/") || path.startsWith("/files/")) return "Storage";
  if (path.startsWith("/realtime/")) return "Realtime";
  if (path.startsWith("/hooks/")) return "Webhooks";
  if (path.startsWith("/admin/")) return "User administration";
  if (tags.some((tag) => tag === "events" || tag === "event subscriptions")) return "Events";
  if (path.startsWith("/platform/")) return "Management";
  if (path.includes("health") || path.includes("status")) return "System";
  if (source === "application") return "Application routes";
  return "Other";
}

function operations(catalog) {
  return (catalog?.documents || []).flatMap((entry) => {
    const document = entry.document || {};
    return Object.entries(document.paths || {}).flatMap(([path, rawPathItem]) => {
      const pathItem = resolve(document, rawPathItem);
      return METHODS.filter((method) => pathItem[method]).map((method) => {
        const operation = resolve(document, pathItem[method]);
        const parameters = [...(pathItem.parameters || []), ...(operation.parameters || [])].map((item) => resolve(document, item));
        return {
          id: `${entry.id}:${method}:${path}`,
          source: entry.id,
          sourceTitle: entry.title,
          document,
          method,
          path,
          operation,
          parameters: [...new Map(parameters.map((item) => [`${item.in}:${item.name}`, item])).values()],
          category: categoryFor(path, operation, entry.id),
          area: areaFor(path),
          label: operation.summary || operation.operationId || `${method.toUpperCase()} ${path}`,
        };
      });
    });
  });
}

function initialParameter(parameter, document, env, version) {
  if (parameter.name === "env") return env;
  if (parameter.name === "environment" && parameter.in === "path") return env;
  if (parameter.name === "version") return version;
  const schema = schemaOf(document, parameter.schema);
  const value = parameter.example ?? schema.example ?? schema.default ?? schema.enum?.[0];
  return value === undefined ? "" : value;
}

function coerce(value, schema) {
  if (value === "") return value;
  if (schema.type === "integer") return Number.parseInt(value, 10);
  if (schema.type === "number") return Number(value);
  if (schema.type === "boolean") return value === true || value === "true";
  if (schema.type === "array") return Array.isArray(value) ? value : String(value).split(",").map((part) => part.trim()).filter(Boolean);
  return value;
}

function requestBodyFor(selected, contentType) {
  const body = resolve(selected.document, selected.operation.requestBody);
  return resolve(selected.document, body.content?.[contentType] || {});
}

function preferredContentType(selected) {
  const content = resolve(selected.document, selected.operation.requestBody).content || {};
  return Object.keys(content).find((type) => type.includes("json")) || Object.keys(content)[0] || "";
}

function fillPath(path, parameters, values) {
  let result = path;
  for (const parameter of parameters.filter((item) => item.in === "path")) result = result.replace(`{${parameter.name}}`, encodeURIComponent(values[parameter.name] ?? ""));
  return result;
}

function fileBase64(file) {
  return new Promise((resolveValue, reject) => {
    const reader = new FileReader();
    reader.onerror = reject;
    reader.onload = () => resolveValue(String(reader.result || "").split(",")[1] || "");
    reader.readAsDataURL(file);
  });
}

function securityLabel(selected) {
  const security = selected.operation.security ?? selected.document.security ?? [];
  const names = [...new Set(security.flatMap((item) => Object.keys(item)))];
  return names.length ? names.join(" or ") : "Public";
}

export default function Explorer({ env }) {
  const { gateway_url: gatewayUrl } = usePage().props;
  const base = envPath(env);
  const versionState = useApi(`${base}/api-versions`);
  const [version, setVersion] = useState("v1");
  const catalogState = useApi("/openapi", { service: "explorer", params: { env, version } });
  const [search, setSearch] = useState("");
  const query = () => new URLSearchParams(window.location.search);
  const [selectedId, setSelectedState] = useState(() => query().get("op") || "");
  const setSelectedId = (id) => {
    setSelectedState(id);
    const target = new URL(window.location.href);
    id ? target.searchParams.set("op", id) : target.searchParams.delete("op");
    window.history.pushState(window.history.state, "", `${target.pathname}${target.search}`);
  };
  useEffect(() => {
    const back = () => setSelectedState(query().get("op") || "");
    window.addEventListener("popstate", back);
    return () => window.removeEventListener("popstate", back);
  }, []);
  const [values, setValues] = useState({ path: {}, query: {}, headers: {}, body: undefined });
  const [contentType, setContentType] = useState("");
  const [file, setFile] = useState(null);
  const [customHeaders, setCustomHeaders] = useState([]);
  const [apiKey, setApiKey] = useState("");
  const [token, setToken] = useState("");
  const [showToken, setShowToken] = useState(false);
  const [credentials, setCredentials] = useState({ email: "", password: "", code: "", mfa_token: "" });
  const [authBusy, setAuthBusy] = useState(false);
  const [authMessage, setAuthMessage] = useState("");
  const [response, setResponse] = useState(null);
  const [error, setError] = useState(null);
  const [sending, setSending] = useState(false);

  const versionNames = useMemo(() => {
    const names = (versionState.data?.data || []).filter((item) => item.status === "active" || item.name === "v1").map((item) => item.name);
    return [...new Set(["v1", ...names])].sort((a, b) => Number(a.slice(1)) - Number(b.slice(1)));
  }, [versionState.data]);
  const all = useMemo(() => operations(catalogState.data), [catalogState.data]);
  const filtered = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return needle ? all.filter((item) => `${item.method} ${item.path} ${item.category} ${item.operation.summary || ""} ${(item.operation.tags || []).join(" ")}`.toLowerCase().includes(needle)) : all;
  }, [all, search]);
  const selected = all.find((item) => item.id === selectedId) || filtered[0] || null;

  useEffect(() => {
    setResponse(null);
  }, [version]);

  useEffect(() => {
    if (!selected) return;
    const next = { path: {}, query: {}, headers: {}, body: undefined };
    for (const parameter of selected.parameters) {
      const location = parameter.in === "header" ? "headers" : parameter.in;
      if (next[location]) next[location][parameter.name] = initialParameter(parameter, selected.document, env, version);
    }
    const type = preferredContentType(selected);
    if (type) {
      const media = requestBodyFor(selected, type);
      const examples = Object.values(media.examples || {});
      next.body = media.example ?? examples[0]?.value ?? sampleFor(selected.document, media.schema);
    }
    setValues(next);
    setContentType(type);
    setFile(null);
    setCustomHeaders([]);
    setResponse(null);
    setError(null);
  }, [selected?.id, env, version]); // eslint-disable-line react-hooks/exhaustive-deps

  const setParameter = (location, name, value) => setValues((current) => ({ ...current, [location]: { ...current[location], [name]: value } }));
  const builtPath = selected ? fillPath(selected.path, selected.parameters, values.path) : "";
  const queryValues = selected ? Object.fromEntries(selected.parameters.filter((item) => item.in === "query" && values.query[item.name] !== "").map((item) => [item.name, coerce(values.query[item.name], schemaOf(selected.document, item.schema))])) : {};
  const declaredHeaders = selected ? Object.fromEntries(selected.parameters.filter((item) => item.in === "header" && values.headers[item.name] !== "" && !["apikey", "authorization"].includes(item.name.toLowerCase())).map((item) => [item.name, String(values.headers[item.name])])) : {};
  const headers = { ...declaredHeaders, ...Object.fromEntries(customHeaders.filter((item) => item.name && item.value).map((item) => [item.name, item.value])) };
  const requestUrl = builtPath ? `${gatewayUrl || ""}${builtPath}${Object.keys(queryValues).length ? `?${new URLSearchParams(queryValues).toString()}` : ""}` : "";
  const bodyPresent = values.body !== undefined || Boolean(file);
  const managementRequest = selected?.path.startsWith("/platform/v1/") || selected?.path.startsWith("/admin/v1/");
  const curl = selected ? [
    `curl -X ${selected.method.toUpperCase()} '${requestUrl || builtPath}'`,
    !managementRequest && !Object.keys(headers).some((key) => key.toLowerCase() === "apikey") ? `  -H 'apikey: ${apiKey || "<your-api-key>"}'` : null,
    token ? `  -H 'Authorization: Bearer ${token.slice(0, 12)}…'` : null,
    ...Object.entries(headers).map(([key, value]) => `  -H '${key}: ${value}'`),
    contentType ? `  -H 'Content-Type: ${contentType}'` : null,
    values.body !== undefined && !file ? `  --data '${typeof values.body === "string" ? values.body.replace(/'/g, "'\\''") : JSON.stringify(values.body)}'` : null,
    file ? `  --data-binary '@${file.name}'` : null,
  ].filter(Boolean).join(" \\\n") : "";

  const send = async () => {
    if (!selected) return;
    setSending(true);
    setError(null);
    setResponse(null);
    const started = performance.now();
    try {
      let requestBody = values.body;
      let bodyEncoding;
      let actualContentType = contentType;
      if (file) {
        requestBody = await fileBase64(file);
        bodyEncoding = "base64";
        actualContentType = file.type || contentType || "application/octet-stream";
      }
      if (selected.path.startsWith("/platform/v1/") || selected.path.startsWith("/admin/v1/")) {
        const platform = selected.path.startsWith("/platform/v1/");
        const prefix = platform ? "/platform/v1" : "/admin/v1";
        const result = await api(selected.method.toUpperCase(), builtPath.slice(prefix.length), bodyPresent ? requestBody : undefined, { service: platform ? "platform" : "auth", params: queryValues });
        setResponse({ status: 200, duration_ms: Math.round((performance.now() - started) * 100) / 100, headers: {}, body: result });
      } else {
        const result = await post("/request", { env, version, method: selected.method.toUpperCase(), endpoint: builtPath, query: queryValues, headers, body: requestBody, body_present: bodyPresent, body_encoding: bodyEncoding, content_type: actualContentType, access_token: token }, { service: "explorer" });
        setResponse(result);
      }
    } catch (caught) {
      setError(caught);
    } finally {
      setSending(false);
    }
  };

  const signInUser = async () => {
    setAuthBusy(true);
    setAuthMessage("");
    try {
      const result = await post("/sign-in", { env, ...credentials }, { service: "explorer" });
      if (result.mfa_required) {
        setCredentials((current) => ({ ...current, password: "", mfa_token: result.mfa_token }));
        setAuthMessage("Enter the user's MFA code to finish signing in.");
      } else if (result.access_token) {
        setToken(result.access_token);
        setCredentials({ email: credentials.email, password: "", code: "", mfa_token: "" });
        setAuthMessage(`Signed in as ${credentials.email}`);
      }
    } catch (caught) {
      setAuthMessage(caught.message || "Sign-in failed");
    } finally {
      setAuthBusy(false);
    }
  };

  const endpointItem = (item) => ({ key: item.id, label: item.label, sub: item.path, badge: item.method.toUpperCase(), badgeTone: METHOD_TONES[item.method], title: `${item.method.toUpperCase()} ${item.path}` });
  const byArea = (rows) => {
    const areas = [...new Set(rows.map((item) => item.area))];
    return areas.map((area) => ({ title: titleCase(area), items: rows.filter((item) => item.area === area).map(endpointItem) }));
  };
  const searching = search.trim().length > 0;
  // Folders: group → area (a resource, an auth area, a management section) → endpoints.
  const tree = GROUP_ORDER.map((title) => {
    const rows = filtered.filter((item) => item.category === title);
    const areas = [...new Set(rows.map((item) => item.area))];
    const single = areas.length === 1;
    return {
      key: `g:${title}`,
      title,
      icon: GROUP_META[title].icon,
      items: single ? rows.map(endpointItem) : [],
      folders: single ? [] : areas.map((area) => ({ key: `g:${title}:${area}`, title: titleCase(area), items: rows.filter((item) => item.area === area).map(endpointItem) })),
    };
  }).filter((node) => node.items.length || node.folders.length);
  const subnav = {
    title: "API Explorer",
    wide: true,
    loading: catalogState.loading,
    empty: "No matching endpoints.",
    search: { value: search, onChange: setSearch, placeholder: "Search every endpoint…" },
    tree,
    expandAll: searching,
    active: selected?.id,
    onSelect: setSelectedId,
  };
  const visibleParameters = selected ? selected.parameters.filter((item) => !isContext(item)) : [];

  return (
    <Layout title="API Explorer" subnav={subnav}>
      <div className="ex-context">
        <span className="ex-context-chip" title="Requests go to the environment you are in. Switch environments from the top bar."><Icon name="networking" size={13} />Environment <b>{env}</b></span>
        {versionNames.length > 1 ? (
          <label className="ex-context-chip">API <select value={version} onChange={(event) => setVersion(event.target.value)}>{versionNames.map((name) => <option key={name}>{name}</option>)}</select></label>
        ) : <span className="ex-context-chip">API <b>{version}</b></span>}
        <span className="ex-context-chip muted">{all.length} endpoints</span>
      </div>
      <Loading state={catalogState}>
        {() => !selected ? (
          <Empty>{searching ? "No endpoint matches your search." : "This API version has no documented endpoints."}</Empty>
        ) : (
          <div className="ex-workbench">
            <section className="ex-operation-head">
              <div className="grow">
                <div className="ex-crumbs"><span>{selected.category}</span><span>›</span><span>{titleCase(selected.area)}</span></div>
                <div className="ex-route"><Badge tone={METHOD_TONES[selected.method]}>{selected.method.toUpperCase()}</Badge><code>{selected.path}</code></div>
                <h2>{selected.operation.summary || selected.operation.operationId || "API request"}</h2>
                {selected.operation.description && <p className="muted">{selected.operation.description}</p>}
                <div className="row wrap ex-labels"><Badge>{selected.category}</Badge><Badge>{selected.sourceTitle}</Badge><Badge tone={securityLabel(selected) === "Public" ? "green" : "brand"}>{securityLabel(selected)}</Badge></div>
              </div>
              <Button variant="primary" disabled={sending || visibleParameters.some((item) => item.in === "path" && item.required && !values.path[item.name])} onClick={send}>{sending ? "Sending…" : "Send request"}</Button>
            </section>

            <div className="ex-columns">
              <div className="ex-builder">
                {visibleParameters.some((item) => item.in === "path") && <ParameterSection title="Path parameters" parameters={visibleParameters.filter((item) => item.in === "path")} values={values.path} document={selected.document} onChange={(name, value) => setParameter("path", name, value)} />}
                {selected.parameters.some((item) => item.in === "query") && <ParameterSection title="Query parameters" parameters={selected.parameters.filter((item) => item.in === "query")} values={values.query} document={selected.document} onChange={(name, value) => setParameter("query", name, value)} />}
                {selected.parameters.some((item) => item.in === "header" && !["apikey", "authorization"].includes(item.name.toLowerCase())) && <ParameterSection title="Documented headers" parameters={selected.parameters.filter((item) => item.in === "header" && !["apikey", "authorization"].includes(item.name.toLowerCase()))} values={values.headers} document={selected.document} onChange={(name, value) => setParameter("headers", name, value)} />}

                <Card title="Headers" actions={<Button size="sm" onClick={() => setCustomHeaders((items) => [...items, { id: crypto.randomUUID(), name: "", value: "" }])}><Icon name="plus" size={13} /> Add header</Button>}>
                  {customHeaders.length === 0 ? <p className="muted ex-inline-empty">No additional headers. Authorization and API context are supplied securely by Studio.</p> : <div className="ex-header-list">{customHeaders.map((header) => <div className="ex-header-row" key={header.id}><input aria-label="Header name" placeholder="X-Request-Id" value={header.name} onChange={(event) => setCustomHeaders((items) => items.map((item) => item.id === header.id ? { ...item, name: event.target.value } : item))} /><input aria-label="Header value" placeholder="value" value={header.value} onChange={(event) => setCustomHeaders((items) => items.map((item) => item.id === header.id ? { ...item, value: event.target.value } : item))} /><Button className="icon-only" aria-label="Remove header" onClick={() => setCustomHeaders((items) => items.filter((item) => item.id !== header.id))}><Icon name="trash" size={13} /></Button></div>)}</div>}
                </Card>

                {contentType && <RequestBody selected={selected} contentType={contentType} onContentType={setContentType} value={values.body} onChange={(body) => setValues((current) => ({ ...current, body }))} file={file} onFile={setFile} />}
                {managementRequest ? <Card title="Authorization"><div className="ex-auth-status"><Icon name="check" size={15} /><span><b>Studio management access</b><small>This request uses your current scoped Studio session. Service credentials never enter the browser.</small></span></div></Card> : <AuthCard credentials={credentials} setCredentials={setCredentials} apiKey={apiKey} setApiKey={setApiKey} token={token} setToken={setToken} showToken={showToken} setShowToken={setShowToken} busy={authBusy} message={authMessage} signIn={signInUser} />}
              </div>

              <aside className="ex-inspect">
                <Card title="Request" actions={<Button size="sm" onClick={() => navigator.clipboard?.writeText(curl)}><Icon name="copy" size={13} /> Copy cURL</Button>}><div className="ex-url"><Badge tone={METHOD_TONES[selected.method]}>{selected.method.toUpperCase()}</Badge><code>{requestUrl || builtPath}</code></div><pre className="code-block ex-curl">{curl}</pre></Card>
                <ResponseDocumentation selected={selected} />
                {error && <ErrorNote error={error} />}
                {response && <Card title="Response" actions={<div className="row"><Badge tone={response.status < 300 ? "green" : response.status < 500 ? "yellow" : "red"}>{response.status}</Badge><span className="muted">{response.duration_ms} ms</span>{response.size != null && <span className="muted">{response.size} B</span>}</div>}>
                  {Object.keys(response.headers || {}).length > 0 && <details className="ex-response-headers"><summary>Response headers</summary><div className="ex-meta">{Object.entries(response.headers).map(([key, value]) => <span key={key}><span className="muted">{key}:</span> <code>{value}</code></span>)}</div></details>}
                  <Json value={response.body} />
                </Card>}
              </aside>
            </div>
          </div>
        )}
      </Loading>
    </Layout>
  );
}

function ParameterSection({ title, parameters, values, document, onChange }) {
  return <Card title={title}><div className="ex-parameters">{parameters.map((parameter) => <ParameterField key={`${parameter.in}:${parameter.name}`} parameter={parameter} value={values[parameter.name] ?? ""} document={document} onChange={(value) => onChange(parameter.name, value)} />)}</div></Card>;
}

function ParameterField({ parameter, value, document, onChange }) {
  const schema = schemaOf(document, parameter.schema);
  const hint = [parameter.description, schema.format, schema.type === "array" ? "Comma-separated values" : null].filter(Boolean).join(" · ");
  let control;
  if (schema.enum?.length) control = <select value={value} onChange={(event) => onChange(event.target.value)}><option value="">Select…</option>{schema.enum.map((option) => <option key={String(option)} value={String(option)}>{String(option)}</option>)}</select>;
  else if (schema.type === "boolean") control = <select value={String(value)} onChange={(event) => onChange(event.target.value)}><option value="">Select…</option><option value="true">true</option><option value="false">false</option></select>;
  else control = <input type={["integer", "number"].includes(schema.type) ? "number" : schema.format === "date" ? "date" : "text"} value={value} onChange={(event) => onChange(event.target.value)} placeholder={parameter.example ?? schema.example ?? schema.default ?? schema.type ?? "value"} />;
  return <Field label={<span><code>{parameter.name}</code>{parameter.required && <em className="ex-required">required</em>}</span>} hint={hint}>{control}</Field>;
}

function RequestBody({ selected, contentType, onContentType, value, onChange, file, onFile }) {
  const body = resolve(selected.document, selected.operation.requestBody);
  const contentTypes = Object.keys(body.content || {});
  const media = requestBodyFor(selected, contentType);
  const schema = schemaOf(selected.document, media.schema);
  const binary = schema.format === "binary" || contentType === "application/octet-stream" || contentType.startsWith("multipart/");
  return <Card title="Request body" actions={<select className="ex-content-type" value={contentType} onChange={(event) => { const next = event.target.value; onContentType(next); onFile(null); const nextMedia = requestBodyFor(selected, next); onChange(nextMedia.example ?? Object.values(nextMedia.examples || {})[0]?.value ?? sampleFor(selected.document, nextMedia.schema)); }}>{contentTypes.map((type) => <option key={type}>{type}</option>)}</select>}>
    {body.description && <p className="muted ex-body-description">{body.description}</p>}
    {binary ? <div className="ex-file"><input type="file" onChange={(event) => onFile(event.target.files?.[0] || null)} />{file && <span className="hint">{file.name} · {file.size.toLocaleString()} bytes · sent as raw bytes</span>}</div> : contentType.includes("json") ? <JsonInput value={value} onChange={onChange} rows={14} /> : <textarea rows={10} value={typeof value === "string" ? value : JSON.stringify(value ?? "", null, 2)} onChange={(event) => onChange(event.target.value)} />}
  </Card>;
}

function AuthCard({ credentials, setCredentials, apiKey, setApiKey, token, setToken, showToken, setShowToken, busy, message, signIn }) {
  return <Card title="Application-user authentication"><p className="muted ex-body-description">Run anonymously, sign in as an application user, or paste an existing bearer token. Tokens remain only in this tab.</p>
    <Field label="API key for cURL" optional hint="Studio supplies a scoped API context for live requests. This key is only included in copied cURL commands."><input type="password" value={apiKey} onChange={(event) => setApiKey(event.target.value)} placeholder="pk_… or sk_…" autoComplete="off" /></Field>
    {!credentials.mfa_token ? <div className="grid two"><Field label="User email"><input type="email" value={credentials.email} onChange={(event) => setCredentials({ ...credentials, email: event.target.value })} placeholder="user@example.com" autoComplete="off" /></Field><Field label="Password"><input type="password" value={credentials.password} onChange={(event) => setCredentials({ ...credentials, password: event.target.value })} placeholder="Password" autoComplete="new-password" /></Field></div> : <Field label="MFA code"><input value={credentials.code} onChange={(event) => setCredentials({ ...credentials, code: event.target.value })} placeholder="123456" inputMode="numeric" autoComplete="one-time-code" /></Field>}
    <div className="row wrap ex-auth-actions"><Button variant="primary" size="sm" disabled={busy || (!credentials.mfa_token && (!credentials.email || !credentials.password)) || (credentials.mfa_token && !credentials.code)} onClick={signIn}>{busy ? "Signing in…" : credentials.mfa_token ? "Verify MFA" : "Sign in user"}</Button>{token && <><Badge tone="green">authenticated</Badge><Button size="sm" onClick={() => setToken("")}>Use anonymous</Button></>}</div>
    {message && <p className="muted ex-auth-message">{message}</p>}
    <details className="ex-token"><summary>Use an existing access token</summary><div className="row"><input type={showToken ? "text" : "password"} value={token} onChange={(event) => setToken(event.target.value)} placeholder="Bearer token" autoComplete="off" /><Button size="sm" onClick={() => setShowToken((current) => !current)}>{showToken ? "Hide" : "Show"}</Button></div></details>
  </Card>;
}

function ResponseDocumentation({ selected }) {
  const responses = selected.operation.responses || {};
  if (!Object.keys(responses).length) return null;
  return <Card title="Documented responses"><div className="ex-response-list">{Object.entries(responses).map(([status, raw]) => { const response = resolve(selected.document, raw); return <div key={status}><Badge tone={String(status).startsWith("2") ? "green" : String(status).startsWith("4") ? "yellow" : String(status).startsWith("5") ? "red" : ""}>{status}</Badge><span>{response.description || "Response"}</span><small>{Object.keys(response.content || {}).join(", ")}</small></div>; })}</div></Card>;
}
