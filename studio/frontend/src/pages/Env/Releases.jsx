import { useEffect, useMemo, useState } from "react";
import Layout from "../../components/Layout";
import { Badge, Button, Card, Field, Loading, PageHead, Segmented, Sheet, Status, Table, useAction, when } from "../../components/ui";
import { envPath, post, useApi } from "../../lib/api";

export default function Releases({ project, env }) {
  const base = envPath(project.ref, env);
  const revisions = useApi(`${base}/revisions`), versions = useApi(`${base}/api-versions`), releases = useApi(`${base}/releases`), deployments = useApi(`${base}/deployments`);
  const [branch, setBranch] = useState(() => {
    try { return localStorage.getItem(`pawabase.branch.${project.ref}.${env}`) || "main"; } catch { return "main"; }
  }), [newVersion, setNewVersion] = useState(""), [revisionMessage, setRevisionMessage] = useState(""), [view, setView] = useState("releases"), [selected, setSelected] = useState(null), [prepareOpen, setPrepareOpen] = useState(false), [revisionOpen, setRevisionOpen] = useState(false), [versionOpen, setVersionOpen] = useState(false);
  const [release, setRelease] = useState({ revision_id: "", api_version: "", name: "", allow_breaking: false });
  const [run, busy] = useAction();
  const reload = () => [revisions, versions, releases, deployments].forEach((state) => state.reload());
  const revisionRows = revisions.data?.data || [], versionRows = versions.data?.data || [];
  const validRevisions = useMemo(() => revisionRows.filter((item) => item.status === "valid"), [revisionRows]);
  const draft = { ...release, revision_id: release.revision_id || validRevisions[0]?.id || "", api_version: release.api_version || versionRows[0]?.name || "" };
  const act = async (fn, message) => { if (await run(fn, message)) reload(); };
  const chooseRevision = (item) => { setRelease({ ...release, revision_id: item.id }); setView("releases"); setPrepareOpen(true); };
  const chooseVersion = (item) => { setRelease({ ...release, api_version: item.name }); setView("releases"); setPrepareOpen(true); };
  const prepare = async () => {
    if (await run(() => post(`${base}/releases`, draft), "Release prepared")) {
      setRelease({ revision_id: "", api_version: "", name: "", allow_breaking: false });
      setPrepareOpen(false);
      reload();
    }
  };
  const createRevision = async () => {
    if (await run(() => post(`${base}/branches/${branch}/revisions`, { message: revisionMessage }), "Revision created")) {
      setRevisionMessage("");
      setRevisionOpen(false);
      reload();
    }
  };
  const createVersion = async () => {
    if (await run(() => post(`${base}/api-versions`, { name: newVersion }), "API version created")) {
      setNewVersion("");
      setVersionOpen(false);
      reload();
    }
  };
  useEffect(() => {
    const sync = (event) => { if (event.detail?.project === project.ref && event.detail?.env === env) setBranch(event.detail.branch); };
    window.addEventListener("pawabase:branch", sync);
    return () => window.removeEventListener("pawabase:branch", sync);
  }, [project.ref, env]);

  return <Layout title="Releases & versions">
    <PageHead title="Releases & versions" description="Freeze definitions into a revision, release them behind a stable API path, and roll traffic back atomically." actions={<Button variant="primary" disabled={busy} onClick={() => setRevisionOpen(true)}>Create revision</Button>} />
    <div className="row" style={{ justifyContent: "space-between", marginBottom: 18 }}><Segmented value={view} onChange={setView} options={[["releases", "Releases"], ["revisions", "Revisions"], ["versions", "API versions"], ["history", "Deployments"]]} /><span className="hint">Select an item to inspect or use it in a release.</span></div>
    {view === "releases" && <div className="stack lg">
      <Card flush title="Prepared releases" actions={<Button size="sm" variant="primary" disabled={busy || !validRevisions.length || !versionRows.length} onClick={() => setPrepareOpen(true)}>Prepare release</Button>}><Loading state={releases} empty="No releases prepared.">{(data) => <Table rows={data.data} onRowClick={setSelected} columns={[{ label: "Release", render: (item) => <b>{item.name}</b> }, { label: "Path", render: (item) => <code>/rest/{item.api_version}</code> }, { label: "Status", render: (item) => <Status value={item.status} /> }, { label: "Compatibility", render: (item) => item.compatibility?.compatible ? <Badge tone="green">compatible</Badge> : <Badge tone="red">breaking</Badge> }, { label: "Created", render: (item) => when(item.created_at) }]} />}</Loading></Card>
      {selected && <Card title={selected.name} actions={<Button size="sm" disabled={busy || selected.status === "active"} onClick={() => act(() => post(`${base}/releases/${selected.id}/activate`), `${selected.name} activated`)}>Activate release</Button>}><div className="stack" style={{ gap: 8 }}><div className="row"><Status value={selected.status} /><code>/rest/{selected.api_version}</code></div><span className="muted">Revision {selected.revision_id?.slice(0, 8) || "—"} · created {when(selected.created_at)}</span><pre className="code-block">{JSON.stringify(selected.compatibility || {}, null, 2)}</pre></div></Card>}
    </div>}
    {view === "revisions" && <Card flush title="Revisions"><Loading state={revisions} empty="No revisions yet.">{(data) => <Table rows={data.data} onRowClick={chooseRevision} columns={[{ label: "#", key: "number" }, { label: "Branch", key: "branch" }, { label: "Status", render: (item) => <Status value={item.status} /> }, { label: "Message", key: "message" }, { label: "Created", render: (item) => when(item.created_at) }, { label: "", render: (item) => <Button size="sm" onClick={() => chooseRevision(item)}>Use in release</Button> }]} />}</Loading></Card>}
    {view === "versions" && <Card flush title="Public API versions" actions={<Button size="sm" variant="primary" disabled={busy} onClick={() => setVersionOpen(true)}>Add version</Button>}><Loading state={versions} empty="No API versions yet.">{(data) => <Table rows={data.data} onRowClick={chooseVersion} columns={[{ label: "Version", key: "name" }, { label: "Status", render: (item) => <Status value={item.status} /> }, { label: "", render: (item) => <Button size="sm" onClick={() => chooseVersion(item)}>Use in release</Button> }]} />}</Loading></Card>}
    {view === "history" && <Card flush title="Deployment history"><Loading state={deployments} empty="No deployments yet.">{(data) => <Table rows={data.data} columns={[{ label: "Version", key: "api_version" }, { label: "Action", key: "action" }, { label: "Release", render: (item) => <code>{item.release_id.slice(0, 8)}</code> }, { label: "Status", render: (item) => <Status value={item.status} /> }, { label: "When", render: (item) => when(item.created_at) }, { label: "", render: (item) => item.id === data.data.find((row) => row.api_version === item.api_version)?.id && item.previous_release_id ? <Button size="sm" disabled={busy} onClick={() => act(() => post(`${base}/api-versions/${item.api_version}/rollback`), `${item.api_version} rolled back`)}>Rollback</Button> : null }]} />}</Loading></Card>}
    {prepareOpen && <Sheet title="Prepare release" subtitle="Select the snapshot and public API path this release will use." icon="releases" onClose={() => setPrepareOpen(false)} footer={<><Button onClick={() => setPrepareOpen(false)} disabled={busy}>Cancel</Button><Button variant="primary" disabled={busy || !draft.revision_id || !draft.api_version || !draft.name} onClick={prepare}>Prepare release</Button></>}>
      <Field label="Revision" hint="Only valid immutable snapshots can be released."><select value={draft.revision_id} onChange={(e) => setRelease({ ...release, revision_id: e.target.value })}>{validRevisions.map((item) => <option key={item.id} value={item.id}>#{item.number} · {item.branch} · {item.message || item.checksum.slice(0, 8)}</option>)}</select></Field>
      <Field label="API version"><select value={draft.api_version} onChange={(e) => setRelease({ ...release, api_version: e.target.value })}>{versionRows.map((item) => <option key={item.id}>{item.name}</option>)}</select></Field>
      <Field label="Release name"><input value={release.name} onChange={(e) => setRelease({ ...release, name: e.target.value })} placeholder="v2.0.0" autoFocus /></Field>
      <label className="check"><input type="checkbox" checked={release.allow_breaking} onChange={(e) => setRelease({ ...release, allow_breaking: e.target.checked })} /> I acknowledge any breaking compatibility findings.</label>
    </Sheet>}
    {revisionOpen && <Sheet title="Create revision" subtitle={`Capture the current ${branch} definition tree as an immutable snapshot.`} icon="releases" onClose={() => setRevisionOpen(false)} footer={<><Button onClick={() => setRevisionOpen(false)} disabled={busy}>Cancel</Button><Button variant="primary" disabled={busy} onClick={createRevision}>Create revision</Button></>}><Field label="Revision message" hint="Optional, but useful for future release history."><input value={revisionMessage} onChange={(e) => setRevisionMessage(e.target.value)} placeholder={`Snapshot ${branch}`} autoFocus /></Field></Sheet>}
    {versionOpen && <Sheet title="Add API version" subtitle="Create a stable public API path for releases." icon="releases" onClose={() => setVersionOpen(false)} footer={<><Button onClick={() => setVersionOpen(false)} disabled={busy}>Cancel</Button><Button variant="primary" disabled={busy || !/^v[1-9][0-9]*$/.test(newVersion)} onClick={createVersion}>Add version</Button></>}><Field label="Version" hint="Use v1, v2, and so on."><input value={newVersion} onChange={(e) => setNewVersion(e.target.value)} placeholder="v2" autoFocus /></Field></Sheet>}
  </Layout>;
}
