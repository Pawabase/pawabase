import { Badge, Card, CopyText, Empty, Json, Modal, Status } from "../../../components/ui";
import Waterfall from "./Waterfall";
import { MethodBadge, ms, statusTone } from "./shared";

const levelTone = (level) => (level === "error" || level === "critical" ? "red" : level === "warning" ? "yellow" : level === "debug" ? "" : "blue");

export default function RequestTrace({ value, onClose }) {
  const summary = value.summary || {};
  const request = (value.requests || [])[0];
  return (
    <Modal title="Request trace" onClose={onClose}>
      <div className="stack lg">
        <CopyText text={value.request_id} />
        {request && (
          <div className="row wrap">
            <MethodBadge method={request.method} />
            <code>{request.path}</code>
            <Badge tone={statusTone(request.status)}>{request.status}</Badge>
            <span className="faint">{request.route || "unmatched route"}</span>
          </div>
        )}
        <div className="trace-stats">
          <Stat label="Duration" value={ms(summary.duration_ms)} tone={summary.failed ? "red" : ""} />
          <Stat label="Steps" value={summary.spans || 0} hint={summary.failed_spans ? `${summary.failed_spans} failed` : null} />
          <Stat label="DB queries" value={summary.db_queries || 0} hint={summary.db_queries ? `${ms(summary.db_ms)} total` : null} />
          <Stat label="Logs" value={summary.logs || 0} />
          <Stat label="Flows / functions" value={`${summary.flows || 0} / ${summary.functions || 0}`} />
          <Stat label="Events / jobs" value={`${summary.events || 0} / ${summary.jobs || 0}`} />
        </div>

        {value.error && (
          <div className="stack">
            <div className="alert error">{value.error}</div>
            {value.traceback && <details open><summary>Traceback</summary><pre className="code-block">{value.traceback}</pre></details>}
          </div>
        )}

        <section className="stack">
          <h3>Timeline</h3>
          <Waterfall spans={value.spans} total={summary.duration_ms} />
          {summary.slowest_span && <div className="faint">Slowest step: <b>{summary.slowest_span.kind}</b> <code>{summary.slowest_span.name.slice(0, 80)}</code> took {ms(summary.slowest_span.duration_ms)}.</div>}
        </section>

        <Section title={`Logs (${(value.logs || []).length})`} empty={!value.logs?.length}>
          <div className="log-list">
            {(value.logs || []).map((log, index) => (
              <div key={`${log.run_id || "request"}-${index}`} className="log-line">
                <Badge tone={levelTone(log.level)}>{log.level || "info"}</Badge>
                <span className="log-message">{log.message}</span>
                <span className="faint log-source">{log.source}{log.at_ms != null ? ` · +${ms(log.at_ms)}` : ""}</span>
                {(log.data != null || Object.keys(log.tags || {}).length > 0) && <Json value={{ tags: log.tags || {}, data: log.data }} />}
              </div>
            ))}
          </div>
        </Section>

        <Section title={`Functions (${(value.function_runs || []).length})`} empty={!value.function_runs?.length}>
          {(value.function_runs || []).map((run) => (
            <Card key={run.id} title={run.function} actions={<Status value={run.status} />}>
              <div className="faint">{run.trigger} · {ms(run.duration_ms)} · run <code>{run.id}</code></div>
              {run.error && <div className="alert error">{run.error}</div>}
              <details><summary>Input and output</summary><Json value={{ input: run.input, output: run.output }} /></details>
            </Card>
          ))}
        </Section>

        <Section title={`Flow runs (${(value.flow_runs || []).length})`} empty={!value.flow_runs?.length}>
          {(value.flow_runs || []).map((run) => (
            <Card key={run.id} title={run.flow} actions={<Status value={run.status} />}>
              <div className="faint">{run.trigger} · {ms(run.duration_ms)} · run <code>{run.id}</code></div>
              {run.error && <div className="alert error">{run.error}</div>}
              <details><summary>Input and output</summary><Json value={{ input: run.input, output: run.output }} /></details>
              <details open={run.status === "failed"}><summary>Step trace ({run.trace?.length || 0})</summary><Json value={run.trace || []} /></details>
            </Card>
          ))}
        </Section>

        <Section title={`Events (${(value.events || []).length})`} empty={!value.events?.length}>
          {(value.events || []).map((event) => <Card key={event.id} title={<code>{event.name}</code>}><Json value={event} /></Card>)}
        </Section>

        <Section title={`Jobs (${(value.jobs || []).length})`} empty={!value.jobs?.length}>
          {(value.jobs || []).map((job) => <Card key={job.id} title={job.job} actions={<Status value={job.status} />}><Json value={job} /></Card>)}
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
    <div className="trace-stat">
      <div className="faint">{label}</div>
      <div className={`trace-stat-value ${tone || ""}`}>{value}</div>
      {hint && <div className="faint">{hint}</div>}
    </div>
  );
}

function Section({ title, empty, children }) {
  return <section className="stack"><h3>{title}</h3>{empty ? <Empty>Nothing recorded.</Empty> : children}</section>;
}
