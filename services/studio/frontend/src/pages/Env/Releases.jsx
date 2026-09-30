import { useMemo, useState } from "react";
import Layout from "../../components/Layout";
import { Badge, Button, Card, Field, Loading, PageHead, Status, Table, useAction, when } from "../../components/ui";
import { envPath, post, useApi } from "../../lib/api";

export default function Releases({ project, env }) {
  const base = envPath(project.ref, env);
  const branches = useApi(`${base}/branches`);
  const revisions = useApi(`${base}/revisions`);
  const versions = useApi(`${base}/api-versions`);
  const releases = useApi(`${base}/releases`);
  const deployments = useApi(`${base}/deployments`);
  const [branch, setBranch] = useState("main");
  const [newBranch, setNewBranch] = useState("");
  const [newVersion, setNewVersion] = useState("");
  const [release, setRelease] = useState({ revision_id: "", api_version: "", name: "", allow_breaking: false });
  const [run, busy] = useAction();

  const reload = () => [branches, revisions, versions, releases, deployments].forEach((state) => state.reload());
  const revisionRows = revisions.data?.data || [];
  const versionRows = versions.data?.data || [];
  const validRevisions = useMemo(() => revisionRows.filter((item) => item.status === "valid"), [revisionRows]);
  const releaseDraft = {
    ...release,
    revision_id: release.revision_id || validRevisions[0]?.id || "",
    api_version: release.api_version || versionRows[0]?.name || "",
  };
  const act = async (fn, message) => { if (await run(fn, message)) reload(); };

  return (
    <Layout title="Releases & versions">
      <PageHead
        title="Releases & versions"
        description="Freeze the current definitions into a revision, release it behind a stable /rest/vN path, and roll traffic back atomically. Data and secrets remain environment-scoped."
        actions={<Button variant="primary" disabled={busy} onClick={() => act(() => post(`${base}/branches/${branch}/revisions`, { message: `Snapshot ${branch}` }), "Revision created")}>Create revision</Button>}
      />

      <div className="grid two" style={{ marginBottom: 20 }}>
        <Card title="Branches">
          <div className="stack">
            <Field label="Working branch">
              <select value={branch} onChange={(event) => setBranch(event.target.value)}>
                <option value="main">main</option>
                {(branches.data?.data || []).filter((item) => item.name !== "main").map((item) => <option key={item.id}>{item.name}</option>)}
              </select>
            </Field>
            <div className="row">
              <input value={newBranch} onChange={(event) => setNewBranch(event.target.value)} placeholder="feature-checkout" />
              <Button disabled={busy || !newBranch} onClick={() => act(async () => { await post(`${base}/branches`, { name: newBranch, from_revision: validRevisions[0]?.id || null }); setBranch(newBranch); setNewBranch(""); }, "Branch created")}>Add branch</Button>
            </div>
          </div>
        </Card>
        <Card title="Public API versions">
          <div className="row" style={{ marginBottom: 12 }}>
            <input value={newVersion} onChange={(event) => setNewVersion(event.target.value)} placeholder="v2" />
            <Button disabled={busy || !/^v[1-9][0-9]*$/.test(newVersion)} onClick={() => act(async () => { await post(`${base}/api-versions`, { name: newVersion }); setNewVersion(""); }, "API version created")}>Add version</Button>
          </div>
          <Loading state={versions} empty="No API versions yet.">
            {(data) => <div className="row wrap">{data.data.map((item) => <span className="row" key={item.id}><Badge>{item.name}</Badge><Status value={item.status} /></span>)}</div>}
          </Loading>
        </Card>
      </div>

      <Card title="Create a release" actions={<Button variant="primary" disabled={busy || !releaseDraft.revision_id || !releaseDraft.api_version || !releaseDraft.name} onClick={() => act(async () => { await post(`${base}/releases`, releaseDraft); setRelease({ revision_id: "", api_version: "", name: "", allow_breaking: false }); }, "Release prepared")}>Prepare release</Button>}>
        <div className="grid three">
          <Field label="Revision"><select value={releaseDraft.revision_id} onChange={(event) => setRelease({ ...release, revision_id: event.target.value })}>{validRevisions.map((item) => <option key={item.id} value={item.id}>#{item.number} · {item.branch} · {item.message || item.checksum.slice(0, 8)}</option>)}</select></Field>
          <Field label="API version"><select value={releaseDraft.api_version} onChange={(event) => setRelease({ ...release, api_version: event.target.value })}>{versionRows.map((item) => <option key={item.id}>{item.name}</option>)}</select></Field>
          <Field label="Release name"><input value={release.name} onChange={(event) => setRelease({ ...release, name: event.target.value })} placeholder="v2.0.0" /></Field>
        </div>
        <label className="row"><input type="checkbox" checked={release.allow_breaking} onChange={(event) => setRelease({ ...release, allow_breaking: event.target.checked })} style={{ width: "auto" }} /> Acknowledge breaking compatibility findings</label>
      </Card>

      <Card flush title="Releases" className="mt-field">
        <Loading state={releases} empty="No releases prepared.">
          {(data) => <Table rows={data.data} columns={[
            { label: "Release", render: (item) => <b>{item.name}</b> },
            { label: "Path", render: (item) => <code>/rest/{item.api_version}</code> },
            { label: "Status", render: (item) => <Status value={item.status} /> },
            { label: "Compatibility", render: (item) => item.compatibility?.compatible ? <Badge tone="green">compatible</Badge> : <Badge tone="red">breaking</Badge> },
            { label: "Created", render: (item) => when(item.created_at) },
            { label: "", render: (item) => <Button size="sm" disabled={busy || item.status === "active"} onClick={() => act(() => post(`${base}/releases/${item.id}/activate`), `${item.name} activated`)}>Activate</Button> },
          ]} />}
        </Loading>
      </Card>

      <Card flush title="Deployment history" className="mt-field">
        <Loading state={deployments} empty="No deployments yet.">
          {(data) => <Table rows={data.data} columns={[
            { label: "Version", key: "api_version" }, { label: "Action", key: "action" },
            { label: "Release", render: (item) => <code>{item.release_id.slice(0, 8)}</code> },
            { label: "Status", render: (item) => <Status value={item.status} /> },
            { label: "When", render: (item) => when(item.created_at) },
            { label: "", render: (item) => item.id === data.data.find((row) => row.api_version === item.api_version)?.id && item.previous_release_id ? <Button size="sm" disabled={busy} onClick={() => act(() => post(`${base}/api-versions/${item.api_version}/rollback`), `${item.api_version} rolled back`)}>Rollback</Button> : null },
          ]} />}
        </Loading>
      </Card>
    </Layout>
  );
}
