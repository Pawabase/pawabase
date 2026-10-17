import { useEffect, useState } from "react";
import Layout from "../../components/Layout";
import { Badge, Button, Card, CopyText, EmptyState, Field, Loading, PageHead, Section, Segmented, Sheet, Table, copyToClipboard, useAction, useToast, when } from "../../components/ui";
import { Icon } from "../../components/icons";
import { DefinitionSheet } from "../../components/definitions/DefinitionSheet";
import { KINDS, editable } from "../../lib/kinds";
import { del, envPath, post, put, studioObjectUrl, uploadObject, useApi } from "../../lib/api";

export default function Storage({ env }) {
  const base = envPath(env, "/buckets");
  const buckets = useApi(base);
  const [bucket, setBucket] = useState(null);
  const [editing, setEditing] = useState(null);
  const list = buckets.data?.data || [];
  useEffect(() => { if (!bucket && list.length) setBucket(list[0]); }, [list.length]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!new URLSearchParams(window.location.search).has("new")) return;
    setEditing({ isNew: true, body: KINDS.buckets.blank });
    window.history.replaceState({}, "", window.location.pathname);
  }, []);
  const subnav = {
    title: "Storage",
    loading: buckets.loading,
    empty: "No buckets yet. Create one with the + button.",
    action: { label: "New bucket", onClick: () => setEditing({ isNew: true, body: KINDS.buckets.blank }) },
    groups: [{ title: "Buckets", items: list.map((b) => ({ key: b.name, label: b.name, icon: "buckets", tag: b.public ? "public" : undefined })) }],
    active: bucket?.name,
    onSelect: (name) => setBucket(list.find((b) => b.name === name) || null),
  };
  return (
    <Layout title="Storage" subnav={subnav}>
      <PageHead
        title={bucket ? bucket.name : "Storage"}
        description="Buckets on the environment's storage (local disk or any S3-compatible service). Objects are delivered from your application's gateway and follow each bucket's access policies."
      />
      {bucket ? <Objects key={bucket.name} base={base} bucket={bucket} env={env} onEdit={() => setEditing({ isNew: false, body: editable(bucket) })} /> : (
        <Card><EmptyState icon="storage" tone="sky" title="No bucket yet">Create a bucket with the + button in the column on the left.</EmptyState></Card>
      )}
      {editing && (
        <DefinitionSheet
          kind="buckets"
          env={env}
          editing={{ ...editing, key: editing.body.name }}
          onSave={(body) => (editing.isNew ? post(base, body) : put(`${base}/${editing.body.name}`, body))}
          onDelete={() => del(`${base}/${editing.body.name}`)}
          onClose={(result) => { setEditing(null); if (result !== undefined) { buckets.reload(); setBucket(null); } }}
        />
      )}
    </Layout>
  );
}

function Objects({ base, bucket, env, onEdit }) {
  const [prefix, setPrefix] = useState("");
  const objects = useApi(`${base}/${bucket.name}/objects`, { params: { prefix } });
  const [selected, setSelected] = useState(null);
  const [run, busy] = useAction();
  const upload = async (file) => {
    await run(() => uploadObject(env, bucket.name, prefix + file.name, file), `Uploaded ${file.name}`);
    objects.reload();
  };
  const up = () => setPrefix(prefix.split("/").slice(0, -2).join("/") + (prefix.split("/").length > 2 ? "/" : ""));
  const newFolder = async () => {
    const name = (window.prompt("Folder name") || "").trim().replace(/^\/+|\/+$/g, "");
    if (!name) return;
    if (await run(() => post(`${base}/${bucket.name}/folders`, { prefix: `${prefix}${name}` }), `Created ${name}/`)) objects.reload();
  };
  const removeFolder = async (key) => {
    const name = key.replace(/\/+$/, "");
    if (confirm(`Delete the folder ${name}/? It must be empty.`) && await run(() => del(`${base}/${bucket.name}/folders/${name}`), "Folder deleted")) objects.reload();
  };
  return (
    <Card flush title={<span>{bucket.name} <span className="faint" style={{ fontWeight: 400 }}>/{prefix}</span></span>} actions={<>
      {prefix && <Button size="sm" onClick={up}><Icon name="arrowUp" size={14} /> Up</Button>}
      <Button size="sm" onClick={newFolder}><Icon name="plus" size={14} /> New folder</Button>
      <label className="btn sm primary" style={{ opacity: busy ? 0.55 : 1 }}><Icon name="upload" size={14} /> Upload<input type="file" hidden onChange={(e) => { const f = e.target.files[0]; e.target.value = ""; if (f) upload(f); }} /></label>
      <Button size="sm" onClick={onEdit}><Icon name="settings" size={14} /> Settings</Button>
    </>}>
      <Loading state={objects}>
        {(data) => (
          <Table
            rows={[...data.prefixes.map((p) => ({ key: p, folder: true })), ...data.files]}
            empty="No objects here."
            onRowClick={(o) => (o.folder ? setPrefix(o.key) : setSelected(o))}
            columns={[
              { label: "Name", render: (o) => <span style={{ color: "var(--brand)" }}><Icon name={o.folder ? "storage" : "link"} size={14} /> {o.key.slice(prefix.length)}</span> },
              { label: "Type", render: (o) => (o.folder ? "" : o.content_type) },
              { label: "Size", render: (o) => (o.folder ? "" : bytes(o.size)) },
              { label: "Modified", render: (o) => (o.folder || !o.modified ? "" : when(new Date(o.modified * 1000).toISOString())) },
              { label: "", render: (o) => o.folder && <Button size="sm" variant="danger" onClick={(e) => { e.stopPropagation(); removeFolder(o.key); }}><Icon name="trash" size={14} /> Delete</Button> },
            ]}
          />
        )}
      </Loading>
      {selected && (
        <ObjectPanel
          base={base}
          env={env}
          bucket={bucket}
          objectKey={selected.key}
          onClose={(changed) => { setSelected(null); if (changed) objects.reload(); }}
        />
      )}
    </Card>
  );
}

const DURATIONS = [
  [900, "15 minutes"],
  [3600, "1 hour"],
  [86400, "24 hours"],
  [604800, "7 days"],
  ["custom", "Custom"],
];
const UNITS = { minutes: 60, hours: 3600, days: 86400 };

function ObjectPanel({ base, env, bucket, objectKey, onClose }) {
  const details = useApi(`${base}/${bucket.name}/object`, { params: { key: objectKey } });
  const [tab, setTab] = useState("overview");
  const [changed, setChanged] = useState(false);
  const [run, busy] = useAction();
  const object = details.data;
  const name = objectKey.split("/").pop();
  const close = () => onClose(changed);
  return (
    <Sheet
      title={name}
      subtitle={`${bucket.name} / ${objectKey}`}
      icon="storage"
      tone="sky"
      tabs={[{ value: "overview", label: "Overview" }, { value: "sharing", label: "Sharing" }, { value: "transform", label: "Transform" }, { value: "metadata", label: "Metadata" }]}
      tab={tab}
      onTab={setTab}
      tabsPlacement="top"
      onClose={close}
    >
      <Loading state={details}>
        {() => (
          <>
            {tab === "overview" && <Overview {...{ base, env, bucket, object, run, busy, onChanged: () => { setChanged(true); details.reload(); }, onDeleted: () => onClose(true) }} />}
            {tab === "sharing" && <Sharing {...{ base, bucket, object, run }} />}
            {tab === "transform" && <Transform {...{ env, bucket, object }} />}
            {tab === "metadata" && <Metadata object={object} />}
          </>
        )}
      </Loading>
    </Sheet>
  );
}

function Overview({ base, env, bucket, object, run, busy, onChanged, onDeleted }) {
  const [renaming, setRenaming] = useState(null);
  const view = studioObjectUrl(env, bucket.name, object.key);
  const replace = async (file) => {
    if (await run(() => uploadObject(env, bucket.name, object.key, file), "Replaced")) onChanged();
  };
  const move = async () => {
    const result = await run(() => post(`${base}/${bucket.name}/object/move`, { source: object.key, target: renaming }), "Moved");
    if (result) onDeleted();
  };
  const remove = async () => {
    if (confirm(`Delete ${object.key}? Share links to it stop working.`) && await run(() => del(`${base}/${bucket.name}/objects/${object.key}`), "Deleted")) onDeleted();
  };
  return (
    <>
      <Preview url={view} object={object} />
      <Section title="Object">
        <dl className="kv">
          <Row label="File name" value={object.name} />
          <Row label="Key" value={<span className="mono">{object.key}</span>} />
          <Row label="Type" value={object.content_type} />
          <Row label="Extension" value={object.extension || "—"} />
          <Row label="Size" value={`${bytes(object.size)} (${object.size.toLocaleString()} bytes)`} />
          <Row label="Bucket" value={object.bucket} />
          <Row label="Created" value={object.created ? new Date(object.created * 1000).toLocaleString() : "Not recorded by this storage provider"} />
          <Row label="Last modified" value={object.modified ? new Date(object.modified * 1000).toLocaleString() : "—"} />
          <Row label="Visibility" value={object.visibility === "public" ? <Badge tone="green">Public</Badge> : <Badge>Restricted by policy</Badge>} />
        </dl>
      </Section>
      <Section title="Address" description={object.visibility === "public" ? "Works directly in <img>, <video> and links." : "Readable only by requests that satisfy the bucket's read policy."}>
        <Field label="Gateway URL"><CopyText text={object.url} /></Field>
      </Section>
      <Section title="Actions">
        <div className="row" style={{ flexWrap: "wrap", gap: 8 }}>
          <a className="btn sm" href={studioObjectUrl(env, bucket.name, object.key, "?download=1")}>Download</a>
          <label className="btn sm" style={{ opacity: busy ? 0.55 : 1 }}>Replace<input type="file" hidden onChange={(e) => { const f = e.target.files[0]; e.target.value = ""; if (f) replace(f); }} /></label>
          <Button size="sm" onClick={() => setRenaming(renaming === null ? object.key : null)}>Rename or move</Button>
          <Button size="sm" variant="danger" onClick={remove}>Delete</Button>
        </div>
        {renaming !== null && (
          <div className="row" style={{ marginTop: 12 }}>
            <input className="mono grow" value={renaming} onChange={(e) => setRenaming(e.target.value)} />
            <Button size="sm" variant="primary" disabled={!renaming || renaming === object.key} onClick={move}>Move</Button>
          </div>
        )}
      </Section>
    </>
  );
}

function Preview({ url, object }) {
  const type = object.content_type || "";
  const asText = /^text\//.test(type) || type === "application/json";
  const [body, setBody] = useState(null);
  useEffect(() => {
    if (!asText || object.size > 512 * 1024) return undefined;
    let live = true;
    fetch(url, { credentials: "same-origin" }).then((r) => r.text()).then((t) => { if (live) setBody(t); }).catch(() => { if (live) setBody(""); });
    return () => { live = false; };
  }, [url, asText, object.size]);
  const frame = { display: "block", width: "100%", maxHeight: "50vh", borderRadius: "var(--r)", background: "var(--sunken)" };
  let content;
  if (type.startsWith("image/") && type !== "image/svg+xml") content = <img src={url} alt={object.name} style={{ ...frame, objectFit: "contain" }} />;
  else if (type.startsWith("video/")) content = <video src={url} controls preload="metadata" style={frame} />;
  else if (type.startsWith("audio/")) content = <audio src={url} controls preload="metadata" style={{ width: "100%" }} />;
  else if (type === "application/pdf") content = <iframe title={object.name} src={url} style={{ ...frame, height: "50vh", border: 0 }} />;
  else if (asText) content = <pre style={{ ...frame, overflow: "auto", whiteSpace: "pre-wrap", margin: 0, padding: 12 }}>{object.size > 512 * 1024 ? "Too large to preview. Download it instead." : body ?? "Loading…"}</pre>;
  else content = (
    <div style={{ ...frame, padding: 28, textAlign: "center" }}>
      <Icon name="audit" size={36} />
      <p className="hint">No preview for {type || "this type"}. Use Download.</p>
    </div>
  );
  return <Section>{content}</Section>;
}

function Row({ label, value }) {
  return (
    <>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </>
  );
}

function Sharing({ base, bucket, object, run }) {
  const toast = useToast();
  const copy = async (url) => {
    if (await copyToClipboard(url)) toast("Link copied");
    else toast("Could not copy. Select the link and copy it by hand.", "error");
  };
  const shares = useApi(`${base}/${bucket.name}/shares`, { params: { key: object.key } });
  const [duration, setDuration] = useState(3600);
  const [custom, setCustom] = useState({ amount: 2, unit: "hours" });
  const [permissions, setPermissions] = useState("both");
  const [fresh, setFresh] = useState(null);
  const seconds = duration === "custom" ? Math.max(1, Number(custom.amount) || 1) * UNITS[custom.unit] : duration;
  const create = async () => {
    const result = await run(() => post(`${base}/${bucket.name}/shares`, { key: object.key, expires_in: seconds, permissions }), "Link created");
    if (result) { setFresh(result); shares.reload(); }
  };
  const revoke = async (share) => {
    if (await run(() => del(`${base}/${bucket.name}/shares/${share.id}`), "Link revoked")) { if (fresh?.id === share.id) setFresh(null); shares.reload(); }
  };
  return (
    <>
      <Section title="New temporary link" description="Anyone with the link can open this object until it expires or you revoke it. It never grants access to anything else.">
        <Field label="Expires after">
          <Segmented options={DURATIONS} value={duration} onChange={setDuration} />
        </Field>
        {duration === "custom" && (
          <div className="row">
            <input type="number" min="1" style={{ width: 100 }} value={custom.amount} onChange={(e) => setCustom({ ...custom, amount: e.target.value })} />
            <Segmented options={Object.keys(UNITS)} value={custom.unit} onChange={(unit) => setCustom({ ...custom, unit })} />
          </div>
        )}
        <Field label="Allows">
          <Segmented options={[["both", "View and download"], ["view", "View only"], ["download", "Download only"]]} value={permissions} onChange={setPermissions} />
        </Field>
        <div><Button variant="primary" size="sm" onClick={create}>Create link</Button></div>
        {fresh && <Field label={`Expires ${new Date(fresh.expires_at).toLocaleString()}`}><CopyText text={fresh.url} /></Field>}
      </Section>
      <Section title="Links for this object">
        <Loading state={shares}>
          {(data) => (
            <Table
              rows={data.data}
              empty="No links yet."
              columns={[
                { label: "Status", render: (s) => <Badge tone={s.status === "active" ? "green" : undefined}>{s.status}</Badge> },
                { label: "Allows", render: (s) => ({ both: "View and download", view: "View only", download: "Download only" })[s.permissions] },
                { label: "Created", render: (s) => when(s.created_at) },
                { label: "Expires", render: (s) => new Date(s.expires_at).toLocaleString() },
                { label: "Opened", key: "hits" },
                { label: "", render: (s) => s.status === "active" && (
                  <div className="row">
                    {s.url && <Button size="sm" onClick={(e) => { e.stopPropagation(); copy(s.url); }}><Icon name="copy" /> Copy</Button>}
                    <Button size="sm" variant="danger" onClick={() => revoke(s)}>Revoke</Button>
                  </div>
                ) },
              ]}
            />
          )}
        </Loading>
      </Section>
    </>
  );
}

function Transform({ env, bucket, object }) {
  const [opts, setOpts] = useState({ width: 400, height: 400, fit: "cover", format: "webp", quality: 80, rotate: 0 });
  const cfg = object.transforms || {};
  if (!object.transformable) {
    return <Section title="Transformations"><p className="hint">Only PNG, JPEG, WebP, GIF, AVIF and BMP images can be transformed. This object is {object.content_type}.</p></Section>;
  }
  if (cfg.enabled === false) {
    return <Section title="Transformations"><p className="hint">Transformations are turned off for this bucket. Enable them in the bucket's settings.</p></Section>;
  }
  const query = new URLSearchParams(Object.entries(opts).filter(([, v]) => v !== "" && v !== 0 && v !== null)).toString();
  const preview = studioObjectUrl(env, bucket.name, object.key, `?${query}`);
  const gateway = `${object.url}?${query}`;
  const set = (change) => setOpts({ ...opts, ...change });
  const numeric = (name) => (e) => set({ [name]: e.target.value === "" ? "" : Number(e.target.value) });
  return (
    <>
      <Section title="Try a transformation" description="The stored original is never changed. Settings here only build a URL.">
        <div className="form-grid">
          <Field label="Width"><input type="number" min="1" value={opts.width} onChange={numeric("width")} /></Field>
          <Field label="Height"><input type="number" min="1" value={opts.height} onChange={numeric("height")} /></Field>
          <Field label="Fit"><Segmented options={(cfg.fits || ["contain", "cover", "fill"]).map((f) => [f, f])} value={opts.fit} onChange={(fit) => set({ fit })} /></Field>
          <Field label="Format"><Segmented options={[["", "original"], ...(cfg.auto_format === false ? [] : [["auto", "auto"]]), ...(cfg.formats || ["webp", "avif", "jpeg", "png"]).map((f) => [f, f])]} value={opts.format} onChange={(format) => set({ format })} /></Field>
          <Field label="Quality"><input type="number" min="1" max="100" value={opts.quality} onChange={numeric("quality")} /></Field>
          <Field label="Rotate"><Segmented options={[[0, "0°"], [90, "90°"], [180, "180°"], [270, "270°"]]} value={opts.rotate} onChange={(rotate) => set({ rotate })} /></Field>
        </div>
      </Section>
      <Section title="Result">
        <img src={preview} alt="Transformed preview" style={{ maxWidth: "100%", maxHeight: "40vh", borderRadius: "var(--r)", background: "var(--sunken)" }} />
        <Field label="URL"><CopyText text={gateway} /></Field>
      </Section>
    </>
  );
}

function Metadata({ object }) {
  const rows = {
    bucket: object.bucket,
    key: object.key,
    content_type: object.content_type,
    declared_type: object.declared_type || null,
    size: object.size,
    etag: object.etag || null,
    modified: object.modified ? new Date(object.modified * 1000).toISOString() : null,
    provider: object.provider,
    visibility: object.visibility,
    url: object.url,
    active_share_links: object.shares,
  };
  return (
    <Section title="Metadata" description="What the storage provider and the platform know about this object.">
      <Table rows={Object.entries(rows).map(([k, v]) => ({ k, v: v === null ? "—" : String(v) }))} columns={[{ label: "Field", key: "k" }, { label: "Value", render: (r) => <span className="mono" style={{ wordBreak: "break-all" }}>{r.v}</span> }]} />
    </Section>
  );
}

function bytes(n) {
  if (n == null) return "—";
  const units = ["B", "KB", "MB", "GB"];
  let i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i += 1; }
  return `${n.toFixed(i ? 1 : 0)} ${units[i]}`;
}
