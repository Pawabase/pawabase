import { useMemo, useState } from "react";
import { Badge, Button, Field, Sheet, Switch, Table, useAction } from "./ui";
import { post } from "../lib/api";
import { coerce, readTable } from "../lib/tabular";
import { envPath } from "../lib/api";

const CHUNK = 500;

/** Bring rows from a CSV or JSON file into a resource: choose the file, match columns to fields, check it, import it. */
export default function ImportWizard({ env, resource, onClose, onDone }) {
  const base = envPath(env, `/resources/${resource.name}/import`);
  const [step, setStep] = useState(0);
  const [table, setTable] = useState(null);
  const [fileName, setFileName] = useState("");
  const [problem, setProblem] = useState("");
  const [mapping, setMapping] = useState({});
  const [mode, setMode] = useState("insert");
  const [events, setEvents] = useState(false);
  const [report, setReport] = useState(null);
  const [progress, setProgress] = useState(null);
  const [run, busy] = useAction();

  const key = resource.primary_key || "id";
  const targets = useMemo(() => {
    const own = (resource.fields || []).map((f) => ({ name: f.name, type: f.type, required: Boolean(f.required) }));
    return [{ name: key, type: "string", required: false, key: true }, ...own.filter((f) => f.name !== key)];
  }, [resource, key]);

  const choose = async (file) => {
    setProblem(""); setTable(null); setReport(null);
    if (!file) return;
    try {
      const parsed = readTable(file.name, await file.text());
      if (!parsed.rows.length) throw new Error("There are no rows in that file.");
      setTable(parsed); setFileName(file.name);
      const lower = Object.fromEntries(parsed.columns.map((c) => [c.toLowerCase().replace(/[\s-]+/g, "_"), c]));
      setMapping(Object.fromEntries(targets.map((t) => [t.name, lower[t.name.toLowerCase()] || ""]).filter(([, source]) => source)));
    } catch (error) {
      setProblem(error.message || "That file could not be read.");
    }
  };

  const prepared = useMemo(() => {
    if (!table) return [];
    return table.rows.map((row) => {
      const out = {};
      for (const target of targets) {
        const source = mapping[target.name];
        if (!source) continue;
        const value = coerce(row[source], target.type);
        if (value !== undefined) out[target.name] = value;
      }
      return out;
    });
  }, [table, mapping, targets]);

  const missing = targets.filter((t) => t.required && !mapping[t.name]);
  const send = async (dry) => {
    const total = { created: 0, updated: 0, failed: 0, errors: [], dry_run: dry };
    const result = await run(async () => {
      for (let at = 0; at < prepared.length; at += CHUNK) {
        setProgress(Math.min(prepared.length, at + CHUNK));
        const part = await post(base, { rows: prepared.slice(at, at + CHUNK), mode, dry_run: dry, emit_events: events });
        total.created += part.created; total.updated += part.updated; total.failed += part.failed;
        total.errors.push(...part.errors.map((e) => ({ ...e, row: e.row + at })));
      }
      return total;
    });
    setProgress(null);
    if (result && result !== true) { setReport(result); if (!dry) onDone(); }
  };

  const steps = ["File", "Columns", "Check and import"];
  const footer = (
    <>
      {step > 0 && <Button disabled={busy} onClick={() => { setStep(step - 1); setReport(null); }}>Back</Button>}
      <span className="grow" />
      {step === 0 && <Button variant="primary" disabled={!table} onClick={() => setStep(1)}>Next</Button>}
      {step === 1 && <Button variant="primary" disabled={missing.length > 0 || Object.keys(mapping).length === 0} onClick={() => setStep(2)}>Next</Button>}
      {step === 2 && <>
        <Button disabled={busy} onClick={() => send(true)}>Check only</Button>
        <Button variant="primary" disabled={busy} onClick={() => send(false)}>{busy ? "Importing…" : `Import ${prepared.length.toLocaleString()} row${prepared.length === 1 ? "" : "s"}`}</Button>
      </>}
    </>
  );

  return (
    <Sheet title={`Import into ${resource.name}`} subtitle={steps.map((s, i) => (i === step ? `${i + 1}. ${s}` : null)).filter(Boolean)[0]} icon="resources" onClose={onClose} footer={footer}>
      {step === 0 && (
        <>
          <Field label="CSV or JSON file" hint="A CSV needs a header row. A JSON file is an array of objects. Up to a few hundred thousand rows works in the browser.">
            <input type="file" accept=".csv,.tsv,.json,text/csv,application/json" onChange={(e) => choose(e.target.files[0])} />
          </Field>
          {problem && <div className="alert error">{problem}</div>}
          {table && (
            <>
              <p className="muted" style={{ margin: 0 }}><b>{fileName}</b>: {table.rows.length.toLocaleString()} row{table.rows.length === 1 ? "" : "s"}, {table.columns.length} column{table.columns.length === 1 ? "" : "s"}.</p>
              <div className="card flush"><Table rows={table.rows.slice(0, 5)} columns={table.columns.slice(0, 8).map((c) => ({ label: c, render: (r) => String(r[c] ?? "").slice(0, 60) }))} /></div>
            </>
          )}
        </>
      )}
      {step === 1 && (
        <>
          <p className="muted" style={{ margin: 0 }}>Match each field to a column in your file. Leave a field unmatched to leave it empty (or to use its default). Cells that are empty are skipped.</p>
          <div className="stack">
            {targets.map((t) => (
              <div key={t.name} className="map-row">
                <span className="map-field"><b>{t.name}</b> <span className="faint">{t.key ? "key" : t.type}</span>{t.required && <Badge tone="red">required</Badge>}</span>
                <select value={mapping[t.name] || ""} onChange={(e) => setMapping((m) => ({ ...m, [t.name]: e.target.value }))} aria-label={`Column for ${t.name}`}>
                  <option value="">{t.required ? "Choose a column" : "Not imported"}</option>
                  {table.columns.map((c) => <option key={c}>{c}</option>)}
                </select>
              </div>
            ))}
          </div>
          {missing.length > 0 && <div className="alert warn">Match the required field{missing.length > 1 ? "s" : ""}: {missing.map((t) => t.name).join(", ")}.</div>}
        </>
      )}
      {step === 2 && (
        <>
          <Field label="When a row has a key that already exists">
            <select value={mode} onChange={(e) => { setMode(e.target.value); setReport(null); }}>
              <option value="insert">Add every row as a new record</option>
              <option value="upsert">Update the existing record, and add the rest</option>
            </select>
          </Field>
          <Switch checked={events} onChange={(v) => { setEvents(v); setReport(null); }} label="Run events, flows and realtime for each row" hint="Off by default: a bulk import should not send a welcome email for every row." />
          <p className="hint" style={{ margin: 0 }}>Every row is checked against the resource's field rules before anything is written. Rows that fail are skipped and listed; the rest are imported.</p>
          {progress !== null && <div className="alert info">Working… {progress.toLocaleString()} of {prepared.length.toLocaleString()} rows.</div>}
          {report && (
            <div className={`alert ${report.failed ? "warn" : "ok"}`}>
              <b>{report.dry_run ? "Check complete. Nothing was written." : "Import complete."}</b>{" "}
              {report.created.toLocaleString()} {report.dry_run ? "would be added" : "added"}, {report.updated.toLocaleString()} {report.dry_run ? "would be updated" : "updated"}, {report.failed.toLocaleString()} {report.failed ? "failed" : "failed"}.
            </div>
          )}
          {report?.errors?.length > 0 && (
            <div className="card flush"><Table rows={report.errors.slice(0, 50)} columns={[{ label: "Row", render: (e) => e.row }, { label: "Problem", render: (e) => <span className="error-text">{e.error}</span> }]} /></div>
          )}
          {report?.errors?.length > 50 && <p className="hint" style={{ margin: 0 }}>Showing the first 50 problems.</p>}
        </>
      )}
    </Sheet>
  );
}
