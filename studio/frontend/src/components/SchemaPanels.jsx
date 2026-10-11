import { useEffect, useState } from "react";
import { Badge, Button, Card, Field, Loading, Modal, useAction, when } from "./ui";
import { envPath, post, useApi } from "../lib/api";

const STATES = {
  in_sync: { label: "In sync", tone: "green" },
  pending: { label: "Not applied", tone: "yellow" },
  review: { label: "Needs review", tone: "yellow" },
  no_table: { label: "No table yet", tone: "yellow" },
};

const OPS = {
  create_table: "Create table",
  add_column: "Add column",
  add_index: "Add index",
  retype_column: "Change type",
  drop_column: "Remove column",
};

/** How a resource's table compares with its definition, as a badge. */
export function SchemaBadge({ report }) {
  if (!report) return <span className="muted">—</span>;
  const state = STATES[report.state] || STATES.in_sync;
  const count = report.state === "pending" || report.state === "review" ? ` · ${report.pending}` : "";
  return <Badge tone={state.tone}><span className="dot" />{state.label}{count}</Badge>;
}

/** The changes waiting to be applied to a resource's table, with the SQL that would run, and what is being kept for rollbacks. */
export function SchemaPanel({ env, name, onChanged }) {
  const path = envPath(env, `/resources/${name}/schema`);
  const state = useApi(path);
  const [run, busy] = useAction();
  const [chosen, setChosen] = useState(null);
  const [typed, setTyped] = useState("");
  const report = state.data;
  useEffect(() => {
    if (report) setChosen(new Set(report.changes.filter((c) => c.safe).map((c) => c.id)));
  }, [report]);
  const toggle = (id) => setChosen((current) => {
    const next = new Set(current);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });
  const destructive = report?.changes.filter((c) => !c.safe && chosen?.has(c.id)) || [];
  const needsName = destructive.length > 0;
  const ready = chosen?.size > 0 && (!needsName || typed === name);
  const refresh = () => { state.reload(); onChanged?.(); };
  const apply = async () => {
    const result = await run(
      () => post(`${path}/apply`, { changes: [...chosen], ...(needsName ? { confirm: typed } : {}) }),
      "Applied to the table",
    );
    if (result) { setTyped(""); refresh(); }
  };
  const purge = async () => {
    const result = await run(() => post(`${path}/purge`, { confirm: typed }), "Removed for good");
    if (result) { setTyped(""); refresh(); }
  };
  return (
    <Loading state={state}>
      {() => report && (
        <div className="stack lg">
          <Card
            title="Table and definition"
            actions={<><SchemaBadge report={report} /><Button size="sm" onClick={state.reload}>Refresh</Button></>}
          >
            {report.changes.length === 0 ? (
              <p className="muted">The table <b className="mono">{report.table}</b> matches the definition. Nothing is waiting to be applied.</p>
            ) : (
              <div className="stack">
                <p className="muted">
                  Saving a resource changes its definition, not the table. These changes are waiting. Nothing runs until you apply them, and each one can be rolled back.
                </p>
                {report.changes.map((change) => (
                  <label key={change.id} className="schema-change">
                    <input type="checkbox" checked={chosen?.has(change.id) || false} onChange={() => toggle(change.id)} />
                    <span className="stack sm">
                      <span className="row wrap">
                        <b>{OPS[change.op] || change.op}</b>
                        {change.column && <code>{change.column}</code>}
                        {!change.safe && <Badge tone="yellow">Rewrites values</Badge>}
                      </span>
                      <span className="hint">{change.detail}</span>
                      <pre className="code-block schema-sql">{change.sql.join(";\n")};</pre>
                    </span>
                  </label>
                ))}
                {needsName && (
                  <Field label={`Type ${name} to confirm`} hint="The old values are kept next to the column until you remove them, so this can be rolled back.">
                    <input value={typed} onChange={(event) => setTyped(event.target.value)} placeholder={name} />
                  </Field>
                )}
                <div className="row">
                  <Button variant="primary" disabled={busy || !ready} onClick={apply}>
                    Apply {chosen?.size || 0} change{chosen?.size === 1 ? "" : "s"}
                  </Button>
                </div>
              </div>
            )}
          </Card>
          {report.retained.length > 0 && (
            <Card title="Kept for rollbacks">
              <div className="stack">
                <p className="muted">These columns and tables are hidden from your API and held so a rollback can restore them. Removing them frees the space and makes those rollbacks impossible.</p>
                {report.retained.map((item) => (
                  <div key={item.name} className="row">
                    <Badge>{item.kind}</Badge><code>{item.original}</code><span className="hint">kept as {item.name}</span>
                  </div>
                ))}
                <Field label={`Type ${name} to remove them for good`}>
                  <input value={typed} onChange={(event) => setTyped(event.target.value)} placeholder={name} />
                </Field>
                <div className="row"><Button disabled={busy || typed !== name} onClick={purge}>Remove for good</Button></div>
              </div>
            </Card>
          )}
          {report.exists && (
            <Card title="Columns in the table">
              <div className="row wrap">
                {report.columns.map((column) => <Badge key={column.name}>{column.name} · {String(column.type).toLowerCase()}</Badge>)}
              </div>
            </Card>
          )}
        </div>
      )}
    </Loading>
  );
}

/** Every change made to the resource's table, newest first, with a rollback for the latest. */
export function HistoryPanel({ env, name, onChanged }) {
  const path = envPath(env, `/resources/${name}/migrations`);
  const state = useApi(path);
  const [run, busy] = useAction();
  const [confirming, setConfirming] = useState(null);
  const [open, setOpen] = useState(null);
  const rollBack = async () => {
    const id = confirming.id;
    const result = await run(() => post(`${path}/${id}/rollback`), "Rolled back");
    if (result) { setConfirming(null); state.reload(); onChanged?.(); }
  };
  const label = (row) => (row.kind === "rollback" ? "Rollback" : row.status === "rolled_back" ? "Rolled back" : row.status === "failed" ? "Failed" : "Applied");
  const tone = (row) => (row.status === "failed" ? "red" : row.kind === "rollback" || row.status === "rolled_back" ? "yellow" : "green");
  return (
    <Loading state={state} empty="No changes yet. Applying the definition to the table starts the history.">
      {(data) => (
        <div className="stack">
          {data.data.map((row) => (
            <div key={row.id} className="card schema-entry">
              <div className="spread">
                <div className="row wrap">
                  <b>#{row.number}</b>
                  <Badge tone={tone(row)}>{label(row)}</Badge>
                  <span>{row.summary}</span>
                </div>
                <div className="row">
                  {row.can_roll_back
                    ? <Button size="sm" disabled={busy} onClick={() => setConfirming(row)}>Roll back</Button>
                    : <span className="hint">{row.status === "applied" && row.kind === "apply" ? row.cannot_roll_back : ""}</span>}
                  <Button size="sm" onClick={() => setOpen(open === row.id ? null : row.id)}>{open === row.id ? "Hide SQL" : "SQL"}</Button>
                </div>
              </div>
              <div className="hint">{when(row.created_at)}{row.actor ? ` · ${row.actor}` : ""} · from {row.source}</div>
              {row.error && <div className="alert error">{row.error}</div>}
              {open === row.id && (
                <div className="stack sm">
                  <b>Ran</b><pre className="code-block schema-sql">{row.up.join(";\n")}{row.up.length ? ";" : ""}</pre>
                  {row.down.length > 0 && <><b>To undo</b><pre className="code-block schema-sql">{row.down.join(";\n")};</pre></>}
                </div>
              )}
            </div>
          ))}
          {confirming && (
            <Modal
              title={`Roll back #${confirming.number}?`}
              onClose={() => setConfirming(null)}
              footer={<><Button onClick={() => setConfirming(null)}>Cancel</Button><Button variant="primary" disabled={busy} onClick={rollBack}>Roll back</Button></>}
            >
              <div className="stack">
                <p>{confirming.summary}</p>
                <p className="muted">The table goes back to how it was before this change. Values the change retired are restored; anything written to what it added is kept aside, not deleted. The definition is not changed, so the table will show as not applied again.</p>
                <pre className="code-block schema-sql">{confirming.down.join(";\n")};</pre>
              </div>
            </Modal>
          )}
        </div>
      )}
    </Loading>
  );
}
