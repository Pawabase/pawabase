import { useMemo, useState } from "react";
import Layout from "../../components/Layout";
import { Badge, Button, Card, Field, Loading, PageHead, Segmented, Status, Table, useAction, when } from "../../components/ui";
import { envPath, post, useApi } from "../../lib/api";

export default function Releases({ project, env }) {
  const base = envPath(project.ref, env);
  const branches = useApi(`${base}/branches`), revisions = useApi(`${base}/revisions`), versions = useApi(`${base}/api-versions`), releases = useApi(`${base}/releases`), deployments = useApi(`${base}/deployments`);
  const [branch, setBranch] = useState("main"), [newBranch, setNewBranch] = useState(""), [newVersion, setNewVersion] = useState(""), [view, setView] = useState("releases"), [selected, setSelected] = useState(null);
  const [release, setRelease] = useState({ revision_id: "", api_version: "", name: "", allow_breaking: false });
  const [run, busy] = useAction();
  const reload = () => [branches, revisions, versions, releases, deployments].forEach((state) => state.reload());
  const revisionRows = revisions.data?.data || [], versionRows = versions.data?.data || [];
  const validRevisions = useMemo(() => revisionRows.filter((item) => item.status === "valid"), [revisionRows]);
  const draft = { ...release, revision_id: release.revision_id || validRevisions[0]?.id || "", api_version: release.api_version || versionRows[0]?.name || "" };
  const act = async (fn, message) => { if (await run(fn, message)) reload(); };
  const chooseRevision = (item) => { setRelease({ ...release, revision_id: item.id }); setView("releases"); };
  const chooseVersion = (item) => { setRelease({ ...release, api_version: item.name }); setView("releases"); };

  return <Layout title="Releases & versions">
    <PageHead title="Releases & versions" description="Freeze definitions into a revision, release them behind a stable API path, and roll traffic back atomically." actions={<Button variant="primary" disabled={busy} onClick={() => act(() => post(`${base}/branches/${branch}/revisions`, { message: `Snapshot ${branch}` }), "Revision created")}>Create revision</Button>} />
    <div className="row" style={{ justifyContent: "space-between", marginBottom: 18 }}><Segmented value={view} onChange={setView} options={[["releases", "Releases"], ["revisions", "Revisions"], ["versions", "API versions"], ["history", "Deployments"]]} /><span className="hint">Select an item to inspect or use it in a release.</span></div>
    {view === "releases" && <div className="stack lg">
      <Card title="Release workspace" actions={<Button variant="primary" disabled={busy || !draft.revision_id || !draft.api_version || !draft.name} onClick={() => act(async () => { await post(`${base}/releases`, draft); setRelease({ revision_id: "", api_version: "", name: "", allow_breaking: false }); }, "Release prepared")}>Prepare release</Button>}>
        <div className="stack" style={{ gap: 14 }}>
          <Field label="Working branch"><select value={branch} onChange={(e) => setBranch(e.target.value)}><option value="main">main</option>{(branches.data?.data || []).filter((item) => item.name !== "main").map((item) => <option key={item.id}>{item.name}</option>)}</select></Field>
          <div className="row"><input value={newBranch} onChange={(e) => setNewBranch(e.target.value)} placeholder="feature-checkout" /><Button disabled={busy || !newBranch} onClick={() => act(async () => { await post(`${base}/branches`, { name: newBranch, from_revision: validRevisions[0]?.id || null }); setBranch(newBranch); setNewBranch(""); }, "Branch created")}>Add branch</Button></div>
          <Field label="Revision"><select value={draft.revision_id} onChange={(e) => setRelease({ ...release, revision_id: e.target.value })}>{validRevisions.map((item) => <option key={item.id} value={item.id}>#{item.number} · {item.branch} · {item.message || item.checksum.slice(0, 8)}</option>)}</select></Field>
          <Field label="API version"><select value={draft.api_version} onChange={(e) => setRelease({ ...release, api_version: e.target.value })}>{versionRows.map((item) => <option key={item.id}>{item.name}</option>)}</select></Field>
          <Field label="Release name"><input value={release.name} onChange={(e) => setRelease({ ...release, name: e.target.value })} placeholder="v2.0.0" /></Field>
          <label className="row"><input type="checkbox" checked={release.allow_breaking} onChange={(e) => setRelease({ ...release, allow_breaking: e.target.checked })} style={{ width: "auto" }} /> Acknowledge breaking compatibility findings</label>
        </div>
      </Card>
      <Card flush title="Prepared releases"><Loading state={releases} empty="No releases prepared.">{(data) => <Table rows={data.data} onRowClick={setSelected} columns={[{ label: "Release", render: (item) => <b>{item.name}</b> }, { label: "Path", render: (item) => <code>/rest/{item.api_version}</code> }, { label: "Status", render: (item) => <Status value={item.status} /> }, { label: "Compatibility", render: (item) => item.compatibility?.compatible ? <Badge tone="green">compatible</Badge> : <Badge tone="red">breaking</Badge> }, { label: "Created", render: (item) => when(item.created_at) }]} />}</Loading></Card>
      {selected && <Card title={selected.name} actions={<Button size="sm" disabled={busy || selected.status === "active"} onClick={() => act(() => post(`${base}/releases/${selected.id}/activate`), `${selected.name} activated`)}>Activate release</Button>}><div className="stack" style={{ gap: 8 }}><div className="row"><Status value={selected.status} /><code>/rest/{selected.api_version}</code></div><span className="muted">Revision {selected.revision_id?.slice(0, 8) || "—"} · created {when(selected.created_at)}</span><pre className="code-block">{JSON.stringify(selected.compatibility || {}, null, 2)}</pre></div></Card>}
    </div>}
    {view === "revisions" && <Card flush title="Revisions"><Loading state={revisions} empty="No revisions yet.">{(data) => <Table rows={data.data} onRowClick={chooseRevision} columns={[{ label: "#", key: "number" }, { label: "Branch", key: "branch" }, { label: "Status", render: (item) => <Status value={item.status} /> }, { label: "Message", key: "message" }, { label: "Created", render: (item) => when(item.created_at) }, { label: "", render: (item) => <Button size="sm" onClick={() => chooseRevision(item)}>Use in release</Button> }]} />}</Loading></Card>}
    {view === "versions" && <Card title="Public API versions" actions={<Button disabled={busy || !/^v[1-9][0-9]*$/.test(newVersion)} onClick={() => act(async () => { await post(`${base}/api-versions`, { name: newVersion }); setNewVersion(""); }, "API version created")}>Add version</Button>}><div className="row" style={{ marginBottom: 16 }}><input value={newVersion} onChange={(e) => setNewVersion(e.target.value)} placeholder="v2" /><span className="hint">Select a version to use it in a release.</span></div><Loading state={versions} empty="No API versions yet.">{(data) => <Table rows={data.data} onRowClick={chooseVersion} columns={[{ label: "Version", key: "name" }, { label: "Status", render: (item) => <Status value={item.status} /> }, { label: "", render: (item) => <Button size="sm" onClick={() => chooseVersion(item)}>Use in release</Button> }]} />}</Loading></Card>}
    {view === "history" && <Card flush title="Deployment history"><Loading state={deployments} empty="No deployments yet.">{(data) => <Table rows={data.data} columns={[{ label: "Version", key: "api_version" }, { label: "Action", key: "action" }, { label: "Release", render: (item) => <code>{item.release_id.slice(0, 8)}</code> }, { label: "Status", render: (item) => <Status value={item.status} /> }, { label: "When", render: (item) => when(item.created_at) }, { label: "", render: (item) => item.id === data.data.find((row) => row.api_version === item.api_version)?.id && item.previous_release_id ? <Button size="sm" disabled={busy} onClick={() => act(() => post(`${base}/api-versions/${item.api_version}/rollback`), `${item.api_version} rolled back`)}>Rollback</Button> : null }]} />}</Loading></Card>}
  </Layout>;
}
