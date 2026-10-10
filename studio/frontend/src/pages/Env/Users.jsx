import { useState } from "react";
import Layout, { envHref } from "../../components/Layout";
import { Icon } from "../../components/icons";
import { Badge, Button, Card, Field, IconButton, Json, Loading, Modal, PageHead, Section, Segmented, Switch, Table, TagInput, Tabs, useAction, when } from "../../components/ui";
import { PermissionPicker, RolePicker } from "../../components/AccessPickers";
import { api, del, envPath, patch, post, put, useApi } from "../../lib/api";

const AUTH = { service: "auth" };

const AUTH_GROUPS = [
  { title: "Directory", items: [["users", "Users"], ["orgs", "Organizations"], ["roles", "Roles"]] },
  { title: "Sign-in", items: [["providers", "Social providers"], ["events", "Sign-in activity"]] },
  { title: "Settings", items: [["config", "Configuration"], ["emails", "Email templates"]] },
];
const AUTH_LABELS = Object.fromEntries(AUTH_GROUPS.flatMap((group) => group.items));

export default function Users({ env }) {
  const [tab, setTab] = useState("users");
  const base = `/envs/${env}`;
  const subnav = {
    title: "Users & auth",
    groups: [
      ...AUTH_GROUPS.map((group) => ({ title: group.title, items: group.items.map(([key, label]) => ({ key, label })) })),
      { title: "Access", items: [{ key: "policies", label: "Policies", href: envHref(env, "policies") }] },
    ],
    active: tab,
    onSelect: setTab,
  };
  return (
    <Layout title="Users & auth" subnav={subnav}>
      <PageHead title={AUTH_LABELS[tab]} />
      {tab === "users" && <UserList base={base} />}
      {tab === "roles" && <Roles base={base} />}
      {tab === "orgs" && <Orgs base={base} />}
      {tab === "events" && <Events base={base} />}
      {tab === "config" && <AuthConfig env={env} parts={["general"]} title="Configuration" />}
      {tab === "providers" && <AuthConfig env={env} parts={["providers"]} title="Social providers" />}
      {tab === "emails" && <AuthConfig env={env} parts={["emails"]} title="Email templates" />}
    </Layout>
  );
}

function UserList({ base }) {
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("");
  const users = useApi(`${base}/users`, { ...AUTH, params: { search, status } });
  const [selected, setSelected] = useState(null);
  const [creating, setCreating] = useState(false);
  return (
    <div className="stack lg">
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
  const roleList = useApi(`${base}/roles`, AUTH);
  const resources = useApi(`${base}/resources`, { params: { limit: 500 } });
  const [roles, setRoles] = useState(user.roles || []);
  const [permissions, setPermissions] = useState(user.permissions || []);
  const [run, busy] = useAction();
  const sameSet = (x, y) => x.length === y.length && x.every((v) => y.includes(v));
  const saveRoles = async () => { if (await run(() => patch(`${base}/users/${id}`, { roles }, AUTH), "Roles saved")) onDone(); };
  const savePermissions = async () => {
    const before = user.permissions || [];
    const add = permissions.filter((p) => !before.includes(p));
    const remove = before.filter((p) => !permissions.includes(p));
    const steps = [];
    if (add.length) steps.push(() => post(`${base}/users/${id}/permissions`, { permissions: add }, AUTH));
    if (remove.length) steps.push(() => api("DELETE", `${base}/users/${id}/permissions`, { permissions: remove }, AUTH));
    if (await run(async () => { for (const step of steps) await step(); return true; }, "Permissions saved")) onDone();
  };
  return (
    <div className="stack" style={{ gap: 14 }}>
      <Field label="Roles" hint="Click to give or take away a role. Hover a role to see what it allows.">
        <RolePicker value={roles} onChange={setRoles} roles={roleList.data?.data || []} />
      </Field>
      <div><Button disabled={busy || sameSet(roles, user.roles || [])} onClick={saveRoles}>Save roles</Button></div>
      <Field label="Direct permissions" hint="On top of what the roles allow.">
        <PermissionPicker value={permissions} onChange={setPermissions} known={roleList.data?.permissions || []} resources={(resources.data?.data || []).map((r) => r.name)} />
      </Field>
      <div><Button disabled={busy || sameSet(permissions, user.permissions || [])} onClick={savePermissions}>Save permissions</Button></div>
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
  const resources = useApi(`${base}/resources`, { params: { limit: 500 } });
  const [editing, setEditing] = useState(null);
  const [run, busy] = useAction();
  return (
    <Card flush title="Roles" actions={<Button size="sm" variant="primary" onClick={() => setEditing({ isNew: true, name: "", description: "", permissions: [] })}>New role</Button>}>
      <Loading state={roles} empty="No roles yet.">
        {(data) => <Table rows={data.data} onRowClick={(r) => setEditing({ ...r, permissions: r.permissions || [] })} columns={[
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
            const body = { name: editing.name, description: editing.description || "", permissions: editing.permissions };
            if (await run(() => put(`${base}/roles`, body, AUTH), "Saved")) { setEditing(null); roles.reload(); }
          }}>Save</Button>
        </>}>
          <Field label="Name"><input value={editing.name} disabled={!editing.isNew} onChange={(e) => setEditing({ ...editing, name: e.target.value })} placeholder="editor" /></Field>
          <Field label="Description"><input value={editing.description || ""} onChange={(e) => setEditing({ ...editing, description: e.target.value })} /></Field>
          <Field label="Permissions" hint="Click to allow. * grants everything.">
            <PermissionPicker value={editing.permissions} onChange={(permissions) => setEditing({ ...editing, permissions })} known={roles.data?.permissions || []} resources={(resources.data?.data || []).map((r) => r.name)} />
          </Field>
        </Modal>
      )}
    </Card>
  );
}

const ORG_ROLES = ["owner", "admin", "member", "viewer"];
const slugify = (text) => text.toLowerCase().trim().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 63);

function Orgs({ base }) {
  const orgs = useApi(`${base}/orgs`, AUTH);
  const [selected, setSelected] = useState(null);
  const [creating, setCreating] = useState(false);
  return (
    <div className="stack lg">
      <Card flush title="Organizations" actions={<Button variant="primary" size="sm" onClick={() => setCreating(true)}><Icon name="plus" size={14} /> New organization</Button>}>
        <Loading state={orgs} empty="No organizations yet. Create one here, or users create them through /auth/v1/orgs.">
          {(data) => <Table rows={data.data} onRowClick={(o) => setSelected(o.slug)} empty="No organizations yet. Create one here, or users create them through /auth/v1/orgs." columns={[{ label: "Name", render: (o) => <b>{o.name}</b> }, { label: "Slug", render: (o) => <code>{o.slug}</code> }, { label: "Members", key: "members" }, { label: "Created", render: (o) => when(o.created_at) }]} />}
        </Loading>
      </Card>
      {selected && <OrgDetail base={base} slug={selected} onClose={() => { setSelected(null); orgs.reload(); }} />}
      {creating && <NewOrg base={base} onClose={() => setCreating(false)} onCreated={(slug) => { setCreating(false); orgs.reload(); setSelected(slug); }} />}
    </div>
  );
}

function NewOrg({ base, onClose, onCreated }) {
  const [data, setData] = useState({ name: "", slug: "", owner_email: "" });
  const [touched, setTouched] = useState(false);
  const [run, busy] = useAction();
  const slug = touched ? data.slug : slugify(data.name);
  const valid = data.name.trim() && /^[a-z0-9][a-z0-9-]{1,62}$/.test(slug);
  return (
    <Modal title="New organization" onClose={onClose} footer={<Button variant="primary" disabled={busy || !valid} onClick={async () => {
      const made = await run(() => post(`${base}/orgs`, { name: data.name.trim(), slug, owner_email: data.owner_email.trim() || null }, AUTH), "Organization created");
      if (made) onCreated(slug);
    }}>Create organization</Button>}>
      <Field label="Name"><input autoFocus value={data.name} onChange={(e) => setData({ ...data, name: e.target.value })} placeholder="Acme Inc" /></Field>
      <Field label="Slug" hint="Lowercase letters, digits and hyphens. It appears in URLs and in the organization claim of a session.">
        <input value={slug} onChange={(e) => { setTouched(true); setData({ ...data, slug: slugify(e.target.value) }); }} placeholder="acme-inc" />
      </Field>
      <Field label="Owner's email" optional hint="An existing user to make the owner. Without one the organization starts with no members.">
        <input type="email" value={data.owner_email} onChange={(e) => setData({ ...data, owner_email: e.target.value })} placeholder="ada@example.com" />
      </Field>
    </Modal>
  );
}

function OrgDetail({ base, slug, onClose }) {
  const org = useApi(`${base}/orgs/${slug}`, AUTH);
  const [name, setName] = useState(null);
  const [add, setAdd] = useState({ email: "", role: "member" });
  const [run, busy] = useAction();
  const o = org.data;
  const act = async (fn, label) => { if (await run(fn, label)) org.reload(); };
  return (
    <Modal title={o?.name || slug} onClose={onClose} footer={<Button onClick={onClose}>Done</Button>}>
      <Loading state={org}>
        {() => (
          <>
            <section className="form-section">
              <div className="form-section-head"><div><h3>Details</h3><p>The slug <code>{slug}</code> cannot change.</p></div></div>
              <div className="row">
                <input value={name ?? o.name} onChange={(e) => setName(e.target.value)} aria-label="Organization name" />
                <Button variant="primary" disabled={busy || name === null || !name.trim() || name === o.name} onClick={() => act(async () => { await patch(`${base}/orgs/${slug}`, { name: name.trim() }, AUTH); setName(null); }, "Renamed")}>Rename</Button>
              </div>
            </section>
            <section className="form-section">
              <div className="form-section-head"><div><h3>Members</h3><p>Owners and admins manage the organization from the application. Here an operator can change roles directly.</p></div></div>
              <Table
                rows={o.members}
                empty="No members yet."
                columns={[
                  { label: "Email", render: (m) => <b>{m.email}</b> },
                  { label: "Role", render: (m) => <select value={m.role} disabled={busy} onChange={(e) => act(() => patch(`${base}/orgs/${slug}/members/${m.user_id}`, { role: e.target.value }, AUTH), "Role changed")} style={{ width: 130 }}>{ORG_ROLES.map((r) => <option key={r}>{r}</option>)}</select> },
                  { label: "", render: (m) => <Button size="sm" variant="danger" disabled={busy} onClick={async () => { if (confirm(`Remove ${m.email} from ${o.name}?`)) await act(() => del(`${base}/orgs/${slug}/members/${m.user_id}`, AUTH), "Removed"); }}>Remove</Button> },
                ]}
              />
              <div className="row" style={{ marginTop: 12 }}>
                <input type="email" value={add.email} onChange={(e) => setAdd({ ...add, email: e.target.value })} placeholder="Existing user's email" aria-label="Email to add" />
                <select value={add.role} onChange={(e) => setAdd({ ...add, role: e.target.value })} style={{ width: 130 }}>{ORG_ROLES.map((r) => <option key={r}>{r}</option>)}</select>
                <Button variant="primary" disabled={busy || !add.email.trim()} onClick={() => act(async () => { await post(`${base}/orgs/${slug}/members`, { email: add.email.trim(), role: add.role }, AUTH); setAdd({ email: "", role: "member" }); }, "Member added")}>Add member</Button>
              </div>
            </section>
            {o.teams?.length > 0 && (
              <section className="form-section">
                <div className="form-section-head"><div><h3>Teams</h3></div></div>
                <div className="row wrap">{o.teams.map((t) => <Badge key={t.slug}>{t.name}</Badge>)}</div>
              </section>
            )}
            <section className="form-section">
              <div className="form-section-head"><div><h3>Delete organization</h3><p>Removes its memberships, teams and invitations for good. The users stay.</p></div></div>
              <Button variant="danger" disabled={busy} onClick={async () => { if (confirm(`Delete ${o.name}? This cannot be undone.`) && await run(() => del(`${base}/orgs/${slug}`, AUTH), "Organization deleted")) onClose(); }}>Delete organization</Button>
            </section>
          </>
        )}
      </Loading>
    </Modal>
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

// Every field Akountz's AuthConfig actually reads (services/akountz/app/environment.py),
// each with its own control — no raw JSON for an operator to get wrong.
const AUTH_DEFAULTS = {
  signup_enabled: true,
  require_email_verification: false,
  password_policy: "basic",
  password_min_length: 8,
  access_ttl: 900,
  refresh_ttl: 30 * 24 * 3600,
  magic_link_enabled: true,
  mfa_enabled: true,
  site_url: "",
  redirect_urls: [],
  providers: {},
  default_roles: [],
  emails: {},
};

const KNOWN_PROVIDERS = ["google", "github", "discord", "microsoft"];
const EMAIL_KINDS = [
  ["verify", "Verify email", "Confirm your email for {project}"],
  ["recovery", "Password recovery", "Reset your {project} password"],
  ["magic", "Magic link", "Your {project} sign-in link"],
  ["invite", "Organization invite", "You are invited to {organization} on {project}"],
  ["email_change", "Email change", "Confirm your new email for {project}"],
];

function AuthConfig({ env, parts = ['general', 'providers', 'emails'], title = 'Akountz configuration' }) {
  const envState = useApi(envPath(env));
  const [auth, setAuth] = useState(undefined);
  const [run, busy] = useAction();
  const value = auth ?? envState.data?.auth ?? {};
  const dirty = auth !== undefined;
  const set = (patch) => setAuth({ ...AUTH_DEFAULTS, ...value, ...patch });
  const field = (key) => value[key] ?? AUTH_DEFAULTS[key];
  return (
    <Card
      title={title}
      actions={<Button variant="primary" size="sm" disabled={busy || !dirty} onClick={async () => { if (await run(() => patch(envPath(env), { auth: { ...AUTH_DEFAULTS, ...value } }), "Saved")) { envState.reload(); setAuth(undefined); } }}>Save</Button>}
    >
      <Loading state={envState}>
        {() => (
          <div className="stack lg">
            {parts.includes("general") && <>
            <Section title="Sign-up" description="Who can create an account, and whether their email must be confirmed first.">
              <div className="grid two">
                <Switch checked={field("signup_enabled")} onChange={(v) => set({ signup_enabled: v })} label="Sign-up enabled" hint="Turn off to invite-only new accounts." />
                <Switch checked={field("require_email_verification")} onChange={(v) => set({ require_email_verification: v })} label="Require email verification" hint="Unverified users can't sign in until they confirm." />
              </div>
              <Field label="Default roles" hint="Granted automatically at sign-up.">
                <TagInput value={field("default_roles")} onChange={(v) => set({ default_roles: v })} placeholder="customer" />
              </Field>
            </Section>

            <Section title="Passwords" description="Password strength and how long sessions last.">
              <div className="grid two">
                <Field label="Policy">
                  <Segmented options={[["basic", "Basic"], ["strict", "Strict"]]} value={field("password_policy")} onChange={(v) => set({ password_policy: v })} />
                </Field>
                <Field label="Minimum length" hint="Characters.">
                  <input type="number" min={6} max={128} value={field("password_min_length")} onChange={(e) => set({ password_min_length: Number(e.target.value) || 8 })} />
                </Field>
                <Field label="Access token lifetime" hint="Seconds. How long a bearer token works before it needs refreshing.">
                  <input type="number" min={60} value={field("access_ttl")} onChange={(e) => set({ access_ttl: Number(e.target.value) || 900 })} />
                </Field>
                <Field label="Refresh token lifetime" hint="Seconds. How long a signed-out-of-band session stays resumable.">
                  <input type="number" min={3600} value={field("refresh_ttl")} onChange={(e) => set({ refresh_ttl: Number(e.target.value) || 2592000 })} />
                </Field>
              </div>
            </Section>

            <Section title="Sign-in methods">
              <div className="grid two">
                <Switch checked={field("magic_link_enabled")} onChange={(v) => set({ magic_link_enabled: v })} label="Magic links" hint="Passwordless sign-in by emailed one-time link." />
                <Switch checked={field("mfa_enabled")} onChange={(v) => set({ mfa_enabled: v })} label="MFA (TOTP)" hint="Let users add an authenticator app for aal2." />
              </div>
            </Section>

            <Section title="URLs" description="Where a browser is allowed to land after an OAuth or magic-link redirect.">
              <Field label="Site URL"><input value={field("site_url")} onChange={(e) => set({ site_url: e.target.value })} placeholder="https://app.example.com" /></Field>
              <Field label="Additional allowed redirect URLs">
                <TagInput value={field("redirect_urls")} onChange={(v) => set({ redirect_urls: v })} placeholder="https://staging.example.com/auth/callback" />
              </Field>
            </Section>

            </>}
            {parts.includes("providers") && <ProvidersSection value={field("providers")} onChange={(providers) => set({ providers })} />}
            {parts.includes("emails") && <EmailsSection value={field("emails")} onChange={(emails) => set({ emails })} />}
          </div>
        )}
      </Loading>
    </Card>
  );
}

function ProvidersSection({ value, onChange }) {
  const names = Object.keys(value);
  const [adding, setAdding] = useState("");
  const update = (name, patch) => onChange({ ...value, [name]: { ...value[name], ...patch } });
  const remove = (name) => { const next = { ...value }; delete next[name]; onChange(next); };
  const add = (name) => { if (name && !value[name]) onChange({ ...value, [name]: { client_id: "", client_secret: "", enabled: true } }); setAdding(""); };
  const options = KNOWN_PROVIDERS.filter((p) => !names.includes(p));
  return (
    <Section title="OAuth providers" description="Social sign-in. Reference credentials as secret://NAME rather than pasting them here.">
      {names.length === 0 && <p className="muted" style={{ margin: 0 }}>No providers configured.</p>}
      <div className="stack" style={{ gap: 10 }}>
        {names.map((name) => {
          const p = value[name];
          const shipped = KNOWN_PROVIDERS.includes(name);
          return (
            <div key={name} className="card sunken" style={{ padding: 14 }}>
              <div className="row" style={{ justifyContent: "space-between", marginBottom: 10 }}>
                <b style={{ textTransform: "capitalize" }}>{name}</b>
                <div className="row">
                  <Switch size="sm" checked={p.enabled !== false} onChange={(v) => update(name, { enabled: v })} label="Enabled" />
                  <IconButton icon="trash" label={`Remove ${name}`} onClick={() => remove(name)} />
                </div>
              </div>
              <div className="grid two">
                <Field label="Client ID"><input value={p.client_id || ""} onChange={(e) => update(name, { client_id: e.target.value })} /></Field>
                <Field label="Client secret"><input value={p.client_secret || ""} onChange={(e) => update(name, { client_secret: e.target.value })} placeholder="secret://GITHUB_SECRET" /></Field>
              </div>
              {!shipped && (
                <div className="grid two" style={{ marginTop: 10 }}>
                  <Field label="Authorize endpoint"><input value={p.authorize_endpoint || ""} onChange={(e) => update(name, { authorize_endpoint: e.target.value })} /></Field>
                  <Field label="Token endpoint"><input value={p.token_endpoint || ""} onChange={(e) => update(name, { token_endpoint: e.target.value })} /></Field>
                  <Field label="Userinfo endpoint"><input value={p.userinfo_endpoint || ""} onChange={(e) => update(name, { userinfo_endpoint: e.target.value })} /></Field>
                </div>
              )}
              <Field label="Scopes" className="mt-field">
                <TagInput value={p.scopes || []} onChange={(v) => update(name, { scopes: v })} placeholder="openid" />
              </Field>
            </div>
          );
        })}
      </div>
      <div className="row" style={{ marginTop: 10 }}>
        {options.length > 0 && (
          <select value="" onChange={(e) => add(e.target.value)}>
            <option value="">Add a provider…</option>
            {options.map((p) => <option key={p} value={p}>{p}</option>)}
          </select>
        )}
        <input value={adding} onChange={(e) => setAdding(e.target.value)} placeholder="custom-provider-name" style={{ width: 200 }} />
        <Button size="sm" disabled={!adding.trim()} onClick={() => add(adding.trim())}><Icon name="plus" />Add</Button>
      </div>
    </Section>
  );
}

function EmailsSection({ value, onChange }) {
  const [kind, setKind] = useState(EMAIL_KINDS[0][0]);
  const override = value[kind] || {};
  const meta = EMAIL_KINDS.find((k) => k[0] === kind);
  const set = (patch) => onChange({ ...value, [kind]: { ...override, ...patch } });
  const reset = () => { const next = { ...value }; delete next[kind]; onChange(next); };
  return (
    <Section title="Email templates" description="Subject and body for each auth email. Leave blank to use the built-in default.">
      <Tabs value={kind} onChange={setKind} tabs={EMAIL_KINDS.map(([v, label]) => ({ value: v, label }))} />
      <div style={{ marginTop: 12 }}>
        <Field label="Subject" hint={`Default: “${meta[2]}”. Placeholders: {project} {email} {link}${kind === "invite" ? " {organization} {role}" : ""}.`}>
          <input value={override.subject || ""} onChange={(e) => set({ subject: e.target.value })} placeholder={meta[2]} />
        </Field>
        <Field label="Body" hint="Plain text; a line becomes a paragraph. {link} is turned into a clickable link automatically.">
          <textarea rows={5} value={override.text || ""} onChange={(e) => set({ text: e.target.value })} />
        </Field>
        {(override.subject || override.text) && (
          <Button size="sm" onClick={reset}>Reset to default</Button>
        )}
      </div>
    </Section>
  );
}
