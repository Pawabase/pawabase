// Names of the things a definition can point at — policies, flows,
// schemas, transformers, resources — loaded once per open editor so pickers
// offer real choices instead of free text.
import { useEffect, useState } from "react";
import { envPath, get } from "../../lib/api";

const SOURCES = {
  policies: "/policies",
  flows: "/flows",
  schemas: "/schemas",
  transformers: "/transformers",
  resources: "/resources",
};

export function useRefs(env) {
  const [refs, setRefs] = useState({ loaded: false, policies: [], flows: [], schemas: [], transformers: [], resources: [], fields: {} });
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let live = true;
    const entries = Object.entries(SOURCES);
    Promise.allSettled(entries.map(([, path]) => get(envPath(env, path)))).then((results) => {
      if (!live) return;
      const next = { loaded: true, fields: {} };
      results.forEach((result, i) => {
        const [kind] = entries[i];
        const rows = result.status === "fulfilled" ? result.value?.data || [] : [];
        next[kind] = rows.map((row) => row.name).filter(Boolean);
        if (kind === "resources") {
          for (const row of rows) next.fields[row.name] = (row.fields || []).map((f) => f.name);
        }
      });
      setRefs(next);
    });
    return () => { live = false; };
  }, [env, tick]);
  return { ...refs, reload: () => setTick((n) => n + 1) };
}

/** A <select> over one kind of reference, keeping an unknown current value visible. */
export function RefSelect({ options = [], value, onChange, empty = "None", allowEmpty = true, placeholder }) {
  const list = value && !options.includes(value) ? [value, ...options] : options;
  return (
    <select value={value ?? ""} onChange={(e) => onChange(e.target.value || null)}>
      {(allowEmpty || !value) && <option value="">{placeholder && !allowEmpty ? placeholder : empty}</option>}
      {list.map((o) => <option key={o} value={o}>{o}</option>)}
    </select>
  );
}
