import { useRef, useState } from "react";
import Layout from "../../components/Layout";
import { Icon } from "../../components/icons";
import { Badge, Button, Card, Field, Json, PageHead, Segmented, useAction } from "../../components/ui";
import { envPath, get, post, useApi } from "../../lib/api";
import Snapshots from "./backups/Snapshots";

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
export default function Backups({ env }) {
  const base = envPath(env);
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
  const snapshots = useApi(`${base}/snapshots`);
  const [run, busy] = useAction();

  const toggle = (list, setList, key) => setList(list.includes(key) ? list.filter((k) => k !== key) : [...list, key]);

  const download = async () => {
    await run(async () => {
      const backup = await get(`${base}/backup`, { params: { include: chosen.join(",") } });
      const stamp = (backup.created_at || "").slice(0, 19).replace(/:/g, "-");
      save(`${env}-${stamp}.pawabase-backup.json`, backup);
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
      <PageHead title="Backups" description="Save everything that makes up this environment, on a schedule or on demand, and restore from it later. Or download it as one file to restore into another environment." />
      <div className="stack flex flex-col gap-3 [&.sm]:gap-1.5 [&.lg]:gap-5 lg">
        <Snapshots env={env} base={base} list={snapshots} />
        <Card icon="backups" title="Download a backup">
          <div className="stack flex flex-col gap-3 [&.sm]:gap-1.5 [&.lg]:gap-5">
            {PARTS.map(([key, label, hint]) => (
              <label className="check [label&]:flex [label&]:items-center [label&]:gap-2 [label&]:text-text [label&]:text-ui" key={key} style={{ alignItems: "flex-start" }}>
                <input type="checkbox" checked={chosen.includes(key)} onChange={() => toggle(chosen, setChosen, key)} />
                <span><b>{label}</b><br /><span className="hint text-xs font-book text-muted leading-[1.45]">{hint}</span></span>
              </label>
            ))}
            <p className="hint text-xs font-book text-muted leading-[1.45]">
              Not included: API keys, secret values, stored files, sessions, logs and jobs. A backup holds password hashes, so keep the file private.
            </p>
            <div><Button variant="primary" disabled={busy || !chosen.length} onClick={download}>Download backup</Button></div>
          </div>
        </Card>

        <Card icon="backups" title="Restore from a backup">
          <div className="restore-step grid gap-3.5">
            <label className={`dropzone grid gap-1 justify-items-center text-center p-[26px_18px] [border:2px_dashed_var(--line-strong,_var(--line))] rounded-sm bg-sunken cursor-pointer transition-[border-color_0.15s,_background_0.15s] [&:hover]:[border-color:var(--brand,_#FF7049)] [&:hover]:bg-brand-soft [&.over]:[border-color:var(--brand,_#FF7049)] [&.over]:bg-brand-soft [&_b]:text-base [&_input]:hidden ${over ? "over" : ""}`} onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)} onDrop={(e) => { e.preventDefault(); setOver(false); const dropped = e.dataTransfer.files?.[0]; if (dropped) readFile(dropped); }}>
              <input ref={input} type="file" accept="application/json,.json" onChange={(e) => e.target.files?.[0] && readFile(e.target.files[0])} />
              <Icon name="upload" />
              <b>{file ? file.name : "Choose a backup file, or drop it here"}</b>
              <span className="hint text-xs font-book text-muted leading-[1.45]">{file ? `${(file.size / 1024).toFixed(0)} KB · choose another to replace it` : "A .pawabase-backup.json file downloaded from Studio or the API."}</span>
            </label>

            {checking && <div className="alert rounded-sm p-[11px_14px] text-ui [&.error]:text-danger [&.error]:bg-danger-soft [&.warn]:text-warn [&.warn]:bg-warn-soft [&.ok]:text-ok [&.ok]:bg-ok-soft [&.info]:text-info [&.info]:bg-info-soft [border:1px_solid_color-mix(in_srgb,_currentColor_22%,_transparent)] info">Checking the file…</div>}
            {problem && <div className="alert rounded-sm p-[11px_14px] text-ui [&.error]:text-danger [&.error]:bg-danger-soft [&.warn]:text-warn [&.warn]:bg-warn-soft [&.ok]:text-ok [&.ok]:bg-ok-soft [&.info]:text-info [&.info]:bg-info-soft [border:1px_solid_color-mix(in_srgb,_currentColor_22%,_transparent)] error">{problem}</div>}

            {preview && (
              <>
                <div className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5" style={{ gap: 8, flexWrap: "wrap" }}>
                  <Badge tone="lavender">from {preview.source?.env}</Badge>
                  <span className="muted text-muted">Nothing has changed yet: this is what the file holds.</span>
                </div>
                <div className="restore-summary grid grid-cols-[repeat(auto-fill,_minmax(150px,_1fr))] gap-2 [&_div]:bg-sunken [&_div]:rounded-sm [&_div]:p-[10px_12px] [&_b]:block [&_b]:text-[18px] [&_b]:tabular-nums [&_span]:text-xs [&_span]:text-muted">
                  {Object.entries(preview.contents || {}).flatMap(([part, counts]) =>
                    typeof counts === "object" && counts ? Object.entries(counts).filter(([, n]) => typeof n === "number" && n > 0).map(([name, n]) => ({ key: `${part}.${name}`, name: name.replace(/_/g, " "), n })) : [{ key: part, name: part, n: counts }],
                  ).slice(0, 24).map((item) => <div key={item.key}><b>{typeof item.n === "number" ? item.n.toLocaleString() : String(item.n)}</b><span>{item.name}</span></div>)}
                </div>
                <Field label="What to restore">
                  <div className="stack flex flex-col gap-3 [&.sm]:gap-1.5 [&.lg]:gap-5" style={{ gap: 6 }}>
                    {PARTS.filter(([key]) => available.includes(key)).map(([key, label]) => (
                      <label className="check [label&]:flex [label&]:items-center [label&]:gap-2 [label&]:text-text [label&]:text-ui" key={key}>
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

            <div className="restore-bar sticky bottom-0 flex items-center justify-between gap-3 flex-wrap p-[14px_16px] m-[4px_-4px_-4px] rounded-sm bg-surface [border:1px_solid_var(--line)] shadow-none [&_.what]:text-ui [&_.what]:text-muted [&_.what_b]:text-text">
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
                  <div className="stack flex flex-col gap-3 [&.sm]:gap-1.5 [&.lg]:gap-5" style={{ gap: 4 }}>
                    <b>Warnings</b>
                    {report.warnings.map((w, i) => <span className="muted text-muted" key={i}>{w}</span>)}
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
