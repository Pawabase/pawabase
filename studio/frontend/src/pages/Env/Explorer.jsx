import { useEffect, useMemo, useState } from "react";
import Layout from "../../components/Layout";
import { Badge, Button, Card, Empty, ErrorNote, Field, Json, JsonInput, Loading, PageHead } from "../../components/ui";
import { envPath, post, useApi } from "../../lib/api";

const METHODS = ["get", "post", "put", "patch", "delete"];
const METHOD_TONES = { get: "green", post: "blue", put: "yellow", patch: "yellow", delete: "red" };

function resolveSchema(document, schema) {
  if (!schema?.$ref) return schema || {};
  return schema.$ref.replace(/^#\//, "").split("/").reduce((value, key) => value?.[key], document) || {};
}

function sampleFor(document, raw, depth = 0) {
  if (depth > 5) return null;
  const schema = resolveSchema(document, raw);
  if (schema.example !== undefined) return schema.example;
  if (schema.default !== undefined) return schema.default;
  if (schema.enum?.length) return schema.enum[0];
  if (schema.type === "array") return [sampleFor(document, schema.items, depth + 1)];
  if (schema.type === "object" || schema.properties) {
    return Object.fromEntries(Object.entries(schema.properties || {}).map(([key, value]) => [key, sampleFor(document, value, depth + 1)]));
  }
  if (schema.type === "integer" || schema.type === "number") return 0;
  if (schema.type === "boolean") return false;
  return "";
}

function operations(document) {
  return Object.entries(document?.paths || {}).flatMap(([path, pathItem]) =>
    METHODS.filter((method) => pathItem[method]).map((method) => ({
      id: `${method}:${path}`,
      method,
      path,
      operation: pathItem[method],
      parameters: [...(pathItem.parameters || []), ...(pathItem[method].parameters || [])],
    }))
  );
}

function shortPath(path, version) {
  const prefix = `/rest/${version}`;
  return path.startsWith(prefix) ? path.slice(prefix.length) || "/" : path;
}

export default function Explorer({ project, env }) {
  const base = envPath(project.ref, env);
  const versionState = useApi(`${base}/api-versions`);
  const [version, setVersion] = useState("v1");
  const specState = useApi(`${base}/openapi`, { params: { version } });
  const [search, setSearch] = useState("");
  const [selectedId, setSelectedId] = useState("");
  const [values, setValues] = useState({ path: {}, query: {}, headers: {}, body: undefined });
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
  const all = useMemo(() => operations(specState.data), [specState.data]);
  const filtered = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return needle ? all.filter((item) => `${item.method} ${item.path} ${item.operation.summary || ""} ${(item.operation.tags || []).join(" ")}`.toLowerCase().includes(needle)) : all;
  }, [all, search]);
  const selected = all.find((item) => item.id === selectedId) || filtered[0] || null;

  useEffect(() => {
    setSelectedId("");
    setResponse(null);
  }, [version]);

  useEffect(() => {
    if (!selected || !specState.data) return;
    const content = selected.operation.requestBody?.content || {};
    const schema = content["application/json"]?.schema;
    setValues({ path: {}, query: {}, headers: {}, body: schema ? sampleFor(specState.data, schema) : undefined });
    setResponse(null);
    setError(null);
  }, [selected?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const choose = (item) => setSelectedId(item.id);
  const setParameter = (location, name, value) => setValues((current) => ({ ...current, [location]: { ...current[location], [name]: value } }));
  const send = async () => {
    if (!selected) return;
    let path = shortPath(selected.path, version);
    for (const parameter of selected.parameters.filter((item) => item.in === "path")) {
      path = path.replace(`{${parameter.name}}`, encodeURIComponent(values.path[parameter.name] || ""));
    }
    setSending(true);
    setError(null);
    setResponse(null);
    try {
      const result = await post("/request", {
        project: project.ref,
        env,
        version,
        method: selected.method.toUpperCase(),
        path,
        query: Object.fromEntries(Object.entries(values.query).filter(([, value]) => value !== "")),
        headers: Object.fromEntries(Object.entries(values.headers).filter(([, value]) => value !== "")),
        body: values.body,
        access_token: token,
      }, { service: "explorer" });
      setResponse(result);
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
      const result = await post("/sign-in", { project: project.ref, env, ...credentials }, { service: "explorer" });
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

  return (
    <Layout title="API Explorer">
      <PageHead
        title="API Explorer"
        description="Browse the API generated by the active release and make real requests. Studio supplies project context securely; no API key is needed."
        actions={<Field label="API version"><select value={version} onChange={(event) => setVersion(event.target.value)}>{versionNames.map((name) => <option key={name}>{name}</option>)}</select></Field>}
      />
      <Loading state={specState}>
        {(document) => (
          <div className="ex">
            <aside className="card ex-list">
              <div className="ex-list-head"><h2>Endpoints</h2><span className="muted">{filtered.length === all.length ? all.length : `${filtered.length} of ${all.length}`}</span></div>
              <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search paths, methods, or tags…" />
              <div className="ex-scroll">
                {filtered.map((item) => (
                  <button type="button" key={item.id} onClick={() => choose(item)} className={`ex-item ${selected?.id === item.id ? "active" : ""}`}>
                    <span className="row" style={{ gap: 10, alignItems: "flex-start" }}>
                      <Badge tone={METHOD_TONES[item.method]}>{item.method.toUpperCase()}</Badge>
                      <code className="ex-path">{shortPath(item.path, version)}</code>
                    </span>
                    {item.operation.summary && <span className="ex-summary">{item.operation.summary}</span>}
                  </button>
                ))}
                {!filtered.length && <Empty>No matching endpoints.</Empty>}
              </div>
            </aside>

            {selected ? (
              <div className="ex-main">
                <Card>
                  <div className="ex-request">
                    <div>
                      <div className="ex-route"><Badge tone={METHOD_TONES[selected.method]}>{selected.method.toUpperCase()}</Badge><code>{selected.path}</code></div>
                      <h2>{selected.operation.summary || selected.operation.operationId || "API request"}</h2>
                      {selected.operation.description && <p className="muted">{selected.operation.description}</p>}
                    </div>
                    <Button variant="primary" disabled={sending || selected.parameters.some((item) => item.in === "path" && item.required && !values.path[item.name])} onClick={send}>{sending ? "Sending…" : "Send request"}</Button>
                  </div>
                </Card>

                {selected.parameters.some((item) => item.in === "path") && <ParameterCard title="Path parameters" parameters={selected.parameters.filter((item) => item.in === "path")} values={values.path} onChange={(name, value) => setParameter("path", name, value)} />}
                {selected.parameters.some((item) => item.in === "query") && <ParameterCard title="Query parameters" parameters={selected.parameters.filter((item) => item.in === "query")} values={values.query} onChange={(name, value) => setParameter("query", name, value)} />}
                {selected.parameters.some((item) => item.in === "header" && item.name.toLowerCase() !== "apikey" && item.name.toLowerCase() !== "authorization") && <ParameterCard title="Headers" parameters={selected.parameters.filter((item) => item.in === "header" && item.name.toLowerCase() !== "apikey" && item.name.toLowerCase() !== "authorization")} values={values.headers} onChange={(name, value) => setParameter("headers", name, value)} />}

                {values.body !== undefined && <Card title="JSON body"><JsonInput value={values.body} onChange={(body) => setValues((current) => ({ ...current, body }))} rows={12} /></Card>}

                <Card title="User authentication">
                  <p className="muted">Requests are anonymous by default. Sign in as a project user to test authenticated, role, permission, and ownership policies. Credentials and tokens stay in this page and are never saved.</p>
                  {!credentials.mfa_token ? <div className="grid two">
                    <Field label="User email"><input type="email" value={credentials.email} onChange={(event) => setCredentials({ ...credentials, email: event.target.value })} placeholder="user@example.com" autoComplete="off" /></Field>
                    <Field label="Password"><input type="password" value={credentials.password} onChange={(event) => setCredentials({ ...credentials, password: event.target.value })} placeholder="Password" autoComplete="new-password" /></Field>
                  </div> : <Field label="MFA code"><input value={credentials.code} onChange={(event) => setCredentials({ ...credentials, code: event.target.value })} placeholder="123456" inputMode="numeric" autoComplete="one-time-code" /></Field>}
                  <div className="row" style={{ marginTop: 10 }}>
                    <Button variant="primary" size="sm" disabled={authBusy || (!credentials.mfa_token && (!credentials.email || !credentials.password)) || (credentials.mfa_token && !credentials.code)} onClick={signInUser}>{authBusy ? "Signing in…" : credentials.mfa_token ? "Verify MFA" : "Sign in user"}</Button>
                    {token && <><Badge tone="green">authenticated</Badge><Button size="sm" onClick={() => { setToken(""); setAuthMessage("Using anonymous access"); }}>Use anonymous</Button></>}
                  </div>
                  {authMessage && <p className="muted" style={{ margin: "12px 0 0" }}>{authMessage}</p>}
                  <details style={{ marginTop: 14 }}>
                    <summary className="muted" style={{ cursor: "pointer" }}>Use an existing access token</summary>
                    <div className="row" style={{ marginTop: 10 }}>
                      <input type={showToken ? "text" : "password"} value={token} onChange={(event) => setToken(event.target.value)} placeholder="Bearer token" autoComplete="off" />
                      <Button size="sm" onClick={() => setShowToken((value) => !value)}>{showToken ? "Hide" : "Show"}</Button>
                    </div>
                  </details>
                </Card>

                {error && <ErrorNote error={error} />}
                {response && <Card title="Response" actions={<div className="row"><Badge tone={response.status < 300 ? "green" : response.status < 500 ? "yellow" : "red"}>{response.status}</Badge><span className="muted">{response.duration_ms} ms</span></div>}>
                  {Object.keys(response.headers || {}).length > 0 && <div className="ex-meta">{Object.entries(response.headers).map(([key, value]) => <span key={key}><span className="muted">{key}:</span> <code>{value}</code></span>)}</div>}
                  <Json value={response.body} />
                </Card>}
              </div>
            ) : <Empty>This API version has no endpoints.</Empty>}
          </div>
        )}
      </Loading>
    </Layout>
  );
}

function ParameterCard({ title, parameters, values, onChange }) {
  return <Card title={title}><div className="grid two">{parameters.map((parameter) => (
    <Field key={`${parameter.in}:${parameter.name}`} label={parameter.name} optional={!parameter.required} hint={parameter.description}>
      <input value={values[parameter.name] ?? ""} onChange={(event) => onChange(parameter.name, event.target.value)} placeholder={parameter.schema?.example ?? parameter.example ?? parameter.schema?.type ?? "value"} />
    </Field>
  ))}</div></Card>;
}
