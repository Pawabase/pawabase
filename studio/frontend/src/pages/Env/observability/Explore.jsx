import { useEffect, useMemo, useRef, useState } from "react";
import Chart, { compact } from "../../../components/Chart";
import { Icon } from "../../../components/icons";
import { Badge, Button, Card, Field, Loading, Modal, Segmented, Switch, Table, TagInput, useAction, when } from "../../../components/ui";
import { del, envPath, get, patch, post, useApi } from "../../../lib/api";

const RANGES = [[60, "1h"], [360, "6h"], [1440, "24h"], [4320, "3d"], [10080, "7d"], [20160, "14d"]];
const PERIODS = [[1, "1 min"], [5, "5 min"], [15, "15 min"], [60, "1 hour"], [360, "6 hours"], [1440, "1 day"]];
const defaultPeriod = (minutes) => (minutes <= 60 ? 1 : minutes <= 360 ? 5 : minutes <= 1440 ? 15 : minutes <= 10080 ? 60 : 360);
const LEVEL_TONES = { error: "red", warning: "yellow", warn: "yellow", info: "blue", debug: "", critical: "red" };

function useCatalog(env) {
  const catalog = useApi(envPath(env, "/metric-catalog"));
  return catalog.data?.data || [];
}

/** Pick any metric, a range and a bucket size; compare with the period before; turn what you see into an alarm. */
export function Metrics({ env }) {
  const catalog = useCatalog(env);
  const [metric, setMetric] = useState("requests");
  const [minutes, setMinutes] = useState(360);
  const [period, setPeriod] = useState(0);
  const [compare, setCompare] = useState(false);
  const [alarm, setAlarm] = useState(null);
  const bucket = period || defaultPeriod(minutes);
  const data = useApi(envPath(env, "/metric-series"), { params: { metric, minutes, period: bucket, compare: compare ? "previous" : "" }, interval: 30000 });
  const spec = catalog.find((m) => m.name === metric);
  const d = data.data;
  const rows = useMemo(() => (d?.points || []).map((point, i) => ({ t: point.t, value: point.value ?? 0, previous: d.previous?.[i]?.value ?? 0 })), [d]);
  const latency = metric === "latency_avg" || metric === "latency_p95";
  const unit = d?.unit === "count" ? "" : d?.unit === "%" ? "%" : d?.unit ? ` ${d.unit}` : "";
  const show = (v) => (v == null ? "no data" : `${compact(v)}${unit}`);
  return (
    <div className="stack flex flex-col gap-3 [&.sm]:gap-1.5 [&.lg]:gap-5 lg">
      <div className="explore-bar flex items-center gap-3 flex-wrap [&_select]:w-auto [&_select]:min-w-[150px]">
        <select value={metric} onChange={(e) => setMetric(e.target.value)} aria-label="Metric">
          {catalog.map((m) => <option key={m.name} value={m.name}>{m.label}</option>)}
        </select>
        <Segmented options={RANGES} value={minutes} onChange={(v) => { setMinutes(v); setPeriod(0); }} />
        {!spec?.instant && (
          <select value={bucket} onChange={(e) => setPeriod(Number(e.target.value))} aria-label="Bucket size">
            {PERIODS.map(([v, label]) => <option key={v} value={v}>{label}</option>)}
          </select>
        )}
        {!spec?.instant && <label className="check [label&]:flex [label&]:items-center [label&]:gap-2 [label&]:text-text [label&]:text-ui" style={{ fontSize: 13 }}><input type="checkbox" checked={compare} onChange={(e) => setCompare(e.target.checked)} /> Compare with the period before</label>}
        <span className="grow flex-1 min-w-0" />
        <Button size="sm" onClick={() => setAlarm({ metric, name: `${spec?.label || metric} alarm` })}><Icon name="subscriptions" size={14} /> Create alarm</Button>
      </div>
      <Card title={spec?.label || metric} actions={<span className="muted text-muted small text-ui-sm">{spec?.description}</span>}>
        <Loading state={data}>
          {() => d.instant ? (
            <p className="chart-sum flex items-baseline gap-1.5 flex-wrap m-[0_0_8px] text-muted text-body [&_b]:text-text [&_b]:text-display-sm [&_b]:font-bold-plus [&_b]:tracking-[-0.03em] [&_b_+_b]:text-heading [&_b:not(:first-child)]:text-heading"><b>{show(d.latest)}</b> right now. This measure has no history to chart; alarms can still watch it.</p>
          ) : (
            <>
              <p className="chart-sum flex items-baseline gap-1.5 flex-wrap m-[0_0_8px] text-muted text-body [&_b]:text-text [&_b]:text-display-sm [&_b]:font-bold-plus [&_b]:tracking-[-0.03em] [&_b_+_b]:text-heading [&_b:not(:first-child)]:text-heading">
                <b>{show(d.latest)}</b> latest · average <b>{show(d.average)}</b> · highest <b>{show(d.max)}</b> · lowest <b>{show(d.min)}</b>
              </p>
              <Chart
                height={320}
                stepSeconds={bucket * 60}
                data={rows}
                format={(v) => `${compact(v)}${unit}`}
                series={[
                  ...(compare ? [{ key: "previous", label: "Previous period", color: "var(--faint)", type: latency ? "bar" : "line", grouped: latency, dashed: true }] : []),
                  { key: "value", label: spec?.label || metric, color: "var(--brand)", type: latency ? "bar" : spec?.unit === "count" ? "area" : "line", grouped: latency },
                ]}
                empty="Nothing recorded for this metric in the range."
              />
            </>
          )}
        </Loading>
      </Card>
      {alarm && <AlarmSheet env={env} catalog={catalog} initial={alarm} onClose={() => setAlarm(null)} onSaved={() => setAlarm(null)} />}
    </div>
  );
}

/** Search application and access logs with a small query language, or follow them live. */
export function Logs({ env, onTrace }) {
  const [text, setText] = useState("");
  const [applied, setApplied] = useState("");
  const [minutes, setMinutes] = useState(60);
  const [live, setLive] = useState(false);
  const [rows, setRows] = useState([]);
  const [open, setOpen] = useState(null);
  const [state, setState] = useState({ matched: 0, levels: {}, loading: true, error: null });
  const latest = useRef("");
  const search = async (query = applied, window = minutes) => {
    setState((s) => ({ ...s, loading: true, error: null }));
    try {
      const data = await get(envPath(env, "/logs"), { params: { q: query, minutes: window, limit: 300 } });
      setRows(data.data);
      latest.current = data.data[0]?.at || "";
      setState({ matched: data.matched, levels: data.levels, loading: false, error: null });
    } catch (error) {
      setState((s) => ({ ...s, loading: false, error: error.message }));
    }
  };
  useEffect(() => { search(); }, [env]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!live) return undefined;
    const timer = setInterval(async () => {
      try {
        const data = await get(envPath(env, "/logs"), { params: { q: applied, minutes: 5, limit: 100, after: latest.current || undefined } });
        const fresh = data.data.filter((row) => !latest.current || row.at > latest.current);
        if (fresh.length) { latest.current = fresh[0].at; setRows((current) => [...fresh, ...current].slice(0, 500)); setState((s) => ({ ...s, matched: s.matched + fresh.length })); }
      } catch { /* the next tick tries again */ }
    }, 3000);
    return () => clearInterval(timer);
  }, [live, applied, env]);
  const run = () => { setApplied(text); search(text); };
  const narrow = (level) => { const next = `${text.replace(/\blevel:\S+\s*/g, "").trim()} level:${level}`.trim(); setText(next); setApplied(next); search(next); };
  return (
    <div className="stack flex flex-col gap-3 [&.sm]:gap-1.5 [&.lg]:gap-5 lg">
      <form className="explore-bar flex items-center gap-3 flex-wrap [&_select]:w-auto [&_select]:min-w-[150px]" onSubmit={(e) => { e.preventDefault(); run(); }}>
        <div className="subnav-search flex items-center gap-2 m-[12px_12px_4px] p-[0_10px] [border:1px_solid_var(--line-2)] rounded-sm bg-transparent text-faint [&_input]:flex-1 [&_input]:min-w-0 [&_input]:border-0 [&_input]:outline-none [&_input]:shadow-none [&_input]:bg-transparent [&_input]:p-[8px_0] [&_input]:text-ui [&:focus-within]:border-brand h-control log-query m-0 flex-1 min-w-[320px] [&_input]:font-mono [&_input]:text-ui-sm">
          <Icon name="search" size={14} />
          <input value={text} onChange={(e) => setText(e.target.value)} placeholder='level:error source:function "timed out" -healthcheck' aria-label="Log query" spellCheck={false} />
        </div>
        <select value={minutes} onChange={(e) => { setMinutes(Number(e.target.value)); search(applied, Number(e.target.value)); }} aria-label="Time range">
          {RANGES.map(([v, label]) => <option key={v} value={v}>Last {label}</option>)}
        </select>
        <Button variant="primary" size="sm" type="submit" disabled={state.loading}>Search</Button>
        <label className="check [label&]:flex [label&]:items-center [label&]:gap-2 [label&]:text-text [label&]:text-ui" style={{ fontSize: 13 }}><input type="checkbox" checked={live} onChange={(e) => setLive(e.target.checked)} /> Follow live</label>
      </form>
      <p className="hint text-xs font-book text-muted leading-[1.45]" style={{ margin: 0 }}>
        Filters: <code>level:</code> <code>source:</code> (use <code>*</code> for patterns) <code>status:5xx</code> <code>route:</code> <code>user:</code> <code>ip:</code> <code>method:</code> <code>request:</code>. Other words must all appear; put <code>-</code> before one to exclude it. Quotes keep a phrase together.
      </p>
      <Card flush title={`${state.matched.toLocaleString()} match${state.matched === 1 ? "" : "es"}`} actions={<span className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5" style={{ gap: 6 }}>{Object.entries(state.levels).map(([level, n]) => <button key={level} type="button" className="chip [border:1px_solid_var(--line)] bg-none text-muted rounded-xs p-[3px_11px] font-inherit text-xs cursor-pointer [&:hover]:border-brand [&:hover]:text-brand-strong [&.on]:border-solid [&.on]:border-brand [&.on]:bg-brand-soft [&.on]:text-brand-strong font-semibold [&.active]:bg-text [&.active]:text-surface [&.active]:border-text" onClick={() => narrow(level)}>{level} {n}</button>)}</span>}>
        {state.error && <div className="alert rounded-sm p-[11px_14px] text-ui [&.error]:text-danger [&.error]:bg-danger-soft [&.warn]:text-warn [&.warn]:bg-warn-soft [&.ok]:text-ok [&.ok]:bg-ok-soft [&.info]:text-info [&.info]:bg-info-soft [border:1px_solid_color-mix(in_srgb,_currentColor_22%,_transparent)] error" style={{ margin: 16 }}>{state.error}</div>}
        {!state.error && rows.length === 0 && !state.loading && <div className="empty p-[40px_16px] text-center text-muted text-ui [&_>_.fw-empty]:p-[4px_0] [&_>_.fw-empty]:text-text">Nothing matches. Widen the time range or loosen the query.</div>}
        <div className="log-list grid gap-1.5 font-mono text-ui-sm">
          {rows.map((row, i) => (
            <div key={`${row.at}-${i}`} className={`log-row grid grid-cols-[78px_70px_minmax(120px,_220px)_1fr] gap-2.5 items-baseline p-[7px_20px] [border-top:1px_solid_var(--line)] cursor-pointer [&:hover]:bg-sunken [&_time]:text-faint ${open === i ? "open" : ""}`} onClick={() => setOpen(open === i ? null : i)}>
              <time className="mono font-mono text-ui-sm">{new Date(row.at).toLocaleTimeString([], { hour12: false })}</time>
              <Badge tone={LEVEL_TONES[row.level] ?? ""}>{row.level}</Badge>
              <span className="log-source text-caption text-muted overflow-hidden text-ellipsis whitespace-nowrap mono font-mono text-ui-sm">{row.source}</span>
              <span className="log-message flex-1 min-w-[200px] break-words">{row.message}</span>
              {open === i && (
                <div className="log-detail col-span-full p-[10px_0_6px] cursor-default [&_.kv]:m-[0_0_10px]" onClick={(e) => e.stopPropagation()}>
                  <dl className="kv grid grid-cols-[160px_minmax(0,_1fr)] gap-0 text-ui [&_dt]:text-muted [&_dd]:m-0 [&_dd]:min-w-0 [&_dd]:wrap-anywhere [border-top:1px_solid_var(--line)] [&_dt]:p-[10px_0] [&_dt]:[border-bottom:1px_solid_var(--line)] [&_dd]:p-[10px_0] [&_dd]:[border-bottom:1px_solid_var(--line)]">
                    {Object.entries(row).filter(([k, v]) => v !== "" && v != null && k !== "message").map(([k, v]) => <><dt key={`${k}t`}>{k}</dt><dd key={`${k}d`}>{String(v)}</dd></>)}
                  </dl>
                  {row.request && onTrace && <Button size="sm" onClick={() => onTrace(row.request)}>Open the request trace</Button>}
                </div>
              )}
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}

const COMPARE = [["gt", "is above"], ["gte", "is at or above"], ["lt", "is below"], ["lte", "is at or below"]];
const STATE_TONE = { alarm: "red", ok: "green", unknown: "" };

/** Watch a metric; when it stays over a line, email people. */
export function Alarms({ env }) {
  const catalog = useCatalog(env);
  const alarms = useApi(envPath(env, "/alarms"), { interval: 15000 });
  const [editing, setEditing] = useState(null);
  const [history, setHistory] = useState(null);
  const [run, busy] = useAction();
  const base = envPath(env, "/alarms");
  const rows = alarms.data?.data || [];
  const condition = (a) => `${COMPARE.find(([k]) => k === a.comparison)?.[1]} ${a.threshold}${a.unit === "count" ? "" : a.unit === "%" ? "%" : ` ${a.unit}`}${catalog.find((m) => m.name === a.metric)?.instant ? "" : ` over ${a.window_minutes} min`}${a.breaches_needed > 1 ? `, ${a.breaches_needed} checks in a row` : ""}`;
  return (
    <>
      <Card flush title="Alarms" actions={<Button variant="primary" size="sm" onClick={() => setEditing({})}><Icon name="plus" size={14} /> Create alarm</Button>}>
        <Loading state={alarms} empty="No alarms yet.">
          {() => (
            <Table
              rows={rows}
              empty="No alarms yet. An alarm watches a metric and emails people when it stays over a line."
              columns={[
                { label: "State", render: (a) => <span className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5" style={{ gap: 6 }}><Badge tone={a.enabled ? STATE_TONE[a.state] : ""}>{a.enabled ? a.state : "off"}</Badge>{a.muted && <Badge>muted</Badge>}</span> },
                { label: "Alarm", render: (a) => <span className="stack flex flex-col gap-3 [&.sm]:gap-1.5 [&.lg]:gap-5 sm" style={{ gap: 0 }}><b>{a.name}</b><span className="muted text-muted small text-ui-sm">{a.metric_label} {condition(a)}</span></span> },
                { label: "Last value", render: (a) => (a.last_value == null ? <span className="faint text-faint">—</span> : compact(a.last_value)) },
                { label: "In this state since", render: (a) => (a.state_since ? when(a.state_since) : <span className="faint text-faint">—</span>) },
                { label: "Notifies", render: (a) => ((a.recipients || []).length ? `${a.recipients.length} address${a.recipients.length === 1 ? "" : "es"}` : <span className="faint text-faint">nobody</span>) },
                { label: "", render: (a) => (
                  <span className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5" style={{ gap: 6 }}>
                    <Button size="sm" disabled={busy} onClick={async () => { if (await run(() => post(`${base}/${a.id}/check`), "Checked")) alarms.reload(); }}>Check now</Button>
                    <Button size="sm" onClick={() => setHistory(a)}>History</Button>
                    <Button size="sm" onClick={() => setEditing(a)}>Edit</Button>
                  </span>
                ) },
              ]}
            />
          )}
        </Loading>
      </Card>
      {editing && <AlarmSheet env={env} catalog={catalog} initial={editing} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); alarms.reload(); }} />}
      {history && <HistorySheet base={base} alarm={history} onClose={() => setHistory(null)} />}
    </>
  );
}

function HistorySheet({ base, alarm, onClose }) {
  const events = useApi(`${base}/${alarm.id}/events`);
  return (
    <Modal title={`${alarm.name}: history`} onClose={onClose} footer={<Button onClick={onClose}>Close</Button>}>
      <Loading state={events} empty="Nothing has changed yet.">
        {(data) => (
          <Table
            rows={data.data}
            empty="Nothing has changed yet."
            columns={[
              { label: "When", render: (e) => when(e.created_at) },
              { label: "Change", render: (e) => <span className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5" style={{ gap: 6 }}><Badge tone={STATE_TONE[e.from_state]}>{e.from_state}</Badge>→<Badge tone={STATE_TONE[e.to_state]}>{e.to_state}</Badge></span> },
              { label: "Detail", render: (e) => <span className="muted text-muted small text-ui-sm">{e.message}</span> },
              { label: "Email", render: (e) => (e.notified ? <Badge tone="green">sent</Badge> : <span className="faint text-faint small text-ui-sm">{e.error || "not sent"}</span>) },
            ]}
          />
        )}
      </Loading>
    </Modal>
  );
}

export function AlarmSheet({ env, catalog, initial, onClose, onSaved }) {
  const base = envPath(env, "/alarms");
  const isNew = !initial.id;
  const [form, setForm] = useState({ name: "", description: "", metric: "errors_5xx", comparison: "gt", threshold: 0, window_minutes: 5, breaches_needed: 1, recipients: [], enabled: true, renotify_minutes: 0, ...initial });
  const [run, busy] = useAction();
  const set = (values) => setForm((f) => ({ ...f, ...values }));
  const spec = catalog.find((m) => m.name === form.metric);
  const unit = spec?.unit === "count" ? "" : spec?.unit;
  const save = async () => {
    const body = { name: form.name.trim(), description: form.description, metric: form.metric, comparison: form.comparison, threshold: Number(form.threshold), window_minutes: Number(form.window_minutes) || 5, breaches_needed: Number(form.breaches_needed) || 1, recipients: form.recipients, enabled: form.enabled, renotify_minutes: Number(form.renotify_minutes) || 0 };
    if (await run(() => (isNew ? post(base, body) : patch(`${base}/${initial.id}`, body)), isNew ? "Alarm created" : "Alarm saved")) onSaved();
  };
  return (
    <Modal title={isNew ? "Create alarm" : `Edit ${initial.name}`} onClose={onClose} footer={<>
      {!isNew && <Button variant="danger" disabled={busy} onClick={async () => { if (confirm(`Delete ${initial.name}?`) && await run(() => del(`${base}/${initial.id}`), "Alarm deleted")) onSaved(); }}>Delete</Button>}
      <span className="grow flex-1 min-w-0" />
      <Button variant="primary" disabled={busy || !form.name.trim()} onClick={save}>{isNew ? "Create alarm" : "Save"}</Button>
    </>}>
      <Field label="Name"><input autoFocus value={form.name} onChange={(e) => set({ name: e.target.value })} placeholder="Checkout errors" /></Field>
      <Field label="Metric" hint={spec?.description}>
        <select value={form.metric} onChange={(e) => set({ metric: e.target.value })}>{catalog.map((m) => <option key={m.name} value={m.name}>{m.label}</option>)}</select>
      </Field>
      <div className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5 top" style={{ gap: 12 }}>
        <Field label="Alarm when it" className="grow flex-1 min-w-0"><select value={form.comparison} onChange={(e) => set({ comparison: e.target.value })}>{COMPARE.map(([k, label]) => <option key={k} value={k}>{label}</option>)}</select></Field>
        <Field label={`Line${unit ? ` (${unit})` : ""}`}><input type="number" step="any" value={form.threshold} onChange={(e) => set({ threshold: e.target.value })} style={{ width: 130 }} /></Field>
      </div>
      {!spec?.instant && (
        <div className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5 top" style={{ gap: 12 }}>
          <Field label="Measured over (minutes)" className="grow flex-1 min-w-0" hint="Each check looks at this much recent time."><input type="number" min={1} value={form.window_minutes} onChange={(e) => set({ window_minutes: e.target.value })} /></Field>
          <Field label="Checks in a row" className="grow flex-1 min-w-0" hint="Checks run every minute. More than one ignores a brief blip."><input type="number" min={1} value={form.breaches_needed} onChange={(e) => set({ breaches_needed: e.target.value })} /></Field>
        </div>
      )}
      <Field label="Email these people" hint="They are told when the alarm fires and when it recovers, through this environment's mail provider.">
        <TagInput value={form.recipients} onChange={(v) => set({ recipients: v.map((x) => x.trim()) })} placeholder="ops@example.com" />
      </Field>
      <Field label="Remind every (minutes)" optional hint="While it stays in alarm. Empty tells people once."><input type="number" min={0} value={form.renotify_minutes} onChange={(e) => set({ renotify_minutes: e.target.value })} style={{ maxWidth: 160 }} /></Field>
      <Field label="Note" optional><input value={form.description} onChange={(e) => set({ description: e.target.value })} placeholder="What to do when this fires" /></Field>
      <Switch checked={form.enabled} onChange={(v) => set({ enabled: v })} label="On" hint="Off keeps the alarm but stops checking it." />
      {!isNew && (
        <section className="form-section bg-transparent rounded-none p-[20px_0] flex flex-col gap-3.5 border-0 [border-bottom:1px_solid_var(--line)] [&:last-child]:border-b-0">
          <div className="form-section-head flex items-start justify-between gap-3 [&_h3]:text-base [&_p]:m-[3px_0_0] [&_p]:text-ui-sm [&_p]:text-muted [&_.row]:shrink-0 [&_>_.btn]:shrink-0 [&_h3]:font-semibold"><div><h3>Test the email</h3><p>Sends the current wording to the addresses above, whatever the state.</p></div><Button size="sm" disabled={busy} onClick={async () => { const r = await run(() => post(`${base}/${initial.id}/test`)); if (r && r !== true) alert(r.sent ? "Sent." : `Not sent: ${r.error}`); }}>Send test</Button></div>
        </section>
      )}
    </Modal>
  );
}
