// Editing an environment's settings (`PATCH /envs/{env}` with `settings`), shared by every page that owns a few of them:
// Networking (API access), Realtime, Secrets (plain values) and Settings itself. Each page edits only its own keys, but
// they all save through the same diff, so a key one page does not show is never touched.

import { useEffect, useState } from "react";
import { Button, useAction } from "./ui";
import { envPath, patch, useApi } from "../lib/api";

/** Written by the platform, never by a client. */
export const RESERVED_KEYS = ["deletion_pending"];
/** Settings the platform itself reads. Anything else is a plain value flows and policies read as `$settings.<key>`. */
export const KNOWN_KEYS = ["public_docs", "cors_origins", "ip_allowlist", "maintenance", "realtime", "timezone", "currency", "locale", "description", "app"];

const same = (a, b) => JSON.stringify(a ?? null) === JSON.stringify(b ?? null);

/** "Default" in a policy picker is `null`, but the server applies its own default only when the key is absent. */
export function omitNulls(value) {
  if (Array.isArray(value)) return value.map(omitNulls);
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).filter(([, v]) => v !== null && v !== undefined).map(([k, v]) => [k, omitNulls(v)]));
  }
  return value;
}

/** What to send: changed keys with their new value, removed keys as null (the API clears a null). */
export function diff(original, draft) {
  const out = {};
  for (const key of new Set([...Object.keys(original), ...Object.keys(draft)])) {
    if (RESERVED_KEYS.includes(key)) continue;
    const next = draft[key] === "" ? undefined : draft[key];
    if (same(original[key], next)) continue;
    out[key] = next === undefined ? null : omitNulls(next);
  }
  return out;
}

export function useEnvSettings(env) {
  const path = envPath(env);
  const state = useApi(path);
  const original = state.data?.settings || {};
  const [draft, setDraft] = useState(null);
  const [run, busy] = useAction();
  // A fresh copy whenever what is saved changes (after a save, or a change made elsewhere).
  useEffect(() => { if (state.data) setDraft(state.data.settings || {}); }, [state.data?.version, JSON.stringify(state.data?.settings)]); // eslint-disable-line react-hooks/exhaustive-deps
  const changes = draft ? diff(original, draft) : {};
  const count = Object.keys(changes).length;
  useEffect(() => {
    if (!count) return undefined;
    const warn = (e) => { e.preventDefault(); e.returnValue = ""; };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [count]);
  return {
    state,
    path,
    original,
    draft: draft || {},
    ready: Boolean(state.data && draft),
    set: (values) => setDraft((d) => ({ ...(d || {}), ...values })),
    replace: setDraft,
    count,
    busy,
    discard: () => setDraft(original),
    save: async () => { if (await run(() => patch(path, { settings: changes }), "Saved")) state.reload(); },
  };
}

/** Discard and Save, shown only once something changed. */
export function SaveButtons({ editor }) {
  return (
    <>
      {editor.count > 0 && <Button size="sm" disabled={editor.busy} onClick={editor.discard}>Discard</Button>}
      <Button variant="primary" size="sm" disabled={editor.busy || editor.count === 0} onClick={editor.save}>{editor.count ? `Save ${editor.count} change${editor.count === 1 ? "" : "s"}` : "Save"}</Button>
    </>
  );
}
