import { useState } from "react";
import { Button, Card, Field, Switch, useAction } from "../../../components/ui";
import { get, post } from "../../../lib/api";
import { AUTH } from "./shared";

/** Take an environment's people and roles out as one file, or bring a file in. Passwords stay hashed. */
export default function Transfer({ env, base }) {
  const [run, busy] = useAction();
  const [file, setFile] = useState(null);
  const [replace, setReplace] = useState(false);
  const [preview, setPreview] = useState(null);
  const [done, setDone] = useState(null);
  const download = async () => {
    const data = await run(() => get(`${base}/backup`, AUTH));
    if (!data || data === true) return;
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = `${env}-identities.json`;
    link.click();
    URL.revokeObjectURL(link.href);
  };
  const read = async (chosen) => {
    setFile(null); setPreview(null); setDone(null);
    if (!chosen) return;
    try { setFile({ name: chosen.name, document: JSON.parse(await chosen.text()) }); } catch { setFile({ name: chosen.name, invalid: true }); }
  };
  const check = async () => { const report = await run(() => post(`${base}/restore`, { identities: file.document, replace, dry_run: true }, AUTH)); if (report && report !== true) setPreview(report); };
  const apply = async () => {
    if (replace && !confirm(`Replace every user, role and organization in ${env} with this file? This cannot be undone.`)) return;
    const report = await run(() => post(`${base}/restore`, { identities: file.document, replace }, AUTH), "Imported");
    if (report && report !== true) { setDone(report); setPreview(null); }
  };
  return (
    <div className="stack flex flex-col gap-3 [&.sm]:gap-1.5 [&.lg]:gap-5 lg">
      <Card icon="upload" title="Export" description={<>Roles with their permissions, users with their password hashes, linked social sign-ins, authenticator apps, and organizations with members and teams. User ids are kept, so records that point at a user still do.</>} actions={<Button variant="primary" size="sm" disabled={busy} onClick={download}>Download file</Button>}>
        <p className="hint text-xs font-book text-muted leading-[1.45]" style={{ margin: "10px 0 0" }}>Left out: sessions, tokens, sign-in history and pending invitations. Everyone signs in again after an import. The file holds password hashes, so keep it private.</p>
      </Card>
      <Card icon="upload" title="Import">
        <div className="stack flex flex-col gap-3 [&.sm]:gap-1.5 [&.lg]:gap-5">
          <Field label="Identities file"><input type="file" accept="application/json,.json" onChange={(e) => read(e.target.files[0])} /></Field>
          {file?.invalid && <div className="alert rounded-sm p-[11px_14px] text-ui [&.error]:text-danger [&.error]:bg-danger-soft [&.warn]:text-warn [&.warn]:bg-warn-soft [&.ok]:text-ok [&.ok]:bg-ok-soft [&.info]:text-info [&.info]:bg-info-soft [border:1px_solid_color-mix(in_srgb,_currentColor_22%,_transparent)] error">That file is not valid JSON.</div>}
          {file?.document && (
            <>
              <Switch checked={replace} onChange={setReplace} label="Replace what is here" hint="Off adds and updates. On first removes this environment's users, roles and organizations." />
              <div className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5"><Button disabled={busy} onClick={check}>Check the file</Button><Button variant="primary" disabled={busy} onClick={apply}>Import</Button></div>
            </>
          )}
          {preview && <div className="alert rounded-sm p-[11px_14px] text-ui [&.error]:text-danger [&.error]:bg-danger-soft [&.warn]:text-warn [&.warn]:bg-warn-soft [&.ok]:text-ok [&.ok]:bg-ok-soft [&.info]:text-info [&.info]:bg-info-soft [border:1px_solid_color-mix(in_srgb,_currentColor_22%,_transparent)] info"><b>Dry run.</b> This file holds {Object.entries(preview).filter(([k, v]) => typeof v === "number").map(([k, v]) => `${v} ${k.replace(/_/g, " ")}`).join(", ") || "identities"}. Nothing has changed.</div>}
          {done && <div className="alert rounded-sm p-[11px_14px] text-ui [&.error]:text-danger [&.error]:bg-danger-soft [&.warn]:text-warn [&.warn]:bg-warn-soft [&.ok]:text-ok [&.ok]:bg-ok-soft [&.info]:text-info [&.info]:bg-info-soft [border:1px_solid_color-mix(in_srgb,_currentColor_22%,_transparent)] ok"><b>Imported.</b> {Object.entries(done).filter(([, v]) => typeof v === "number").map(([k, v]) => `${v} ${k.replace(/_/g, " ")}`).join(", ")}.</div>}
        </div>
      </Card>
    </div>
  );
}
