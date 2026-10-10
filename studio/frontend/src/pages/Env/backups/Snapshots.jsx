import { useState } from "react";
import { Badge, Button, Card, Field, Loading, Modal, Segmented, Switch, Table, when, useAction } from "../../../components/ui";
import { del, get, patch, post, put } from "../../../lib/api";

const PARTS = [["definitions", "Definitions"], ["settings", "Settings"], ["users", "Users & access"], ["data", "Data"]];
const DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
const TRIGGERS = { manual: ["Manual", "lavender"], scheduled: ["Scheduled", "sky"], "before-restore": ["Before restore", "butter"] };

const size = (bytes) => (bytes > 1048576 ? `${(bytes / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`);

function save(name, content) {
  const blob = new Blob([JSON.stringify(content, null, 2)], { type: "application/json" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = name;
  link.click();
  URL.revokeObjectURL(link.href);
}

export default function Snapshots({ env, base, list }) {
  const [run, busy] = useAction();
  const [taking, setTaking] = useState(false);
  const [scheduling, setScheduling] = useState(null);
  const [restoring, setRestoring] = useState(null);
  const [verdicts, setVerdicts] = useState({});

  const verify = async (row) => {
    const result = await run(() => post(`${base}/snapshots/${row.id}/verify`));
    if (result && result !== true) setVerdicts((v) => ({ ...v, [row.id]: result }));
  };

  return (
    <Card
      title="Saved backups"
      actions={<div className="row">
        <Button onClick={() => list.data && setScheduling(list.data.schedule)}>Schedule</Button>
        <Button variant="primary" onClick={() => setTaking(true)}>Back up now</Button>
      </div>}
      flush
    >
      <Loading state={list} empty="No saved backups yet. Take one now, or turn on a schedule.">
        {(data) => (
          <>
            <div className="muted small" style={{ padding: "10px 16px" }}>
              {data.schedule.enabled
                ? <>Automatic: {data.schedule.frequency}{data.schedule.frequency !== "hourly" ? ` at ${String(data.schedule.hour).padStart(2, "0")}:00 UTC` : ""}{data.schedule.frequency === "weekly" ? ` on ${DAYS[data.schedule.weekday]}s` : ""}, keeping the last {data.schedule.keep_last || "any number"} for up to {data.schedule.keep_days || "any number of"} days.{data.schedule.last_run_at ? ` Last ran ${when(data.schedule.last_run_at)} (${data.schedule.last_status}).` : ""}</>
                : "Automatic backups are off."}
              {" "}{data.data.length > 0 && <>Stored: {size(data.stored_bytes)}.</>}
            </div>
            {data.data.length > 0 && (
              <Table
                rows={data.data}
                columns={[
                  { label: "Backup", render: (s) => <div><strong>{s.name}</strong>{s.note && <div className="faint small">{s.note}</div>}</div> },
                  { label: "Kind", render: (s) => <Badge tone={TRIGGERS[s.trigger]?.[1]}>{TRIGGERS[s.trigger]?.[0] || s.trigger}</Badge> },
                  { label: "Holds", render: (s) => <span className="muted small">{(s.include || []).join(", ")}</span> },
                  { label: "Size", render: (s) => (s.status === "complete" ? size(s.size_bytes) : <Badge tone="red">{s.status}</Badge>) },
                  { label: "Taken", render: (s) => when(s.created_at) },
                  { label: "Check", render: (s) => verdicts[s.id] ? <Badge tone={verdicts[s.id].ok ? "green" : "red"}>{verdicts[s.id].ok ? "intact" : "damaged"}</Badge> : <Button size="sm" disabled={s.status !== "complete"} onClick={() => verify(s)}>Verify</Button> },
                  { label: "", render: (s) => <div className="row">
                    <Button size="sm" disabled={s.status !== "complete"} onClick={() => setRestoring(s)}>Restore</Button>
                    <Button size="sm" disabled={s.status !== "complete"} onClick={() => run(async () => save(`${env}-${s.created_at.slice(0, 19).replace(/:/g, "-")}.pawabase-backup.json`, await get(`${base}/snapshots/${s.id}/download`)))}>Download</Button>
                    <Button size="sm" onClick={async () => { if (await run(() => patch(`${base}/snapshots/${s.id}`, { pinned: !s.pinned }), s.pinned ? "Unpinned" : "Pinned: retention will keep it")) list.reload(); }}>{s.pinned ? "Unpin" : "Pin"}</Button>
                    <Button size="sm" variant="danger" onClick={async () => { if (confirm(`Delete "${s.name}"?`) && await run(() => del(`${base}/snapshots/${s.id}`), "Deleted")) list.reload(); }}>Delete</Button>
                  </div> },
                ]}
              />
            )}
          </>
        )}
      </Loading>
      {taking && <TakeSheet base={base} onClose={() => setTaking(false)} onDone={() => { setTaking(false); list.reload(); }} />}
      {scheduling && <ScheduleSheet base={base} schedule={scheduling} onClose={() => setScheduling(null)} onDone={() => { setScheduling(null); list.reload(); }} />}
      {restoring && <RestoreSheet env={env} base={base} snapshot={restoring} onClose={() => setRestoring(null)} onDone={() => { setRestoring(null); list.reload(); }} />}
    </Card>
  );
}

function PartPicker({ value, onChange }) {
  return (
    <div className="stack" style={{ gap: 6 }}>
      {PARTS.map(([key, label]) => (
        <label className="check" key={key}>
          <input type="checkbox" checked={value.includes(key)} onChange={() => onChange(value.includes(key) ? value.filter((k) => k !== key) : [...value, key])} /> {label}
        </label>
      ))}
    </div>
  );
}

function TakeSheet({ base, onClose, onDone }) {
  const [data, setData] = useState({ name: "", note: "", include: PARTS.map(([k]) => k) });
  const [run, busy] = useAction();
  return (
    <Modal title="Back up now" onClose={onClose} footer={<Button variant="primary" disabled={busy || !data.include.length} onClick={async () => { if (await run(() => post(`${base}/snapshots`, data), "Backup saved")) onDone(); }}>{busy ? "Backing up…" : "Back up"}</Button>}>
      <Field label="Name" optional><input value={data.name} onChange={(e) => setData({ ...data, name: e.target.value })} placeholder="Before the pricing change" /></Field>
      <Field label="Note" optional><input value={data.note} onChange={(e) => setData({ ...data, note: e.target.value })} /></Field>
      <Field label="What to include"><PartPicker value={data.include} onChange={(include) => setData({ ...data, include })} /></Field>
    </Modal>
  );
}

function ScheduleSheet({ base, schedule, onClose, onDone }) {
  const [data, setData] = useState({ ...schedule, include: schedule.include?.length ? schedule.include : PARTS.map(([k]) => k) });
  const [run, busy] = useAction();
  const num = (key) => (e) => setData({ ...data, [key]: Number(e.target.value) });
  return (
    <Modal title="Automatic backups" onClose={onClose} footer={<Button variant="primary" disabled={busy} onClick={async () => { if (await run(() => put(`${base}/backup-schedule`, data), "Schedule saved")) onDone(); }}>Save</Button>}>
      <Switch checked={data.enabled} onChange={(enabled) => setData({ ...data, enabled })} label="Back up automatically" hint="Taken by the platform's scheduler and kept in its object storage." />
      <Field label="How often"><Segmented value={data.frequency} onChange={(frequency) => setData({ ...data, frequency })} options={[["hourly", "Hourly"], ["daily", "Daily"], ["weekly", "Weekly"]]} /></Field>
      {data.frequency !== "hourly" && (
        <div className="grid2">
          <Field label="At (UTC hour)"><input type="number" min="0" max="23" value={data.hour} onChange={num("hour")} /></Field>
          {data.frequency === "weekly" && <Field label="On"><select value={data.weekday} onChange={num("weekday")}>{DAYS.map((d, i) => <option key={d} value={i}>{d}</option>)}</select></Field>}
        </div>
      )}
      <div className="grid2">
        <Field label="Keep the last" hint="Scheduled backups; 0 for no limit."><input type="number" min="0" value={data.keep_last} onChange={num("keep_last")} /></Field>
        <Field label="Keep for (days)" hint="0 for no limit. Pinned backups are never removed."><input type="number" min="0" value={data.keep_days} onChange={num("keep_days")} /></Field>
      </div>
      <Field label="What to include"><PartPicker value={data.include} onChange={(include) => setData({ ...data, include })} /></Field>
    </Modal>
  );
}

function RestoreSheet({ env, base, snapshot, onClose, onDone }) {
  const [parts, setParts] = useState(snapshot.include || []);
  const [strategy, setStrategy] = useState("merge");
  const [confirmed, setConfirmed] = useState("");
  const [safety, setSafety] = useState(true);
  const [report, setReport] = useState(null);
  const [run, busy] = useAction();
  const ready = parts.length > 0 && (strategy === "merge" || confirmed === env);
  const go = async () => {
    const result = await run(() => post(`${base}/snapshots/${snapshot.id}/restore`, { include: parts, strategy, confirm: strategy === "replace" ? confirmed : undefined, safety_snapshot: safety }), "Restored");
    if (result && result !== true) setReport(result);
  };
  return (
    <Modal title={`Restore: ${snapshot.name}`} onClose={report ? onDone : onClose} footer={report ? <Button variant="primary" onClick={onDone}>Done</Button> : <Button variant={strategy === "replace" ? "danger" : "primary"} disabled={busy || !ready} onClick={go}>{busy ? "Restoring…" : strategy === "replace" ? "Replace from backup" : "Restore"}</Button>}>
      {report ? (
        <div className="stack">
          {report.safety_snapshot && <div className="alert info">The environment as it was is saved as a "Before restore" backup, so this can be undone.</div>}
          <Field label="Restored"><pre className="json">{JSON.stringify({ ...report, warnings: undefined, safety_snapshot: undefined }, null, 2)}</pre></Field>
          {report.warnings?.map((w, i) => <span className="muted" key={i}>{w}</span>)}
        </div>
      ) : (
        <>
          <Field label="What to restore"><PartPicker value={parts} onChange={setParts} /></Field>
          <Field label="How" hint={strategy === "merge" ? "Adds what is missing and updates what matches. Anything the backup does not mention stays." : "Removes everything the chosen parts hold first, so afterwards the environment holds exactly what the backup held."}>
            <Segmented value={strategy} onChange={setStrategy} options={[["merge", "Merge"], ["replace", "Replace"]]} />
          </Field>
          {strategy === "replace" && <Field label={`Type ${env} to confirm`}><input value={confirmed} onChange={(e) => setConfirmed(e.target.value)} placeholder={env} /></Field>}
          <Switch checked={safety} onChange={setSafety} label="Save the current state first" hint="Takes a backup of what will be overwritten, so a restore can be undone." />
        </>
      )}
    </Modal>
  );
}
