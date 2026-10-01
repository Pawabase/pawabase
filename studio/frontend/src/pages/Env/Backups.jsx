import { useRef, useState } from "react";
import Layout from "../../components/Layout";
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

  const choose = async (event) => {
    const picked = event.target.files?.[0];
    setReport(null);
    setPreview(null);
    if (!picked) return;
    let backup;
    try {
      backup = JSON.parse(await picked.text());
    } catch {
      setFile(null);
      return run(async () => { throw new Error("That file is not JSON."); });
    }
    setFile({ name: picked.name, backup });
    const result = await run(() => post(`${base}/restore`, { backup, dry_run: true }));
    if (result && result !== true) {
      setPreview(result);
      setRestoreParts(result.would_restore);
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
  const ready = file && preview && restoreParts.length && (strategy === "merge" || confirm === env);

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
          <div className="stack">
            <Field label="Backup file" hint="A .pawabase-backup.json file downloaded from Studio or the API.">
              <input ref={input} type="file" accept="application/json,.json" onChange={choose} />
            </Field>

            {preview && (
              <>
                <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
                  <Badge tone="lavender">{preview.source?.project}/{preview.source?.env}</Badge>
                  <span className="muted">{file.name}</span>
                </div>
                <Json value={preview.contents} />
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
                <div><Button variant={strategy === "replace" ? "danger" : "primary"} disabled={busy || !ready} onClick={restore}>{strategy === "replace" ? "Replace from backup" : "Restore backup"}</Button></div>
              </>
            )}

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
