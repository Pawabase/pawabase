import { Badge, Card, CopyText, Empty, Json, Modal, Status } from "../../../components/ui";
import Waterfall from "./Waterfall";
import { MethodBadge, ms, statusTone } from "./shared";

const levelTone = (level) => (level === "error" || level === "critical" ? "red" : level === "warning" ? "yellow" : level === "debug" ? "" : "blue");

export default function RequestTrace({ value, onClose }) {
  const summary = value.summary || {};
  const request = (value.requests || [])[0];
  return (
    <Modal title="Request trace" onClose={onClose}>
      <div className="stack flex flex-col gap-3 [&.sm]:gap-1.5 [&.lg]:gap-5 lg">
        <CopyText text={value.request_id} />
        {request && (
          <div className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5 wrap">
            <MethodBadge method={request.method} />
            <code>{request.path}</code>
            <Badge tone={statusTone(request.status)}>{request.status}</Badge>
            <span className="faint text-faint">{request.route || "unmatched route"}</span>
          </div>
        )}
        <div className="statstrip flex flex-wrap items-baseline gap-[4px_0] p-0 m-[0_0_14px] bg-none rounded-none overflow-visible [&_>_div]:inline-flex [&_>_div]:items-baseline [&_>_div]:gap-[7px] [&_>_div]:p-[0_18px] [&_>_div]:m-0 [&_>_div]:border-0 [&_>_div]:bg-none [&_>_div]:[border-left:1px_solid_var(--line-2)] [&_>_div:first-child]:pl-0 [&_>_div:first-child]:border-l-0 [&_.v]:text-heading [&_.v]:font-bold [&_.v]:tracking-[-0.02em] [&_.v]:tabular-nums [&_b]:text-heading [&_b]:font-bold [&_b]:tracking-[-0.02em] [&_b]:tabular-nums [&_.l]:inline-flex [&_.l]:items-center [&_.l]:gap-1.5 [&_.l]:text-muted [&_.l]:text-ui [&_.l]:font-medium [&_.l]:normal-case [&_span:not(.v)]:inline-flex [&_span:not(.v)]:items-center [&_span:not(.v)]:gap-1.5 [&_span:not(.v)]:text-muted [&_span:not(.v)]:text-ui [&_span:not(.v)]:font-medium [&_span:not(.v)]:normal-case [&_.dot]:w-1.5 [&_.dot]:h-1.5 [&_.v.bad]:text-danger [&_>_div]:min-w-0">
          <Stat label="Duration" value={ms(summary.duration_ms)} tone={summary.failed ? "red" : ""} />
          <Stat label="Steps" value={summary.spans || 0} hint={summary.failed_spans ? `${summary.failed_spans} failed` : null} />
          <Stat label="DB queries" value={summary.db_queries || 0} hint={summary.db_queries ? `${ms(summary.db_ms)} total` : null} />
          <Stat label="Logs" value={summary.logs || 0} />
          <Stat label="Flow runs" value={summary.flows || 0} />
          <Stat label="Events / jobs" value={`${summary.events || 0} / ${summary.jobs || 0}`} />
        </div>

        {value.error && (
          <div className="stack flex flex-col gap-3 [&.sm]:gap-1.5 [&.lg]:gap-5">
            <div className="alert rounded-sm p-[11px_14px] text-ui [&.error]:text-danger [&.error]:bg-danger-soft [&.warn]:text-warn [&.warn]:bg-warn-soft [&.ok]:text-ok [&.ok]:bg-ok-soft [&.info]:text-info [&.info]:bg-info-soft [border:1px_solid_color-mix(in_srgb,_currentColor_22%,_transparent)] error">{value.error}</div>
            {value.traceback && <details open><summary>Traceback</summary><pre className="code-block bg-head-bg rounded-sm p-[12px_14px] overflow-auto max-h-[420px] [border:1px_solid_var(--line)] [pre&]:[border:1px_solid_var(--line)] [pre&]:rounded-sm [pre&]:bg-head-bg">{value.traceback}</pre></details>}
          </div>
        )}

        <section className="stack flex flex-col gap-3 [&.sm]:gap-1.5 [&.lg]:gap-5">
          <h3>Timeline</h3>
          <Waterfall spans={value.spans} total={summary.duration_ms} />
          {summary.slowest_span && <div className="faint text-faint">Slowest step: <b>{summary.slowest_span.kind}</b> <code>{summary.slowest_span.name.slice(0, 80)}</code> took {ms(summary.slowest_span.duration_ms)}.</div>}
        </section>

        <Section icon="terminal" title={`Logs (${(value.logs || []).length})`} empty={!value.logs?.length}>
          <div className="log-list grid gap-1.5 font-mono text-ui-sm">
            {(value.logs || []).map((log, index) => (
              <div key={`${log.run_id || "request"}-${index}`} className="log-line flex items-baseline gap-2.5 flex-wrap p-[6px_10px] rounded-sm bg-sunken">
                <Badge tone={levelTone(log.level)}>{log.level || "info"}</Badge>
                <span className="log-message flex-1 min-w-[200px] break-words">{log.message}</span>
                <span className="faint text-faint log-source text-caption text-muted overflow-hidden text-ellipsis whitespace-nowrap">{log.source}{log.at_ms != null ? ` · +${ms(log.at_ms)}` : ""}</span>
                {(log.data != null || Object.keys(log.tags || {}).length > 0) && <Json value={{ tags: log.tags || {}, data: log.data }} />}
              </div>
            ))}
          </div>
        </Section>

        <Section icon="flows" title={`Flow runs (${(value.flow_runs || []).length})`} empty={!value.flow_runs?.length}>
          {(value.flow_runs || []).map((run) => (
            <Card icon="flows" key={run.id} title={run.flow} actions={<Status value={run.status} />}>
              <div className="faint text-faint">{run.trigger} · {ms(run.duration_ms)} · run <code>{run.id}</code></div>
              {run.error && <div className="alert rounded-sm p-[11px_14px] text-ui [&.error]:text-danger [&.error]:bg-danger-soft [&.warn]:text-warn [&.warn]:bg-warn-soft [&.ok]:text-ok [&.ok]:bg-ok-soft [&.info]:text-info [&.info]:bg-info-soft [border:1px_solid_color-mix(in_srgb,_currentColor_22%,_transparent)] error">{run.error}</div>}
              <details><summary>Input and output</summary><Json value={{ input: run.input, output: run.output }} /></details>
              <details open={run.status === "failed"}><summary>Step trace ({run.trace?.length || 0})</summary><Json value={run.trace || []} /></details>
            </Card>
          ))}
        </Section>

        <Section icon="events" title={`Events (${(value.events || []).length})`} empty={!value.events?.length}>
          {(value.events || []).map((event) => <Card icon="events" key={event.id} title={<code>{event.name}</code>}><Json value={event} /></Card>)}
        </Section>

        <Section icon="jobs" title={`Jobs (${(value.jobs || []).length})`} empty={!value.jobs?.length}>
          {(value.jobs || []).map((job) => <Card icon="jobs" key={job.id} title={job.job} actions={<Status value={job.status} />}><Json value={job} /></Card>)}
        </Section>

        {request && (
          <details>
            <summary>Request details</summary>
            <Json value={{ caller: request.user || request.role || "anonymous", ip: request.ip, user_agent: request.user_agent, started_at: request.started_at, notes: { ...request.notes, spans: undefined, logs: undefined, traceback: undefined } }} />
          </details>
        )}
      </div>
    </Modal>
  );
}

function Stat({ label, value, hint, tone }) {
  return (
    <div>
      <span className={`v ${tone === "red" ? "bad" : ""}`}>{value}</span>
      <span className="l">{label}{hint ? ` · ${hint}` : ""}</span>
    </div>
  );
}

function Section({ title, empty, children }) {
  return <section className="stack flex flex-col gap-3 [&.sm]:gap-1.5 [&.lg]:gap-5"><h3>{title}</h3>{empty ? <Empty>Nothing recorded.</Empty> : children}</section>;
}
