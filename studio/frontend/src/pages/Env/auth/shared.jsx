import { useState } from "react";
import { Icon } from "../../../components/icons";
import { Badge, Button, Card, Sheet, useAction } from "../../../components/ui";
import { envPath, patch, useApi } from "../../../lib/api";

export const AUTH = { service: "auth" };

// Every field Akountz's AuthConfig reads. A page that edits some of them still sends the whole block, so nothing is dropped.
export const AUTH_DEFAULTS = {
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
  lockout_threshold: null,
  lockout_minutes: null,
  allowed_email_domains: [],
  blocked_email_domains: [],
  max_sessions: 0,
  signup_form: { sign_in_with: ["email"], attributes: {}, custom: [], steps: "auto" },
  password_rules: {},
  directory: {},
  legal: {},
};

/** The environment's auth settings with a draft on top: change some fields, then save the whole block. */
export function useAuthConfig(env) {
  const state = useApi(envPath(env));
  const [draft, setDraft] = useState(undefined);
  const [run, busy] = useAction();
  const saved = { ...AUTH_DEFAULTS, ...(state.data?.auth || {}) };
  const value = { ...saved, ...(draft || {}) };
  const set = (values) => setDraft((current) => ({ ...(current || {}), ...values }));
  const save = async () => {
    const ok = await run(() => patch(envPath(env), { auth: value }), "Saved");
    if (ok) { state.reload(); setDraft(undefined); }
    return ok;
  };
  return { state, value, set, dirty: draft !== undefined, save, busy, discard: () => setDraft(undefined) };
}

/** A settings card with its own Save bar, for a page that edits part of the auth configuration. */
export function SettingsPanel({ title, description, config, icon, children }) {
  return (
    <Card title={icon ? <span className="panel-head"><Icon name={icon} size={17} />{title}</span> : title} actions={config.dirty && <>
      <Button size="sm" disabled={config.busy} onClick={config.discard}>Discard</Button>
      <Button size="sm" variant="primary" disabled={config.busy} onClick={config.save}>Save changes</Button>
    </>}>
      {description && <p className="muted" style={{ margin: "0 0 16px", maxWidth: 640 }}>{description}</p>}
      <div className="stack lg">{children}</div>
    </Card>
  );
}

/** The same settings in a side sheet, for a page whose list is the page and configuration is a detour from it. */
export function SettingsSheet({ title, description, icon, config, onClose, children }) {
  return (
    <Sheet title={title} subtitle={description} icon={icon} onClose={onClose} footer={<>
      {config.dirty && <Button disabled={config.busy} onClick={() => { config.discard(); onClose(); }}>Discard</Button>}
      <span className="grow" />
      <Button disabled={config.busy} onClick={onClose}>Close</Button>
      <Button variant="primary" disabled={config.busy || !config.dirty} onClick={async () => { if (await config.save()) onClose(); }}>Save changes</Button>
    </>}>
      <div className="stack lg">{children}</div>
    </Sheet>
  );
}

/** Opens a page's SettingsSheet from its list; "unsaved" means edits are waiting behind it. */
export function SettingsButton({ label, onClick, dirty }) {
  return (
    <Button size="sm" onClick={onClick}>
      <Icon name="settings" size={14} />{label}
      {dirty && <Badge tone="yellow">unsaved</Badge>}
    </Button>
  );
}

/** "15 minutes", "30 days": a length of time for people. */
export function duration(seconds) {
  const s = Number(seconds) || 0;
  if (s % 86400 === 0 && s >= 86400) return `${s / 86400} day${s / 86400 === 1 ? "" : "s"}`;
  if (s % 3600 === 0 && s >= 3600) return `${s / 3600} hour${s / 3600 === 1 ? "" : "s"}`;
  if (s % 60 === 0 && s >= 60) return `${s / 60} minute${s / 60 === 1 ? "" : "s"}`;
  return `${s} second${s === 1 ? "" : "s"}`;
}

/** A readable device from a user-agent string. */
export function device(agent) {
  if (!agent) return "Unknown device";
  const browser = /Edg\//.test(agent) ? "Edge" : /Firefox\//.test(agent) ? "Firefox" : /Chrome\//.test(agent) ? "Chrome" : /Safari\//.test(agent) ? "Safari" : /curl\//.test(agent) ? "curl" : /python|httpx|requests/i.test(agent) ? "Script" : null;
  const system = /Windows/.test(agent) ? "Windows" : /Android/.test(agent) ? "Android" : /iPhone|iPad/.test(agent) ? "iOS" : /Mac OS X/.test(agent) ? "macOS" : /Linux/.test(agent) ? "Linux" : null;
  return [browser, system].filter(Boolean).join(" on ") || agent.slice(0, 40);
}

/** Two letters for an avatar. */
export const initials = (text) => (text || "?").replace(/@.*/, "").split(/[\s._-]+/).filter(Boolean).slice(0, 2).map((part) => part[0]).join("").toUpperCase() || "?";

export function Avatar({ text, size = 32 }) {
  return <span className="user-avatar" style={{ width: size, height: size, fontSize: Math.round(size * 0.38) }}>{initials(text)}</span>;
}

export const DIRECTORY_DEFAULTS = {
  passwords: { max_length: 128, min_unique: 0, history: 0, max_age_days: 0, forbid_personal: false, breach_check: false },
  recovery: { enabled: true, link_minutes: 60, revoke_sessions: true, notify_change: true },
  mfa: { totp: true, email: false, backup_codes: true, backup_code_count: 10, email_code_minutes: 10, require: "off", grace_days: 0 },
  devices: { track: true, notify_new: false, remember_days: 0, max_per_user: 0 },
  api_keys: { enabled: false, max_per_user: 10, max_days: 365, scoped: true },
  claims: { mode: "all" },
};

/** One directory policy section, and a setter that changes some of its fields. */
export function useSection(config, name) {
  const current = { ...DIRECTORY_DEFAULTS[name], ...((config.value.directory || {})[name] || {}) };
  const set = (values) => config.set({ directory: { ...(config.value.directory || {}), [name]: { ...current, ...values } } });
  return [current, set];
}
