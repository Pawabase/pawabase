import { useRef, useState } from "react";
import Layout from "../../components/Layout";
import { Icon } from "../../components/icons";
import { Badge, Button, Card, Field, Json, PageHead, Segmented, useAction } from "../../components/ui";
import { envPath, get, post } from "../../lib/api";

const PARTS = [
  ["definitions", "Definitions", "Resources, policies, schemas, transformers, flows, routes, buckets, mail templates, subscriptions, webhooks, inbound hooks and schedules."],
  ["settings", "Settings", "The environment's auth configuration and settings (CORS origins, rate limits, token lifetimes)."],
  ["users", "Users & access", "Roles and permissions, users with their password hashes, linked sign-ins, MFA, organizations and teams. People keep their passwords."],
  ["data", "Data", "Every row of every resource's table, with ids preserved."],
];

function save(name, content) {
  const blob = new Blob([JSON.stringify(content, null, 2)], { type: "application/json" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = name;
  link.click();
  URL.revokeObjectURL(link.href);
}
export default function Backups({ project, env }) {
  const base = envPath(project.ref, env);
  const [chosen, setChosen] = useState(PARTS.map(([key]) => key));
  const [file, setFile] = useState(null);
  const [preview, setPreview] = useState(null);
  const [restoreParts, setRestoreParts] = useState([]);
  const [strategy, setStrategy] = useState("merge");
  const [confirm, setConfirm] = useState("");
  const [report, setReport] = useState(null);
  const [over, setOver] = useState(false);
  const [checking, setChecking] = useState(false);
  const [problem, setProblem] = useState(null);
  const input = useRef(null);
  const [run, busy] = useAction();

  const toggle = (list, setList, key) => setList(list.includes(key) ? list.filter((k) => k !== key) : [...list, key]);

  const download = async () => {
    await run(async () => {
      const backup = await get(`${base}/backup`, { params: { include: chosen.join(",") } });
      const stamp = (backup.created_at || "").slice(0, 19).replace(/:/g, "-");
      save(`${project.ref}-${env}-${stamp}.pawabase-backup.json`, backup);
    }, "Backup downloaded");
  };

  const readFile = async (picked) => {
    setReport(null);
    setPreview(null);
    setProblem(null);
    setFile({ name: picked.name, size: picked.size });
    let backup;
    try {
      backup = JSON.parse(await picked.text());
    } catch {
      setProblem("That file is not JSON, so it is not a Pawabase backup.");
      return;
    }
    setFile({ name: picked.name, size: picked.size, backup });
    setChecking(true);
    try {
      const result = await post(`${base}/restore`, { backup, dry_run: true });
      setPreview(result);
      setRestoreParts(result.would_restore || []);
    } catch (error) {
      setProblem(`This file was refused: ${error.message || "it does not look like a Pawabase backup."}`);
    } finally {
      setChecking(false);
    }
  };

  const restore = async () => {
    const result = await run(
      () => post(`${base}/restore`, { backup: file.backup, include: restoreParts, strategy, confirm: strategy === "replace" ? confirm : undefined }),
      "Backup restored",
    );
    if (result && result !== true) setReport(result);
  };

  const available = preview?.would_restore || [];
  const ready = Boolean(file?.backup && preview && restoreParts.length && (strategy === "merge" || confirm === env));

  return (
    <Layout title="Backups">
      <PageHead title="Backups" description="Download everything that makes up this environment as one file, and restore from it later: into this environment after a mistake, or into another one." />
      <div className="stack lg">
        <Card title="Download a backup">
          <div className="stack">
            {PARTS.map(([key, label, hint]) => (
              <label className="check" key={key} style={{ alignItems: "flex-start" }}>
                <input type="checkbox" checked={chosen.includes(key)} onChange={() => toggle(chosen, setChosen, key)} />
                <span><b>{label}</b><br /><span className="hint">{hint}</span></span>
              </label>
            ))}
            <p className="hint">
              Not included: API keys, secret values, stored files, sessions, logs and jobs. A backup holds password hashes, so keep the file private.
            </p>
            <div><Button variant="primary" disabled={busy || !chosen.length} onClick={download}>Download backup</Button></div>
          </div>
        </Card>

        <Card title="Restore from a backup">
          <div className="restore-step">
            <label className={`dropzone ${over ? "over" : ""}`} onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)} onDrop={(e) => { e.preventDefault(); setOver(false); const dropped = e.dataTransfer.files?.[0]; if (dropped) readFile(dropped); }}>
              <input ref={input} type="file" accept="application/json,.json" onChange={(e) => e.target.files?.[0] && readFile(e.target.files[0])} />
              <Icon name="upload" />
              <b>{file ? file.name : "Choose a backup file, or drop it here"}</b>
              <span className="hint">{file ? `${(file.size / 1024).toFixed(0)} KB · choose another to replace it` : "A .pawabase-backup.json file downloaded from Studio or the API."}</span>
            </label>

            {checking && <div className="alert info">Checking the file…</div>}
            {problem && <div className="alert error">{problem}</div>}

            {preview && (
              <>
                <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
                  <Badge tone="lavender">from {preview.source?.project}/{preview.source?.env}</Badge>
                  <span className="muted">Nothing has changed yet: this is what the file holds.</span>
                </div>
                <div className="restore-summary">
                  {Object.entries(preview.contents || {}).flatMap(([part, counts]) =>
                    typeof counts === "object" && counts ? Object.entries(counts).filter(([, n]) => typeof n === "number" && n > 0).map(([name, n]) => ({ key: `${part}.${name}`, name: name.replace(/_/g, " "), n })) : [{ key: part, name: part, n: counts }],
                  ).slice(0, 24).map((item) => <div key={item.key}><b>{typeof item.n === "number" ? item.n.toLocaleString() : String(item.n)}</b><span>{item.name}</span></div>)}
                </div>
                <Field label="What to restore">
                  <div className="stack" style={{ gap: 6 }}>
                    {PARTS.filter(([key]) => available.includes(key)).map(([key, label]) => (
                      <label className="check" key={key}>
                        <input type="checkbox" checked={restoreParts.includes(key)} onChange={() => toggle(restoreParts, setRestoreParts, key)} /> {label}
                      </label>
                    ))}
                  </div>
                </Field>
                <Field label="How" hint={strategy === "merge" ? "Adds what is missing and updates what matches. Anything the backup does not mention stays." : "Removes everything the chosen parts hold in this environment first, so afterwards it holds exactly what the backup held."}>
                  <Segmented value={strategy} onChange={setStrategy} options={[["merge", "Merge"], ["replace", "Replace"]]} />
                </Field>
                {strategy === "replace" && (
                  <Field label={`Type ${env} to confirm`} hint="Replace deletes current rows, users and definitions in the chosen parts. Download a backup first if you may need them.">
                    <input value={confirm} onChange={(e) => setConfirm(e.target.value)} placeholder={env} />
                  </Field>
                )}
              </>
            )}

            <div className="restore-bar">
              <span className="what">
                {!file ? "Choose a file to begin." : checking ? "Checking…" : !preview ? "This file cannot be restored." : !restoreParts.length ? "Pick at least one part to restore." : strategy === "replace" && confirm !== env ? `Type ${env} above to enable Replace.` : <><b>{restoreParts.length}</b> part{restoreParts.length === 1 ? "" : "s"} · {strategy === "replace" ? "replacing" : "merging into"} <b>{env}</b></>}
              </span>
              <Button variant={strategy === "replace" ? "danger" : "primary"} disabled={busy || !ready} onClick={restore}>{busy && file ? "Working…" : strategy === "replace" ? "Replace from backup" : "Restore backup"}</Button>
            </div>

            {report && (
              <>
                <h4>Restored</h4>
                <Json value={{ ...report, warnings: undefined }} />
                {report.warnings?.length > 0 && (
                  <div className="stack" style={{ gap: 4 }}>
                    <b>Warnings</b>
                    {report.warnings.map((w, i) => <span className="muted" key={i}>{w}</span>)}
                  </div>
                )}
              </>
            )}
          </div>
        </Card>
      </div>
    </Layout>
  );
}
