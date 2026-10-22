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
          <Link className="btn inline-flex items-center justify-center gap-1.5 [border:1px_solid_var(--line-2)] bg-transparent text-text rounded-sm p-[0_12px] font-inherit font-medium-plus text-ui cursor-pointer whitespace-nowrap transition-[background_0.12s,_border-color_0.12s] [&:hover]:border-line-hover [&:active]:transform-[none] [&:disabled]:opacity-45 [&:disabled]:cursor-not-allowed [&.primary]:bg-brand [&.primary]:border-transparent [&.primary]:text-text [&.primary:hover]:filter-none [&.soft]:bg-transparent [&.soft]:border-line-2 [&.soft]:text-text [&.danger]:text-danger [&.danger]:border-line-2 [&.danger]:bg-transparent [&.ghost]:bg-transparent [&.ghost]:border-transparent [&.ghost]:text-muted [&.ghost:hover]:text-text [&.ghost:hover]:bg-hover [&.sm]:p-[0_10px] [&.sm]:text-ui-sm [&.icon-only]:p-0 [&.icon-only]:w-control [&.icon-only]:h-[30px] [&_.icon]:w-4 [&_.icon]:h-4 h-control shadow-none [&:hover]:bg-hover [&:active]:bg-press [&:focus-visible]:outline-[2px_solid_var(--brand)] [&:focus-visible]:outline-offset-[1px] [&.primary:hover]:bg-brand [&.primary:hover]:border-line-hover [&.danger:hover]:bg-danger-soft [&.sm]:h-control-sm [&.sm.icon-only]:w-control-sm [label&]:inline-flex filter-none [&.soft]:shadow-none [&.soft]:filter-none [&.primary]:shadow-none [&.primary]:filter-none [label&]:bg-transparent [label&]:shadow-none [label&]:filter-none [label&.primary]:bg-transparent [label&.primary]:shadow-none [label&.primary]:filter-none [label&.primary]:border-line-2 [label&.primary]:text-text [label&.primary:hover]:bg-hover [label&.primary:hover]:border-line-hover [&.soft:hover]:bg-hover [&:disabled]:bg-transparent [&.primary:disabled]:bg-transparent [&.primary:disabled]:opacity-45 border-line-2 [label&]:border-line-2 [&.soft:hover]:border-line-hover [&.danger:hover]:border-line-hover [&.on]:border-brand [&.on]:text-text primary" href={editor("new")}>New flow</Link>
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
                { label: "Triggers", render: (f) => <div className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5 wrap">{triggers(f).map((t) => <Badge key={t} tone="blue">{t}</Badge>)}</div> },
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
      title={<button type="button" className="sheet-back inline-flex items-center gap-2 border-0 bg-none font-inherit font-bold text-[inherit] text-inherit p-0 cursor-pointer [&:hover]:opacity-[0.75]" onClick={() => setRun(null)}><Icon name="chevronRight" size={16} className="rotate-180 transform-[rotate(180deg)]" />{data.flow}</button>}
      subtitle={`Run #${run.id} · ${when(data.created_at)}`}
      icon="flows"
      tone="lavender"
      onClose={onClose}
      footer={<button type="button" className="btn inline-flex items-center justify-center gap-1.5 [border:1px_solid_var(--line-2)] bg-transparent text-text rounded-sm p-[0_12px] font-inherit font-medium-plus text-ui cursor-pointer whitespace-nowrap transition-[background_0.12s,_border-color_0.12s] [&:hover]:border-line-hover [&:active]:transform-[none] [&:disabled]:opacity-45 [&:disabled]:cursor-not-allowed [&.primary]:bg-brand [&.primary]:border-transparent [&.primary]:text-text [&.primary:hover]:filter-none [&.soft]:bg-transparent [&.soft]:border-line-2 [&.soft]:text-text [&.danger]:text-danger [&.danger]:border-line-2 [&.danger]:bg-transparent [&.ghost]:bg-transparent [&.ghost]:border-transparent [&.ghost]:text-muted [&.ghost:hover]:text-text [&.ghost:hover]:bg-hover [&.sm]:p-[0_10px] [&.sm]:text-ui-sm [&.icon-only]:p-0 [&.icon-only]:w-control [&.icon-only]:h-[30px] [&_.icon]:w-4 [&_.icon]:h-4 h-control shadow-none [&:hover]:bg-hover [&:active]:bg-press [&:focus-visible]:outline-[2px_solid_var(--brand)] [&:focus-visible]:outline-offset-[1px] [&.primary:hover]:bg-brand [&.primary:hover]:border-line-hover [&.danger:hover]:bg-danger-soft [&.sm]:h-control-sm [&.sm.icon-only]:w-control-sm [label&]:inline-flex filter-none [&.soft]:shadow-none [&.soft]:filter-none [&.primary]:shadow-none [&.primary]:filter-none [label&]:bg-transparent [label&]:shadow-none [label&]:filter-none [label&.primary]:bg-transparent [label&.primary]:shadow-none [label&.primary]:filter-none [label&.primary]:border-line-2 [label&.primary]:text-text [label&.primary:hover]:bg-hover [label&.primary:hover]:border-line-hover [&.soft:hover]:bg-hover [&:disabled]:bg-transparent [&.primary:disabled]:bg-transparent [&.primary:disabled]:opacity-45 border-line-2 [label&]:border-line-2 [&.soft:hover]:border-line-hover [&.danger:hover]:border-line-hover [&.on]:border-brand [&.on]:text-text" onClick={() => onOpenFlow(data.flow)}>Open in editor</button>}
    >
      <div className="stack flex flex-col gap-3 [&.sm]:gap-1.5 [&.lg]:gap-5" style={{ gap: 14 }}>
        <div className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5 wrap" style={{ gap: 16 }}>
          <div><span className="hint text-xs font-book text-muted leading-[1.45]">Status</span><div><Status value={data.status} /></div></div>
          <div><span className="hint text-xs font-book text-muted leading-[1.45]">Trigger</span><div>{data.trigger}</div></div>
          <div><span className="hint text-xs font-book text-muted leading-[1.45]">Duration</span><div>{data.duration_ms != null ? `${Math.round(data.duration_ms)} ms` : "—"}</div></div>
        </div>
        {data.error && <div className="alert rounded-sm p-[11px_14px] text-ui [&.error]:text-danger [&.error]:bg-danger-soft [&.warn]:text-warn [&.warn]:bg-warn-soft [&.ok]:text-ok [&.ok]:bg-ok-soft [&.info]:text-info [&.info]:bg-info-soft [border:1px_solid_color-mix(in_srgb,_currentColor_22%,_transparent)] error">{data.error} {data.node && <>at <code>{data.node}</code></>}</div>}
        <Loading state={detail}>
          {(full) => (
            <>
              {full.input !== undefined && <><h3>Input</h3><Json value={full.input} /></>}
              {full.output !== undefined && <><h3>Output</h3><Json value={full.output} /></>}
              <h3>Trace</h3>
              {(full.trace || []).length === 0 && <p className="faint text-faint">No trace recorded for this run.</p>}
              {(full.trace || []).map((s, i) => (
                <div key={i} className={`trace-step [border-left:2px_solid_var(--line-2)] p-[2px_0_8px_10px] text-xs [&.failed]:border-l-danger ${s.error ? "failed" : ""}`}>
                  <div className="spread flex items-center justify-between gap-3"><code>{s.node}</code><span className="faint text-faint">{s.duration_ms} ms → {s.handle || "end"}</span></div>
                  {s.error ? <div className="error-text text-danger text-ui-sm font-medium">{s.error}</div> : <pre className="faint text-faint" style={{ fontSize: 11 }}>{typeof s.output === "string" ? s.output : JSON.stringify(s.output)}</pre>}
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
