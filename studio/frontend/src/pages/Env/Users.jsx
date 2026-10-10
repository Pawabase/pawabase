import { useState } from "react";
import Layout, { envHref } from "../../components/Layout";
import { Icon } from "../../components/icons";
import { Badge, Button, Card, Field, IconButton, Json, JsonInput, Loading, Modal, PageHead, Section, Segmented, Sheet, Switch, Table, TagInput, Tabs, useAction, when } from "../../components/ui";
import { PermissionPicker, RolePicker } from "../../components/AccessPickers";
import { api, del, envPath, patch, post, put, useApi } from "../../lib/api";
import Domains from "./auth/Domains";
import Invitations, { InviteSheet } from "./auth/Invitations";
import Mfa from "./auth/Mfa";
import Passwords from "./auth/Passwords";
import Protection from "./auth/Protection";
import Restrictions from "./auth/Restrictions";
import Sessions from "./auth/Sessions";
import { AUTH, AUTH_DEFAULTS, Avatar, device } from "./auth/shared";
import Transfer from "./auth/Transfer";


const AUTH_PARTS = [
  ["users", "Users"],
  ["orgs", "Organizations"],
  ["invitations", "Invitations"],
  ["roles", "Roles"],
  ["providers", "Social providers"],
  ["oauth", "OAuth servers"],
  ["passwords", "Passwords"],
  ["mfa", "Multi-factor"],
  ["sessions", "Sessions"],
  ["protection", "Attack protection"],
  ["restrictions", "Restrictions"],
  ["domains", "Domains"],
  ["events", "Sign-in activity"],
  ["transfer", "Import & export"],
  ["emails", "Email templates"],
  ["config", "Configuration"],
];
const AUTH_LABELS = Object.fromEntries(AUTH_PARTS);
const AUTH_ABOUT = {
  users: "Everyone who has signed up to this environment. Open a person to manage their profile, security and access.",
  orgs: "Companies, teams and workspaces your users belong to.",
  invitations: "People who have been invited and have not joined yet.",
  roles: "Named bundles of permissions you can give to people.",
  providers: "Let people sign in with an account they already have. Choose a provider to set it up.",
  oauth: "Your own OAuth or OpenID Connect servers, for an identity provider that is not on the list.",
  passwords: "How strong passwords must be, and signing in without one.",
  mfa: "A second step at sign-in, and who has set it up.",
  sessions: "Everyone who is signed in right now, and how long a sign-in lasts.",
  protection: "Slow down guessing: lock accounts after repeated failures, and see who is locked.",
  restrictions: "Who may create an account, and with which email addresses.",
  domains: "Where a browser may be sent after signing in.",
  events: "Every sign-in, refresh and failure, newest first.",
  transfer: "Move people, roles and organizations between environments.",
  emails: "The messages Pawabase sends for verification, recovery and invitations.",
  config: "Everything above in one place, for people who prefer a single form.",
};

export default function Users({ env }) {
  const [tab, setTab] = useState("users");
  const base = `/envs/${env}`;
  const subnav = {
    title: "Users & auth",
    groups: [{ items: [...AUTH_PARTS.map(([key, label]) => ({ key, label })), { key: "policies", label: "Policies", href: envHref(env, "policies") }] }],
    active: tab,
    onSelect: setTab,
  };
  return (
    <Layout title="Users & auth" subnav={subnav}>
      <PageHead title={AUTH_LABELS[tab]} description={AUTH_ABOUT[tab]} />
      {tab === "users" && <UserList base={base} />}
      {tab === "orgs" && <Orgs base={base} />}
      {tab === "invitations" && <Invitations base={base} />}
      {tab === "roles" && <Roles base={base} />}
      {tab === "providers" && <Providers env={env} kind="social" />}
      {tab === "oauth" && <Providers env={env} kind="custom" />}
      {tab === "passwords" && <Passwords env={env} />}
      {tab === "mfa" && <Mfa env={env} base={base} />}
      {tab === "sessions" && <Sessions env={env} base={base} />}
      {tab === "protection" && <Protection env={env} base={base} />}
      {tab === "restrictions" && <Restrictions env={env} />}
      {tab === "domains" && <Domains env={env} />}
      {tab === "events" && <Events base={base} />}
      {tab === "transfer" && <Transfer env={env} base={base} />}
      {tab === "emails" && <AuthConfig env={env} parts={["emails"]} title="Email templates" />}
      {tab === "config" && <AuthConfig env={env} parts={["general", "providers", "emails"]} title="Configuration" />}
    </Layout>
  );
}

const BRAND_ICONS = {
  github: <path d="M12 .297c-6.63 0-12 5.373-12 12 0 5.303 3.438 9.8 8.205 11.385.6.113.82-.258.82-.577 0-.285-.01-1.04-.015-2.04-3.338.724-4.042-1.61-4.042-1.61C4.422 18.07 3.633 17.7 3.633 17.7c-1.087-.744.084-.729.084-.729 1.205.084 1.838 1.236 1.838 1.236 1.07 1.835 2.809 1.305 3.495.998.108-.776.417-1.305.76-1.605-2.665-.3-5.466-1.332-5.466-5.93 0-1.31.465-2.38 1.235-3.22-.135-.303-.54-1.523.105-3.176 0 0 1.005-.322 3.3 1.23.96-.267 1.98-.399 3-.405 1.02.006 2.04.138 3 .405 2.28-1.552 3.285-1.23 3.285-1.23.645 1.653.24 2.873.12 3.176.765.84 1.23 1.91 1.23 3.22 0 4.61-2.805 5.625-5.475 5.92.42.36.81 1.096.81 2.22 0 1.606-.015 2.896-.015 3.286 0 .315.21.69.825.57C20.565 22.092 24 17.592 24 12.297c0-6.627-5.373-12-12-12" />,
  google: <path d="M12.48 10.92v3.28h7.84c-.24 1.84-.853 3.187-1.787 4.133-1.147 1.147-2.933 2.4-6.053 2.4-4.827 0-8.6-3.893-8.6-8.72s3.773-8.72 8.6-8.72c2.6 0 4.507 1.027 5.907 2.347l2.307-2.307C18.747 1.44 16.133 0 12.48 0 5.867 0 .307 5.387.307 12s5.56 12 12.173 12c3.573 0 6.267-1.173 8.373-3.36 2.16-2.16 2.84-5.213 2.84-7.667 0-.76-.053-1.467-.173-2.053H12.48z" />,
  microsoft: <path d="M1 1h10.5v10.5H1zM12.5 1H23v10.5H12.5zM1 12.5h10.5V23H1zM12.5 12.5H23V23H12.5z" />,
  discord: <path d="M20.317 4.37a19.791 19.791 0 0 0-4.885-1.515.074.074 0 0 0-.079.037c-.21.375-.444.864-.608 1.25a18.27 18.27 0 0 0-5.487 0 12.64 12.64 0 0 0-.617-1.25.077.077 0 0 0-.079-.037A19.736 19.736 0 0 0 3.677 4.37a.07.07 0 0 0-.032.027C.533 9.046-.32 13.58.099 18.057a.082.082 0 0 0 .031.057 19.9 19.9 0 0 0 5.993 3.03.078.078 0 0 0 .084-.028 14.09 14.09 0 0 0 1.226-1.994.076.076 0 0 0-.041-.106 13.107 13.107 0 0 1-1.872-.892.077.077 0 0 1-.008-.128 10.2 10.2 0 0 0 .372-.292.074.074 0 0 1 .077-.01c3.928 1.793 8.18 1.793 12.062 0a.074.074 0 0 1 .078.01c.12.098.246.198.373.292a.077.077 0 0 1-.006.127 12.299 12.299 0 0 1-1.873.892.077.077 0 0 0-.041.107c.36.698.772 1.362 1.225 1.993a.076.076 0 0 0 .084.028 19.839 19.839 0 0 0 6.002-3.03.077.077 0 0 0 .032-.054c.5-5.177-.838-9.674-3.549-13.66a.061.061 0 0 0-.031-.03zM8.02 15.33c-1.183 0-2.157-1.085-2.157-2.419 0-1.333.956-2.419 2.157-2.419 1.21 0 2.176 1.096 2.157 2.42 0 1.333-.956 2.418-2.157 2.418zm7.975 0c-1.183 0-2.157-1.085-2.157-2.419 0-1.333.955-2.419 2.157-2.419 1.21 0 2.176 1.096 2.157 2.42 0 1.333-.946 2.418-2.157 2.418z" />,
};

function BrandIcon({ name, size = 28 }) {
  return BRAND_ICONS[name] ? <svg viewBox="0 0 24 24" width={size} height={size} fill="currentColor" aria-hidden>{BRAND_ICONS[name]}</svg> : <Icon name="keys" size={size - 4} />;
}

/** Sign-in providers for an environment. `social` is a grid of the providers Pawabase ships; `custom` lists your own OAuth servers. */
function Providers({ env, kind }) {
  const envState = useApi(envPath(env));
  const [editing, setEditing] = useState(null);
  const [run, busy] = useAction();
  const auth = { ...AUTH_DEFAULTS, ...(envState.data?.auth || {}) };
  const providers = auth.providers || {};
  const save = async (next) => {
    if (await run(() => patch(envPath(env), { auth: { ...auth, providers: next } }), "Saved")) { envState.reload(); setEditing(null); return true; }
    return false;
  };
  const customNames = Object.keys(providers).filter((name) => !KNOWN_PROVIDERS.includes(name));
  const configured = (name) => Boolean(providers[name]?.client_id);
  return (
    <>
      <Loading state={envState}>
        {() => kind === "social" ? (
          <div className="provider-grid">
            {KNOWN_PROVIDERS.map((name) => (
              <button key={name} type="button" className="provider-tile" onClick={() => setEditing({ name })}>
                <span className="provider-icon"><BrandIcon name={name} /></span>
                <b>{name === "github" ? "GitHub" : name[0].toUpperCase() + name.slice(1)}</b>
                {providers[name] ? (providers[name].enabled === false ? <Badge>disabled</Badge> : configured(name) ? <Badge tone="green">enabled</Badge> : <Badge tone="yellow">needs credentials</Badge>) : <span className="faint small">Not set up</span>}
              </button>
            ))}
          </div>
        ) : (
          <Card flush title="OAuth servers" actions={<Button variant="primary" size="sm" onClick={() => setEditing({ name: "", isNew: true })}><Icon name="plus" size={14} /> Add OAuth server</Button>}>
            <Table
              rows={customNames.map((name) => ({ name, ...providers[name] }))}
              onRowClick={(row) => setEditing({ name: row.name })}
              empty="No OAuth servers. Add one with its authorize, token and userinfo endpoints."
              columns={[
                { label: "Name", render: (row) => <b>{row.name}</b> },
                { label: "Authorize endpoint", render: (row) => <code>{row.authorize_endpoint || "—"}</code> },
                { label: "State", render: (row) => (row.enabled === false ? <Badge>disabled</Badge> : <Badge tone="green">enabled</Badge>) },
              ]}
            />
          </Card>
        )}
      </Loading>
      {editing && <ProviderSheet name={editing.name} isNew={editing.isNew} shipped={KNOWN_PROVIDERS.includes(editing.name)} value={providers[editing.name]} busy={busy} taken={Object.keys(providers)} onClose={() => setEditing(null)} onSave={(name, value) => save({ ...providers, [name]: value })} onRemove={(name) => { const next = { ...providers }; delete next[name]; return save(next); }} />}
    </>
  );
}

function ProviderSheet({ name, isNew, shipped, value, busy, taken, onClose, onSave, onRemove }) {
  const [label, setLabel] = useState(name);
  const [draft, setDraft] = useState({ client_id: "", client_secret: "", enabled: true, scopes: [], ...(value || {}) });
  const set = (patchValues) => setDraft((d) => ({ ...d, ...patchValues }));
  const key = isNew ? label.trim().toLowerCase().replace(/[^a-z0-9_-]/g, "-") : name;
  const nameTaken = isNew && taken.includes(key);
  const needsEndpoints = !shipped;
  const complete = key && !nameTaken && draft.client_id && (!needsEndpoints || (draft.authorize_endpoint && draft.token_endpoint && draft.userinfo_endpoint));
  return (
    <Modal title={isNew ? "Add OAuth server" : shipped ? `${name === "github" ? "GitHub" : name[0].toUpperCase() + name.slice(1)} sign-in` : name} onClose={onClose} footer={<>
      {!isNew && value && <Button variant="danger" disabled={busy} onClick={() => confirm(`Remove ${name}?`) && onRemove(name)}>Remove</Button>}
      <span className="grow" />
      <Button variant="primary" disabled={busy || !complete} onClick={() => onSave(key, draft)}>Save</Button>
    </>}>
      {shipped && <div className="provider-head"><span className="provider-icon"><BrandIcon name={name} /></span><p className="muted">Reference credentials as <code>secret://NAME</code> rather than pasting them here.</p></div>}
      {isNew && <Field label="Name" hint="Used in the sign-in address, like /auth/v1/authorize/{environment}/name."><input autoFocus value={label} onChange={(e) => setLabel(e.target.value)} placeholder="acme-sso" />{nameTaken && <span className="error-text">That name is taken.</span>}</Field>}
      <Switch checked={draft.enabled !== false} onChange={(v) => set({ enabled: v })} label="Enabled" hint="Off hides the provider without losing its settings." />
      <div className="grid two">
        <Field label="Client ID"><input value={draft.client_id || ""} onChange={(e) => set({ client_id: e.target.value })} /></Field>
        <Field label="Client secret"><input value={draft.client_secret || ""} onChange={(e) => set({ client_secret: e.target.value })} placeholder="secret://CLIENT_SECRET" /></Field>
      </div>
      {needsEndpoints && (
        <>
          <Field label="Authorize endpoint"><input value={draft.authorize_endpoint || ""} onChange={(e) => set({ authorize_endpoint: e.target.value })} placeholder="https://idp.example.com/oauth/authorize" /></Field>
          <Field label="Token endpoint"><input value={draft.token_endpoint || ""} onChange={(e) => set({ token_endpoint: e.target.value })} placeholder="https://idp.example.com/oauth/token" /></Field>
          <Field label="Userinfo endpoint"><input value={draft.userinfo_endpoint || ""} onChange={(e) => set({ userinfo_endpoint: e.target.value })} placeholder="https://idp.example.com/oauth/userinfo" /></Field>
        </>
      )}
      <Field label="Scopes"><TagInput value={draft.scopes || []} onChange={(v) => set({ scopes: v })} placeholder="openid" /></Field>
    </Modal>
  );
}

const USER_FILTERS = [["", "All"], ["unverified", "Unverified"], ["disabled", "Disabled"], ["locked", "Locked"], ["mfa", "MFA on"], ["no_mfa", "MFA off"]];
const USER_PAGE = 50;

function UserStatus({ u }) {
  return (
    <span className="row wrap" style={{ gap: 6 }}>
      {u.disabled ? <Badge tone="red">disabled</Badge> : u.locked_until && new Date(u.locked_until) > new Date() ? <Badge tone="yellow">locked</Badge> : <Badge tone="green">active</Badge>}
      {!u.email_verified && <Badge tone="yellow">unverified</Badge>}
      {u.mfa_enabled && <Badge tone="blue">MFA</Badge>}
    </span>
  );
}

function UserList({ base }) {
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(0);
  const users = useApi(`${base}/users`, { ...AUTH, params: { search, status, limit: USER_PAGE, offset: page * USER_PAGE } });
  const [selected, setSelected] = useState(null);
  const [creating, setCreating] = useState(false);
  const total = users.data?.total ?? 0;
  const from = total ? page * USER_PAGE + 1 : 0;
  const to = Math.min(total, (page + 1) * USER_PAGE);
  return (
    <div className="stack lg">
      <div className="user-bar">
        <div className="subnav-search user-search">
          <Icon name="search" size={14} />
          <input placeholder="Search by email, name or username" value={search} onChange={(e) => { setSearch(e.target.value); setPage(0); }} aria-label="Search users" />
        </div>
        <Segmented options={USER_FILTERS} value={status} onChange={(v) => { setStatus(v); setPage(0); }} />
        <span className="grow" />
        <Button variant="primary" size="sm" onClick={() => setCreating(true)}><Icon name="plus" size={14} /> New user</Button>
      </div>
      <Card flush>
        <Loading state={users} empty="No users match.">
          {(data) => (
            <>
              <Table
                rows={data.data}
                onRowClick={setSelected}
                empty={search || status ? "No users match." : "No users yet. They appear here when someone signs up, or you create one."}
                columns={[
                  { label: "User", render: (u) => <span className="row" style={{ gap: 12 }}><Avatar text={u.name || u.email} /><span className="stack sm" style={{ gap: 0 }}><b>{u.name || u.email}</b>{u.name && <span className="muted small">{u.email}</span>}</span></span> },
                  { label: "Roles", render: (u) => <div className="row wrap" style={{ gap: 4 }}>{(u.roles || []).length ? u.roles.map((r) => <Badge key={r}>{r}</Badge>) : <span className="faint">—</span>}</div> },
                  { label: "Status", render: (u) => <UserStatus u={u} /> },
                  { label: "Sign-in", render: (u) => <span className="row" style={{ gap: 6 }}>{(u.identities || []).map((i) => <Badge key={i.id}>{i.provider}</Badge>)}{u.has_password !== false && <Badge>password</Badge>}</span> },
                  { label: "Last sign-in", render: (u) => (u.last_sign_in_at ? when(u.last_sign_in_at) : <span className="faint">never</span>) },
                  { label: "Joined", render: (u) => when(u.created_at) },
                ]}
              />
              {total > USER_PAGE && (
                <div className="pager">
                  <span className="muted small">{from}–{to} of {total}</span>
                  <span className="row"><Button size="sm" disabled={page === 0} onClick={() => setPage(page - 1)}>Previous</Button><Button size="sm" disabled={to >= total} onClick={() => setPage(page + 1)}>Next</Button></span>
                </div>
              )}
            </>
          )}
        </Loading>
      </Card>
      {selected && <UserDetail base={base} id={selected.id} onClose={() => { setSelected(null); users.reload(); }} />}
      {creating && <NewUser base={base} onClose={() => setCreating(false)} onCreated={() => { setCreating(false); users.reload(); }} />}
    </div>
  );
}

const USER_TABS = [{ value: "profile", label: "Profile" }, { value: "security", label: "Security" }, { value: "access", label: "Access" }, { value: "orgs", label: "Organizations" }, { value: "activity", label: "Activity" }];

function UserDetail({ base, id, onClose }) {
  const user = useApi(`${base}/users/${id}`, AUTH);
  const history = useApi(`${base}/users/${id}/history`, AUTH);
  const [tab, setTab] = useState("profile");
  const [run, busy] = useAction();
  const act = async (fn, label) => { if (await run(fn, label)) user.reload(); };
  const u = user.data;
  const locked = u?.locked_until && new Date(u.locked_until) > new Date();
  return (
    <Sheet
      title={u ? (u.name || u.email) : "User"}
      subtitle={u?.name ? u.email : undefined}
      icon="users"
      tabs={USER_TABS}
      tab={tab}
      onTab={setTab}
      onClose={onClose}
      footer={<Button onClick={onClose}>Done</Button>}
    >
      <Loading state={user}>
        {() => (
          <>
            {tab === "profile" && <UserProfile base={base} id={id} u={u} act={act} busy={busy} />}
            {tab === "security" && (
              <>
                <section className="form-section">
                  <div className="form-section-head"><div><h3>Account</h3><p><UserStatus u={u} /></p></div></div>
                  <div className="row wrap">
                    <Button disabled={busy} onClick={() => act(() => patch(`${base}/users/${id}`, { disabled: !u.disabled }, AUTH), u.disabled ? "Enabled" : "Disabled")}>{u.disabled ? "Enable account" : "Disable account"}</Button>
                    {locked && <Button disabled={busy} onClick={() => act(() => patch(`${base}/users/${id}`, { unlock: true }, AUTH), "Unlocked")}>Unlock</Button>}
                    {!u.email_verified && <Button disabled={busy} onClick={() => act(() => patch(`${base}/users/${id}`, { email_verified: true }, AUTH), "Marked verified")}>Mark email verified</Button>}
                  </div>
                  {locked && <p className="hint" style={{ margin: "10px 0 0" }}>Locked until {when(u.locked_until)} after repeated failed sign-ins.</p>}
                </section>
                <section className="form-section">
                  <div className="form-section-head"><div><h3>Password</h3><p>{u.has_password ? "This person signs in with a password." : "No password is set. They sign in with a link or a social account."}</p></div></div>
                  <SetPassword busy={busy} onSet={(password) => act(() => patch(`${base}/users/${id}`, { password }, AUTH), "Password set")} />
                </section>
                <section className="form-section">
                  <div className="form-section-head"><div><h3>Second factor</h3><p>{u.mfa_enabled ? "An authenticator app is set up." : "None set up."}</p></div></div>
                  {u.mfa_enabled && <Button disabled={busy} onClick={() => confirm("Remove this person's second factors?") && act(() => post(`${base}/users/${id}/mfa/reset`, {}, AUTH), "Second factors removed")}>Remove second factors</Button>}
                </section>
                <section className="form-section">
                  <div className="form-section-head"><div><h3>Sessions</h3><p>Where this person is signed in.</p></div><Button size="sm" disabled={busy || !(u.sessions || []).length} onClick={() => act(() => post(`${base}/users/${id}/sessions/revoke`, {}, AUTH), "Signed out everywhere")}>Sign out everywhere</Button></div>
                  <Table rows={u.sessions || []} empty="No active sessions." columns={[
                    { label: "Device", render: (s) => device(s.user_agent) },
                    { label: "IP", render: (s) => <code>{s.ip || "—"}</code> },
                    { label: "Last active", render: (s) => when(s.last_seen_at || s.created_at) },
                    { label: "", render: (s) => <Button size="sm" disabled={busy} onClick={() => act(() => del(`${base}/users/${id}/sessions/${s.id}`, AUTH), "Session ended")}>End</Button> },
                  ]} />
                </section>
                <section className="form-section">
                  <div className="form-section-head"><div><h3>Delete user</h3><p>Signs them out, removes their linked accounts and releases the email address. Their history stays.</p></div></div>
                  <Button variant="danger" disabled={busy} onClick={async () => { if (confirm(`Delete ${u.email}?`) && await run(() => del(`${base}/users/${id}`, AUTH), "Deleted")) onClose(); }}>Delete user</Button>
                </section>
              </>
            )}
            {tab === "access" && <Grants base={base} id={id} user={u} onDone={user.reload} />}
            {tab === "orgs" && (
              <Table rows={u.organizations || []} empty="Not a member of any organization." columns={[
                { label: "Organization", render: (o) => <b>{o.name}</b> },
                { label: "Slug", render: (o) => <code>{o.slug}</code> },
                { label: "Role", render: (o) => <Badge>{o.role}</Badge> },
              ]} />
            )}
            {tab === "activity" && (
              <Loading state={history} empty="No activity.">
                {(h) => <Table rows={h.data} empty="No activity." columns={[{ label: "When", render: (e) => when(e.created_at) }, { label: "Event", key: "kind" }, { label: "Method", key: "method" }, { label: "Result", render: (e) => <Badge tone={e.success ? "green" : "red"}>{e.success ? "ok" : e.reason || "failed"}</Badge> }, { label: "IP", render: (e) => <code>{e.ip || "—"}</code> }]} />}
              </Loading>
            )}
          </>
        )}
      </Loading>
    </Sheet>
  );
}

function SetPassword({ busy, onSet }) {
  const [value, setValue] = useState("");
  return (
    <div className="row">
      <input type="password" value={value} onChange={(e) => setValue(e.target.value)} placeholder="New password" autoComplete="new-password" aria-label="New password" />
      <Button disabled={busy || !value} onClick={() => { onSet(value); setValue(""); }}>Set password</Button>
    </div>
  );
}

function UserProfile({ base, id, u, act, busy }) {
  const [name, setName] = useState(u.name || "");
  const [meta, setMeta] = useState({ user: u.user_metadata || {}, app: u.app_metadata || {} });
  const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
  return (
    <>
      <section className="form-section">
        <div className="form-section-head"><div><h3>Profile</h3><p>{u.email}{u.email_verified ? " · verified" : " · not verified"}</p></div></div>
        <div className="row">
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Name" aria-label="Name" />
          <Button disabled={busy || name === (u.name || "")} onClick={() => act(() => patch(`${base}/users/${id}`, { name }, AUTH), "Saved")}>Save name</Button>
        </div>
        <dl className="kv" style={{ marginTop: 14 }}>
          <dt>Username</dt><dd>{u.username}</dd>
          <dt>Joined</dt><dd>{when(u.created_at)}</dd>
          <dt>Last sign-in</dt><dd>{u.last_sign_in_at ? `${when(u.last_sign_in_at)}${u.last_sign_in_ip ? ` from ${u.last_sign_in_ip}` : ""}` : "never"}</dd>
          <dt>Linked accounts</dt><dd>{(u.identities || []).length ? <span className="row wrap" style={{ gap: 6 }}>{u.identities.map((i) => <Badge key={i.id}>{i.provider}</Badge>)}</span> : "none"}</dd>
        </dl>
      </section>
      <section className="form-section">
        <div className="form-section-head"><div><h3>Public metadata</h3><p>Their own profile data. The person and your application can read and change it.</p></div></div>
        <JsonInput value={meta.user} onChange={(v) => setMeta((m) => ({ ...m, user: v }))} rows={6} />
        <div style={{ marginTop: 10 }}><Button disabled={busy || same(meta.user, u.user_metadata || {})} onClick={() => act(() => patch(`${base}/users/${id}`, { user_metadata: meta.user }, AUTH), "Metadata saved")}>Save public metadata</Button></div>
      </section>
      <section className="form-section">
        <div className="form-section-head"><div><h3>Private metadata</h3><p>Only your servers can change this. It travels in the person's access token, so keep it small.</p></div></div>
        <JsonInput value={meta.app} onChange={(v) => setMeta((m) => ({ ...m, app: v }))} rows={6} />
        <div style={{ marginTop: 10 }}><Button disabled={busy || same(meta.app, u.app_metadata || {})} onClick={() => act(() => patch(`${base}/users/${id}`, { app_metadata: meta.app }, AUTH), "Metadata saved")}>Save private metadata</Button></div>
      </section>
    </>
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
  const [inviting, setInviting] = useState(false);
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
              <div className="form-section-head"><div><h3>Members</h3><p>Owners and admins manage the organization from the application. Here an operator can change roles directly.</p></div><Button size="sm" onClick={() => setInviting(true)}>Invite by email</Button></div>
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
                <Button onClick={() => setInviting(true)}>Invite by email</Button>
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
      {inviting && <InviteSheet base={base} org={slug} onClose={() => setInviting(false)} onSent={() => {}} />}
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

            <Section title="Security" description="Lockout and session limits.">
              <div className="grid two">
                <Field label="Failed attempts before a lock" hint="Empty uses the installation default."><input type="number" min={2} max={100} value={field("lockout_threshold") ?? ""} onChange={(e) => set({ lockout_threshold: e.target.value ? Number(e.target.value) : null })} /></Field>
                <Field label="Lock for (minutes)" hint="Empty uses the installation default."><input type="number" min={1} max={1440} value={field("lockout_minutes") ?? ""} onChange={(e) => set({ lockout_minutes: e.target.value ? Number(e.target.value) : null })} /></Field>
                <Field label="Sessions per user" hint="0 is unlimited."><input type="number" min={0} max={100} value={field("max_sessions") || 0} onChange={(e) => set({ max_sessions: Math.max(0, Number(e.target.value) || 0) })} /></Field>
              </div>
              <Field label="Only allow these email domains" hint="Empty allows any."><TagInput value={field("allowed_email_domains") || []} onChange={(v) => set({ allowed_email_domains: v })} placeholder="acme.com" /></Field>
              <Field label="Always block these email domains"><TagInput value={field("blocked_email_domains") || []} onChange={(v) => set({ blocked_email_domains: v })} placeholder="mailinator.com" /></Field>
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
