import { useState } from "react";
import { Button, Card, useAction } from "../../../components/ui";
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
    if (await run(() => patch(envPath(env), { auth: value }), "Saved")) { state.reload(); setDraft(undefined); }
  };
  return { state, value, set, dirty: draft !== undefined, save, busy, discard: () => setDraft(undefined) };
}

/** A settings card with its own Save bar, for a page that edits part of the auth configuration. */
export function SettingsPanel({ title, description, config, children }) {
  return (
    <Card title={title} actions={config.dirty && <>
      <Button size="sm" disabled={config.busy} onClick={config.discard}>Discard</Button>
      <Button size="sm" variant="primary" disabled={config.busy} onClick={config.save}>Save changes</Button>
    </>}>
      {description && <p className="muted" style={{ margin: "0 0 16px", maxWidth: 640 }}>{description}</p>}
      <div className="stack lg">{children}</div>
    </Card>
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
