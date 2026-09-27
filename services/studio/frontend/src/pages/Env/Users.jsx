import { useState } from "react";
import Layout from "../../components/Layout";
import { Badge, Button, Card, Field, Json, JsonInput, Loading, Modal, PageHead, Table, Tabs, useAction, when } from "../../components/ui";
import { api, del, envPath, patch, post, put, useApi } from "../../lib/api";

const AUTH = { service: "auth" };

export default function Users({ project, env }) {
  const [tab, setTab] = useState("users");
  const base = `/projects/${project.ref}/envs/${env}`;
  return (
    <Layout title="Users & auth">
      <PageHead title="Users & auth" description="End users of this environment, managed by Akountz: accounts, sessions, MFA, roles and organizations." />
      <Tabs value={tab} onChange={setTab} tabs={[{ value: "users", label: "Users" }, { value: "roles", label: "Roles" }, { value: "orgs", label: "Organizations" }, { value: "events", label: "Sign-in activity" }, { value: "config", label: "Configuration" }]} />
      {tab === "users" && <UserList base={base} />}
      {tab === "roles" && <Roles base={base} />}
      {tab === "orgs" && <Orgs base={base} />}
      {tab === "events" && <Events base={base} />}
      {tab === "config" && <AuthConfig project={project} env={env} />}
    </Layout>
  );
}

function UserList({ base }) {
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("");
  const users = useApi(`${base}/users`, { ...AUTH, params: { search, status } });
  const stats = useApi(`${base}/stats`, AUTH);
  const [selected, setSelected] = useState(null);
  const [creating, setCreating] = useState(false);
  return (
    <div className="stack lg">
      {stats.data && <div className="grid">{Object.entries(stats.data).filter(([, v]) => typeof v === "number").map(([k, v]) => <div key={k} className="card stat"><b>{v}</b><span>{k.replace(/_/g, " ")}</span></div>)}</div>}
      <Card flush title={<div className="row"><input placeholder="Search email or name" value={search} onChange={(e) => setSearch(e.target.value)} style={{ width: 260 }} />
        <select value={status} onChange={(e) => setStatus(e.target.value)} style={{ width: 150 }}><option value="">all</option><option value="disabled">disabled</option><option value="unverified">unverified</option></select></div>}
        actions={<Button variant="primary" size="sm" onClick={() => setCreating(true)}>New user</Button>}>
        <Loading state={users} empty="No users yet.">
          {(data) => (
            <Table
              rows={data.data}
              onRowClick={setSelected}
              columns={[
                { label: "Email", render: (u) => <b>{u.email}</b> },
                { label: "Name", key: "name" },
                { label: "Roles", render: (u) => <div className="row wrap">{(u.roles || []).map((r) => <Badge key={r}>{r}</Badge>)}</div> },
                { label: "State", render: (u) => <div className="row">{u.disabled ? <Badge tone="red">disabled</Badge> : <Badge tone="green">active</Badge>}{!u.email_verified && <Badge tone="yellow">unverified</Badge>}{u.mfa_enabled && <Badge tone="blue">MFA</Badge>}</div> },
                { label: "Last sign-in", render: (u) => when(u.last_sign_in_at) },
              ]}
            />
          )}
        </Loading>
      </Card>
      {selected && <UserDetail base={base} id={selected.id} onClose={() => { setSelected(null); users.reload(); }} />}
      {creating && <NewUser base={base} onClose={() => setCreating(false)} onCreated={() => { setCreating(false); users.reload(); }} />}
    </div>
  );
}

function UserDetail({ base, id, onClose }) {
  const user = useApi(`${base}/users/${id}`, AUTH);
  const history = useApi(`${base}/users/${id}/history`, AUTH);
  const [run] = useAction();
  const act = async (fn, label) => { if (await run(fn, label)) user.reload(); };
  const u = user.data;
  return (
    <Modal wide title={u?.email || "User"} onClose={onClose}>
      <Loading state={user}>
        {() => (
          <>
            <div className="row wrap">
              <Button onClick={() => act(() => patch(`${base}/users/${id}`, { disabled: !u.disabled }, AUTH), u.disabled ? "Enabled" : "Disabled")}>{u.disabled ? "Enable" : "Disable"}</Button>
              {!u.email_verified && <Button onClick={() => act(() => patch(`${base}/users/${id}`, { email_verified: true }, AUTH), "Marked verified")}>Mark verified</Button>}
              <Button onClick={() => act(() => post(`${base}/users/${id}/sessions/revoke`, {}, AUTH), "Signed out everywhere")}>Sign out everywhere</Button>
              {u.mfa_enabled && <Button onClick={() => act(() => post(`${base}/users/${id}/mfa/reset`, {}, AUTH), "MFA reset")}>Reset MFA</Button>}
              <Button onClick={() => { const password = prompt("New password"); if (password) act(() => patch(`${base}/users/${id}`, { password }, AUTH), "Password set"); }}>Set password</Button>
              <span className="grow" />
              <Button variant="danger" onClick={async () => { if (confirm(`Delete ${u.email}?`) && await run(() => del(`${base}/users/${id}`, AUTH), "Deleted")) onClose(); }}>Delete</Button>
            </div>
            <Grants base={base} id={id} user={u} onDone={user.reload} />
            <Json value={{ ...u, sessions: undefined }} />
            <h3>Sessions</h3>
            <div className="card flush">
              <Table rows={u.sessions || []} empty="No active sessions." columns={[
                { label: "Session", render: (s) => <code>{String(s.id).slice(0, 8)}</code> },
                { label: "Device", render: (s) => s.user_agent || "—" },
                { label: "IP", key: "ip" },
                { label: "Last active", render: (s) => when(s.last_seen_at || s.created_at) },
                { label: "", render: (s) => <Button size="sm" onClick={() => act(() => del(`${base}/users/${id}/sessions/${s.id}`, AUTH), "Session revoked")}>Revoke</Button> },
              ]} />
            </div>
            <h3>History</h3>
            <div className="card flush">
              <Loading state={history} empty="No activity.">
                {(h) => <Table rows={h.data} columns={[{ label: "When", render: (e) => when(e.created_at) }, { label: "Event", key: "kind" }, { label: "OK", render: (e) => <Badge tone={e.success ? "green" : "red"}>{e.success ? "ok" : e.reason || "failed"}</Badge> }, { label: "IP", key: "ip" }]} />}
              </Loading>
            </div>
          </>
        )}
      </Loading>
    </Modal>
  );
}

function Grants({ base, id, user, onDone }) {
  const [roles, setRoles] = useState((user.roles || []).join(", "));
  const [permission, setPermission] = useState("");
  const [run, busy] = useAction();
  const list = (text) => text.split(",").map((s) => s.trim()).filter(Boolean);
  return (
    <div className="stack" style={{ gap: 10 }}>
      <div className="row" style={{ alignItems: "flex-end" }}>
        <Field label="Roles" hint="Comma-separated role names from the Roles tab."><input value={roles} onChange={(e) => setRoles(e.target.value)} /></Field>
        <Button disabled={busy} onClick={async () => { if (await run(() => patch(`${base}/users/${id}`, { roles: list(roles) }, AUTH), "Roles saved")) onDone(); }}>Save roles</Button>
      </div>
      <div className="row wrap">
        <span className="muted" style={{ fontSize: 12.5 }}>Direct permissions:</span>
        {(user.permissions || []).map((p) => (
          <span key={p} className="badge">{p} <a href="#" onClick={async (e) => { e.preventDefault(); if (await run(() => api("DELETE", `${base}/users/${id}/permissions`, { permissions: [p] }, AUTH), "Revoked")) onDone(); }}>✕</a></span>
        ))}
        <input value={permission} onChange={(e) => setPermission(e.target.value)} placeholder="posts:write" style={{ width: 160 }} />
        <Button size="sm" disabled={busy || !permission} onClick={async () => { if (await run(() => post(`${base}/users/${id}/permissions`, { permissions: list(permission) }, AUTH), "Granted")) { setPermission(""); onDone(); } }}>Grant</Button>
      </div>
    </div>
  );
}

function NewUser({ base, onClose, onCreated }) {
  const [data, setData] = useState({ email: "", password: "", name: "" });
  const [run, busy] = useAction();
  return (
    <Modal title="New user" onClose={onClose} footer={<Button variant="primary" disabled={busy || !data.email} onClick={async () => { if (await run(() => post(`${base}/users`, { ...data, password: data.password || null }, AUTH), "User created")) onCreated(); }}>Create</Button>}>
      <Field label="Email"><input autoFocus value={data.email} onChange={(e) => setData({ ...data, email: e.target.value })} /></Field>
      <Field label="Name"><input value={data.name} onChange={(e) => setData({ ...data, name: e.target.value })} /></Field>
      <Field label="Password" hint="Leave empty to have the user set one through a recovery link."><input type="password" value={data.password} onChange={(e) => setData({ ...data, password: e.target.value })} /></Field>
    </Modal>
  );
}

function Roles({ base }) {
  const roles = useApi(`${base}/roles`, AUTH);
  const [editing, setEditing] = useState(null);
  const [run, busy] = useAction();
  return (
    <Card flush title="Roles" actions={<Button size="sm" variant="primary" onClick={() => setEditing({ isNew: true, name: "", description: "", permissions: "" })}>New role</Button>}>
      <Loading state={roles} empty="No roles yet.">
        {(data) => <Table rows={data.data} onRowClick={(r) => setEditing({ ...r, permissions: (r.permissions || []).join(", ") })} columns={[
          { label: "Role", render: (r) => <b>{r.name}</b> }, { label: "Description", key: "description" },
          { label: "Permissions", render: (r) => <div className="row wrap">{(r.permissions || []).map((p) => <Badge key={p}>{p}</Badge>)}</div> },
          { label: "Members", key: "members" },
        ]} />}
      </Loading>
      {editing && (
        <Modal title={editing.isNew ? "New role" : editing.name} onClose={() => setEditing(null)} footer={<>
          {!editing.isNew && <Button variant="danger" onClick={async () => { if (await run(() => del(`${base}/roles/${editing.name}`, AUTH), "Deleted")) { setEditing(null); roles.reload(); } }}>Delete</Button>}
          <span className="grow" />
          <Button variant="primary" disabled={busy || !editing.name} onClick={async () => {
            const body = { name: editing.name, description: editing.description || "", permissions: editing.permissions.split(",").map((s) => s.trim()).filter(Boolean) };
            if (await run(() => put(`${base}/roles`, body, AUTH), "Saved")) { setEditing(null); roles.reload(); }
          }}>Save</Button>
        </>}>
          <Field label="Name"><input value={editing.name} disabled={!editing.isNew} onChange={(e) => setEditing({ ...editing, name: e.target.value })} placeholder="editor" /></Field>
          <Field label="Description"><input value={editing.description || ""} onChange={(e) => setEditing({ ...editing, description: e.target.value })} /></Field>
          <Field label="Permissions" hint="Comma-separated, e.g. posts:write, posts:publish. * grants everything."><input value={editing.permissions} onChange={(e) => setEditing({ ...editing, permissions: e.target.value })} /></Field>
        </Modal>
      )}
    </Card>
  );
}

function Orgs({ base }) {
  const orgs = useApi(`${base}/orgs`, AUTH);
  const [selected, setSelected] = useState(null);
  const detail = useApi(selected ? `${base}/orgs/${selected}` : null, AUTH);
  return (
    <div className="stack lg">
      <Card flush title="Organizations">
        <Loading state={orgs} empty="No organizations. Users create them through /auth/v1/orgs.">
          {(data) => <Table rows={data.data} onRowClick={(o) => setSelected(o.slug)} columns={[{ label: "Name", render: (o) => <b>{o.name}</b> }, { label: "Slug", key: "slug" }, { label: "Members", key: "members" }, { label: "Created", render: (o) => when(o.created_at) }]} />}
        </Loading>
      </Card>
      {selected && detail.data && <Card title={detail.data.name}><Json value={detail.data} /></Card>}
    </div>
  );
}

function Events({ base }) {
  const [failed, setFailed] = useState(false);
  const events = useApi(`${base}/events`, { ...AUTH, params: { failed: failed ? "true" : "" } });
  return (
    <Card flush title="Sign-in activity" actions={<label className="check"><input type="checkbox" checked={failed} onChange={(e) => setFailed(e.target.checked)} /> failures only</label>}>
      <Loading state={events} empty="No activity yet.">
        {(data) => <Table rows={data.data} columns={[{ label: "When", render: (e) => when(e.created_at) }, { label: "Event", key: "kind" }, { label: "Email", key: "email" }, { label: "Result", render: (e) => <Badge tone={e.success ? "green" : "red"}>{e.success ? "ok" : e.reason || "failed"}</Badge> }, { label: "IP", key: "ip" }]} />}
      </Loading>
    </Card>
  );
}

function AuthConfig({ project, env }) {
  const envState = useApi(envPath(project.ref, env));
  const [auth, setAuth] = useState(undefined);
  const [run, busy] = useAction();
  return (
    <Card title="Akountz configuration" actions={<Button variant="primary" size="sm" disabled={busy || auth === undefined} onClick={async () => { if (await run(() => patch(envPath(project.ref, env), { auth }), "Saved")) envState.reload(); }}>Save</Button>}>
      <Loading state={envState}>
        {(data) => (
          <Field label="Auth settings (JSON)" hint='Sign-up, password rules, email confirmation, magic links, MFA, token lifetimes, redirect URLs, OAuth providers, e.g. {"providers": {"github": {"client_id": "…", "client_secret": "secret://GITHUB_SECRET"}}}.'>
            <JsonInput value={auth === undefined ? data.auth || {} : auth} onChange={setAuth} rows={18} />
          </Field>
        )}
      </Loading>
    </Card>
  );
}
