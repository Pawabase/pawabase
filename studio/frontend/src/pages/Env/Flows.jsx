import { Link, router } from "@inertiajs/react";
import { useState } from "react";
import Layout, { envHref } from "../../components/Layout";
import { Icon } from "../../components/icons";
import { Badge, Button, Card, Json, Loading, PageHead, Sheet, Status, Table, when } from "../../components/ui";
import { envPath, useApi } from "../../lib/api";

export default function Flows({ env }) {
  const base = envPath(env);
  const flows = useApi(`${base}/flows`);
  const [runsOpen, setRunsOpen] = useState(false);
  const editor = (name) => envHref(env, "flows", name);
  return (
    <Layout title="Flows">
      <PageHead
        title="Flows"
        description="Visual workflows built from blocks. Triggered by routes, events, schedules, webhooks, realtime messages or other flows."
        actions={<>
          <Button onClick={() => setRunsOpen(true)}><Icon name="events" />Recent runs</Button>
          <Link className="btn primary" href={editor("new")}>New flow</Link>
        </>}
      />
      <Card flush>
        <Loading state={flows} empty="No flows yet. Create one to start automating.">
          {(data) => (
            <Table
              rows={data.data}
              onRowClick={(f) => router.visit(editor(f.name))}
              columns={[
                { label: "Name", render: (f) => <b>{f.name}</b> },
                { label: "Description", key: "description" },
                { label: "Triggers", render: (f) => <div className="row wrap">{triggers(f).map((t) => <Badge key={t} tone="blue">{t}</Badge>)}</div> },
                { label: "Blocks", render: (f) => f.definition?.nodes?.length ?? 0 },
                { label: "State", render: (f) => <Badge tone={f.enabled ? "green" : ""}>{f.enabled ? "enabled" : "disabled"}</Badge> },
              ]}
            />
          )}
        </Loading>
      </Card>
      {runsOpen && (
        <RunsSheet base={base} onOpenFlow={(flow) => router.visit(editor(flow))} onClose={() => setRunsOpen(false)} />
      )}
    </Layout>
  );
}

/** The system's side sheet, showing recent runs; picking one swaps the same
 *  sheet to that run's detail, with a back arrow to return to the list. */
function RunsSheet({ base, onOpenFlow, onClose }) {
  const [run, setRun] = useState(null);
  const runs = useApi(`${base}/flow-runs`, { params: { limit: 30 }, interval: 10000 });
  const detail = useApi(run ? `${base}/flow-runs/${run.id}` : null);

  if (!run) {
    return (
      <Sheet title="Recent runs" icon="events" tone="lavender" onClose={onClose}>
        <Loading state={runs} empty="No runs yet.">
          {(data) => (
            <Table
              rows={data.data}
              onRowClick={(r) => setRun(r)}
              columns={[
                { label: "Flow", key: "flow" },
                { label: "Status", render: (r) => <Status value={r.status} /> },
                { label: "Trigger", key: "trigger" },
                { label: "Duration", render: (r) => (r.duration_ms != null ? `${Math.round(r.duration_ms)} ms` : "—") },
                { label: "When", render: (r) => when(r.created_at) },
              ]}
            />
          )}
        </Loading>
      </Sheet>
    );
  }

  const data = detail.data || run;
  return (
    <Sheet
      title={<button type="button" className="sheet-back" onClick={() => setRun(null)}><Icon name="chevronRight" size={16} className="rotate-180" />{data.flow}</button>}
      subtitle={`Run #${run.id} · ${when(data.created_at)}`}
      icon="flows"
      tone="lavender"
      onClose={onClose}
      footer={<button type="button" className="btn" onClick={() => onOpenFlow(data.flow)}>Open in editor</button>}
    >
      <div className="stack" style={{ gap: 14 }}>
        <div className="row wrap" style={{ gap: 16 }}>
          <div><span className="hint">Status</span><div><Status value={data.status} /></div></div>
          <div><span className="hint">Trigger</span><div>{data.trigger}</div></div>
          <div><span className="hint">Duration</span><div>{data.duration_ms != null ? `${Math.round(data.duration_ms)} ms` : "—"}</div></div>
        </div>
        {data.error && <div className="alert error">{data.error} {data.node && <>at <code>{data.node}</code></>}</div>}
        <Loading state={detail}>
          {(full) => (
            <>
              {full.input !== undefined && <><h3>Input</h3><Json value={full.input} /></>}
              {full.output !== undefined && <><h3>Output</h3><Json value={full.output} /></>}
              <h3>Trace</h3>
              {(full.trace || []).length === 0 && <p className="faint">No trace recorded for this run.</p>}
              {(full.trace || []).map((s, i) => (
                <div key={i} className={`trace-step ${s.error ? "failed" : ""}`}>
                  <div className="spread"><code>{s.node}</code><span className="faint">{s.duration_ms} ms → {s.handle || "end"}</span></div>
                  {s.error ? <div className="error-text">{s.error}</div> : <pre className="faint" style={{ fontSize: 11 }}>{typeof s.output === "string" ? s.output : JSON.stringify(s.output)}</pre>}
                </div>
              ))}
              {full.logs?.length > 0 && <><h3>Logs</h3><Json value={full.logs} /></>}
            </>
          )}
        </Loading>
      </div>
    </Sheet>
  );
}

function triggers(flow) {
  return (flow.definition?.nodes || []).map((n) => n.data?.block).filter((b) => b?.startsWith("trigger.")).map((b) => b.slice(8));
}
