import { useEffect, useState } from "react";
import { Icon } from "../../../components/icons";
import { Badge, Button, Card, EmptyState, Field, JsonInput, Loading, Modal, PageHead, Section, Segmented, Sheet, Switch, Table, TagInput, useAction, when } from "../../../components/ui";
import { del, patch, post, put, useApi } from "../../../lib/api";
import { Stat, StatStrip } from "../observability/kit";

const ACTIONS = [["block", "Block"], ["allow", "Allow"], ["log", "Log"], ["rate_limit", "Rate limit"]];
const ACTION_TONES = { block: "red", allow: "green", log: "blue", rate_limit: "yellow", ban: "red" };
const ACTION_LABELS = { block: "Block", allow: "Allow", log: "Log", rate_limit: "Rate limit", ban: "Banned" };
const FIELDS = [
  ["ip", "IP address"], ["country", "Country"], ["method", "Method"], ["path", "Path"], ["host", "Host"], ["query", "Query string"],
  ["user_agent", "User agent"], ["origin", "Origin"], ["referer", "Referer"], ["role", "Key role"], ["key_id", "API key id"],
  ["content_length", "Body size (bytes)"], ["header:", "Header…"], ["query:", "Query parameter…"],
];
const OPS = [
  ["equals", "is"], ["not_equals", "is not"], ["contains", "contains"], ["not_contains", "does not contain"], ["starts_with", "starts with"],
  ["ends_with", "ends with"], ["glob", "matches pattern"], ["regex", "matches regex"], ["in", "is one of"], ["not_in", "is not one of"],
  ["in_cidr", "is in range"], ["not_in_cidr", "is not in range"], ["exists", "is present"], ["missing", "is missing"], ["gt", "is greater than"], ["lt", "is less than"],
];
const LIST_OPS = new Set(["in", "not_in", "in_cidr", "not_in_cidr"]);
const VALUELESS = new Set(["exists", "missing"]);
const OP_LABEL = Object.fromEntries(OPS);
const FIELD_LABEL = Object.fromEntries(FIELDS);
const DURATIONS = [[0, "Never"], [3600, "1 hour"], [86400, "1 day"], [7 * 86400, "7 days"], [30 * 86400, "30 days"]];

/** Starting points for common rules. */
export const TEMPLATES = [
  { label: "Allow office addresses", rule: { name: "Office", action: "allow", conditions: [{ field: "ip", op: "in_cidr", value: ["10.0.0.0/8"] }] } },
  { label: "Block countries", rule: { name: "Blocked countries", action: "block", conditions: [{ field: "country", op: "in", value: ["XX"] }] } },
  { label: "Rate-limit sign-in", rule: { name: "Sign-in burst", action: "rate_limit", conditions: [{ field: "path", op: "starts_with", value: "/auth/v1/token" }], rate_limit: { requests: 10, window_seconds: 60, by: "ip", ban_seconds: 900 } } },
  { label: "Rate-limit the API", rule: { name: "API burst", action: "rate_limit", conditions: [{ field: "path", op: "starts_with", value: "/rest/" }], rate_limit: { requests: 600, window_seconds: 60, by: "ip", ban_seconds: 0 } } },
  { label: "Block bad bots", rule: { name: "Bad bots", action: "block", logic: "any", conditions: [{ field: "user_agent", op: "contains", value: "python-requests" }, { field: "user_agent", op: "contains", value: "curl" }] } },
  { label: "Block large uploads", rule: { name: "Large bodies", action: "block", conditions: [{ field: "content_length", op: "gt", value: "10485760" }] } },
];

const blank = () => ({ name: "", action: "block", enabled: true, mode: "enforce", logic: "all", conditions: [{ field: "path", op: "starts_with", value: "" }], rate_limit: null, expires_at: null, note: "", match: {} });

function describe(rule) {
  const parts = (rule.conditions || []).map((c) => `${FIELD_LABEL[c.field] || c.field} ${OP_LABEL[c.op] || c.op}${VALUELESS.has(c.op) ? "" : ` ${Array.isArray(c.value) ? c.value.join(", ") : c.value}`}`);
  const m = rule.match || {};
  if (m.ips?.length) parts.push(`IP in ${m.ips.join(", ")}`);
  if (m.paths?.length) parts.push(`path ${m.paths.join(" or ")}`);
  if (m.methods?.length) parts.push(`method ${m.methods.join("/")}`);
  if (m.user_agents?.length) parts.push(`agent has ${m.user_agents.join(" or ")}`);
  return parts.join(rule.logic === "any" ? " or " : " and ") || "—";
}

const seconds = (value) => (!value ? "—" : value < 120 ? `${value}s` : value < 7200 ? `${Math.round(value / 60)} min` : value < 172800 ? `${Math.round(value / 3600)} h` : `${Math.round(value / 86400)} days`);

// ── overview ────────────────────────────────────────────────────────────

export function FirewallOverview({ base, onBan, onNewRule }) {
  const [hours, setHours] = useState(24);
  const stats = useApi(`${base}/firewall-stats`, { params: { hours }, interval: 15000 });
  const rules = useApi(`${base}/firewall`);
  const noRules = rules.data && rules.data.data.length === 0;
  return (
    <>
      <PageHead title="Firewall" description="Every request to this environment's API passes through the firewall first: bans, then your rules in order." actions={<Segmented options={[[1, "1 h"], [24, "24 h"], [168, "7 days"]]} value={hours} onChange={setHours} />} />
      {noRules && (
        <div className="fw-empty flex flex-col items-center text-center gap-2 p-[44px_var(--gutter)] [&_h3]:m-[4px_0_0] [&_h3]:text-heading [&_h3]:font-semibold [&_p]:m-[0_0_8px] [&_p]:text-muted [&_p]:text-ui [&_p]:max-w-[460px]">
          <Icon name="policies" size={20} className="faint text-faint" />
          <h3>No rules yet</h3>
          <p>Requests go through unchecked except for bans. Start from a template:</p>
          <div className="fw-templates w-full max-w-[560px] mt-[18px] text-left [border-top:1px_solid_var(--line)] [&_>_span]:block [&_>_span]:p-[12px_0_6px] [&_>_span]:text-2xs [&_>_span]:font-semibold-plus [&_>_span]:tracking-[0.06em] [&_>_span]:uppercase [&_>_span]:text-muted [&_button]:flex [&_button]:flex-col [&_button]:gap-0.5 [&_button]:w-full [&_button]:p-[10px_0] [&_button]:border-0 [&_button]:[border-bottom:1px_solid_var(--line)] [&_button]:bg-none [&_button]:text-inherit [&_button]:font-inherit [&_button]:text-left [&_button]:cursor-pointer [&_button:hover_b]:underline [&_button_b]:text-body [&_button_b]:font-semibold [&_button_small]:text-xs [&_button_small]:text-muted [&_button_small]:overflow-hidden [&_button_small]:text-ellipsis [&_button_small]:whitespace-nowrap">
            {TEMPLATES.map((t) => (
              <button key={t.label} type="button" onClick={() => onNewRule({ ...blank(), ...t.rule })}>
                <b>{t.label}</b><small>{ACTION_LABELS[t.rule.action]} · {describe({ ...blank(), ...t.rule })}</small>
              </button>
            ))}
          </div>
        </div>
      )}
      <Loading state={stats}>
        {(s) => {
          const by = s.by_action || {};
          const hourly = {};
          for (const row of s.timeline || []) hourly[row.hour] = (hourly[row.hour] || 0) + row.count;
          const peak = Math.max(1, ...Object.values(hourly));
          return (
            <>
              <StatStrip>
                <Stat value={s.total} label="Requests acted on" />
                <Stat tone={by.block ? "danger" : undefined} value={by.block || 0} label="Blocked" />
                <Stat tone={by.rate_limit ? "warn" : undefined} value={by.rate_limit || 0} label="Rate limited" />
                <Stat value={by.log || 0} label="Logged" />
                <Stat value={s.active_bans} label="Active bans" />
              </StatStrip>
              {Object.keys(hourly).length > 0 && (
                <Card icon="events" title="Activity">
                  <div className="fw-bars flex items-end gap-[3px] h-[96px] [&_i]:flex-1 [&_i]:min-w-[3px] [&_i]:bg-danger [&_i]:opacity-[0.75] [&_i]:rounded-[2px_2px_0_0]">{Object.entries(hourly).map(([hour, count]) => <i key={hour} title={`${new Date(hour).toLocaleString()} · ${count}`} style={{ height: `${Math.max(4, (count / peak) * 100)}%` }} />)}</div>
                </Card>
              )}
              {s.total === 0 ? (
                !noRules && (
                  <div className="fw-empty flex flex-col items-center text-center gap-2 p-[44px_var(--gutter)] [&_h3]:m-[4px_0_0] [&_h3]:text-heading [&_h3]:font-semibold [&_p]:m-[0_0_8px] [&_p]:text-muted [&_p]:text-ui [&_p]:max-w-[460px]">
                    <Icon name="networking" size={20} className="faint text-faint" />
                    <h3>Nothing to report</h3>
                    <p>No request was blocked, limited or logged in this period.</p>
                  </div>
                )
              ) : (
                <div className="fw-tops grid grid-cols-[repeat(auto-fit,_minmax(320px,_1fr))] gap-3.5 [&.fw-flat]:grid-cols-2 [&.fw-flat]:gap-0 [&.fw-flat_>_.card:nth-child(odd)]:[border-right:1px_solid_var(--line)] [&.fw-flat_>_.card:nth-child(-n_+_2)]:[border-bottom:1px_solid_var(--line)] max-workbench:[&.fw-flat]:grid-cols-1 max-workbench:[&.fw-flat_>_.card]:border-r-0! max-workbench:[&.fw-flat_>_.card]:[border-bottom:1px_solid_var(--line)] fw-flat [&_>_.card]:m-0 [&_>_.card]:border-0 [&_>_.card]:rounded-none [&_>_.card]:bg-transparent [&_>_.card_>_.card-head]:p-[0_var(--gutter)]">
                  <TopList title="Top addresses" rows={s.top_ips} action={(ip) => <Button size="sm" onClick={() => onBan(ip)}>Ban</Button>} />
                  <TopList title="Top paths" rows={s.top_paths} mono />
                  <TopList title="Top rules" rows={s.top_rules} />
                  <TopList title="Top countries" rows={s.top_countries} empty="Set PAWABASE_FIREWALL_COUNTRY_HEADER behind a proxy that adds one." />
                </div>
              )}
            </>
          );
        }}
      </Loading>
    </>
  );
}

function TopList({ title, rows, action, mono, empty = "Nothing yet." }) {
  const max = Math.max(1, ...(rows || []).map((r) => r.count));
  return (
    <Card icon="policies" title={title} flush>
      {!rows?.length ? <p className="muted text-muted" style={{ padding: 16 }}>{empty}</p> : (
        <div className="fw-top flex flex-col">
          {rows.map((r) => (
            <div key={r.value} className="fw-top-row flex items-center gap-2.5 p-[8px_16px] [border-top:1px_solid_var(--line)] text-ui [&:first-child]:border-t-0 [&_.grow]:overflow-hidden [&_.grow]:text-ellipsis [&_.grow]:whitespace-nowrap pl-gutter pr-gutter">
              <span className={`grow flex-1 min-w-0 ${mono ? "mono" : ""}`} title={r.value}>{r.value}</span>
              <span className="fw-top-bar w-[90px] h-1.5 bg-sunken rounded-[3px] overflow-hidden flex-none [&_i]:block [&_i]:h-full [&_i]:bg-danger [&_i]:opacity-70"><i style={{ width: `${(r.count / max) * 100}%` }} /></span>
              <b>{r.count}</b>
              {action && action(r.value)}
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}

// ── rules ───────────────────────────────────────────────────────────────

export function FirewallRules({ base, prefill, onPrefilled }) {
  const list = useApi(`${base}/firewall`);
  const [editing, setEditing] = useState(null);
  const [run] = useAction();
  const reload = () => list.reload();
  useEffect(() => {
    if (!prefill) return;
    setEditing(prefill);
    onPrefilled?.();
  }, [prefill]); // eslint-disable-line react-hooks/exhaustive-deps
  const move = async (rows, index, by) => {
    const ids = rows.map((r) => r.id);
    const [item] = ids.splice(index, 1);
    ids.splice(index + by, 0, item);
    if (await run(() => put(`${base}/firewall-order`, { ids }))) reload();
  };
  return (
    <>
      <PageHead title="Rules" description="Tried in order. The first allow or block that matches decides; log and rate-limit rules record or count and go on. A simulated rule only records what it would have done."
        actions={list.data?.data?.length ? (
          <>
            <select className="fw-template-select w-auto" value="" onChange={(e) => { const t = TEMPLATES[Number(e.target.value)]; if (t) setEditing({ ...blank(), ...t.rule }); }}>
              <option value="">From a template…</option>
              {TEMPLATES.map((t, i) => <option key={t.label} value={i}>{t.label}</option>)}
            </select>
            <Button variant="primary" onClick={() => setEditing(blank())}><Icon name="plus" />New rule</Button>
          </>
        ) : null} />
      <Card flush>
        <Loading state={list} empty={
          <div className="fw-empty flex flex-col items-center text-center gap-2 p-[44px_var(--gutter)] [&_h3]:m-[4px_0_0] [&_h3]:text-heading [&_h3]:font-semibold [&_p]:m-[0_0_8px] [&_p]:text-muted [&_p]:text-ui [&_p]:max-w-[460px]">
            <Icon name="networking" size={20} className="faint text-faint" />
            <h3>No rules yet</h3>
            <p>Requests no rule matches go through, subject to bans and managed rules.</p>
            <Button variant="primary" onClick={() => setEditing(blank())}><Icon name="plus" />New rule</Button>
            <div className="fw-templates w-full max-w-[560px] mt-[18px] text-left [border-top:1px_solid_var(--line)] [&_>_span]:block [&_>_span]:p-[12px_0_6px] [&_>_span]:text-2xs [&_>_span]:font-semibold-plus [&_>_span]:tracking-[0.06em] [&_>_span]:uppercase [&_>_span]:text-muted [&_button]:flex [&_button]:flex-col [&_button]:gap-0.5 [&_button]:w-full [&_button]:p-[10px_0] [&_button]:border-0 [&_button]:[border-bottom:1px_solid_var(--line)] [&_button]:bg-none [&_button]:text-inherit [&_button]:font-inherit [&_button]:text-left [&_button]:cursor-pointer [&_button:hover_b]:underline [&_button_b]:text-body [&_button_b]:font-semibold [&_button_small]:text-xs [&_button_small]:text-muted [&_button_small]:overflow-hidden [&_button_small]:text-ellipsis [&_button_small]:whitespace-nowrap">
              <span>Or start from</span>
              {TEMPLATES.map((t) => (
                <button key={t.label} type="button" onClick={() => setEditing({ ...blank(), ...t.rule })}>
                  <b>{t.label}</b><small>{ACTION_LABELS[t.rule.action]} · {describe({ ...blank(), ...t.rule })}</small>
                </button>
              ))}
            </div>
          </div>
        }>
          {(data) => (
            <Table
              rows={data.data}
              onRowClick={(r) => setEditing(r)}
              columns={[
                { label: "#", render: (r) => <span className="faint text-faint">{data.data.indexOf(r) + 1}</span> },
                { label: "Rule", render: (r) => <div><b>{r.name}</b>{r.mode === "simulate" && <> <Badge tone="lavender">simulate</Badge></>}{r.expired && <> <Badge>expired</Badge></>}<div className="hint text-xs font-book text-muted leading-[1.45]">{describe(r)}</div></div> },
                { label: "Action", render: (r) => <Badge tone={ACTION_TONES[r.action]}>{ACTION_LABELS[r.action]}</Badge> },
                { label: "Limit", render: (r) => (r.rate_limit ? <span className="small text-ui-sm">{r.rate_limit.requests} / {seconds(r.rate_limit.window_seconds)} by {r.rate_limit.by}{r.rate_limit.ban_seconds ? `, ban ${seconds(r.rate_limit.ban_seconds)}` : ""}</span> : <span className="faint text-faint">—</span>) },
                { label: "Hits", render: (r) => <span title={r.last_hit_at ? `Last ${when(r.last_hit_at)}` : "Never matched"}>{r.hits || 0}</span> },
                { label: "Expires", render: (r) => (r.expires_at ? when(r.expires_at) : <span className="faint text-faint">never</span>) },
                { label: "On", render: (r) => <span onClick={(e) => e.stopPropagation()}><Switch checked={r.enabled} onChange={async (enabled) => { if (await run(() => patch(`${base}/firewall/${r.id}`, { enabled }))) reload(); }} /></span> },
                { label: "", render: (r) => { const i = data.data.indexOf(r); return (
                  <div className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5" onClick={(e) => e.stopPropagation()}>
                    <Button size="sm" disabled={i === 0} onClick={() => move(data.data, i, -1)} aria-label="Move up">↑</Button>
                    <Button size="sm" disabled={i === data.data.length - 1} onClick={() => move(data.data, i, 1)} aria-label="Move down">↓</Button>
                  </div>
                ); } },
              ]}
            />
          )}
        </Loading>
      </Card>
      {editing && <RuleSheet base={base} initial={editing} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); reload(); }} />}
    </>
  );
}

function RuleSheet({ base, initial, onClose, onSaved }) {
  const [rule, setRule] = useState(() => ({ ...blank(), ...initial, conditions: (initial.conditions || []).map((c) => ({ ...c })) }));
  const [expiry, setExpiry] = useState(0);
  const [run, busy] = useAction();
  const set = (change) => setRule((current) => ({ ...current, ...change }));
  const setCondition = (index, change) => set({ conditions: rule.conditions.map((c, i) => (i === index ? { ...c, ...change } : c)) });
  const isNew = !rule.id;
  const save = async () => {
    const body = {
      name: rule.name, action: rule.action, enabled: rule.enabled, mode: rule.mode, logic: rule.logic, note: rule.note || "",
      match: rule.match || {},
      conditions: rule.conditions.map((c) => ({ ...c, value: VALUELESS.has(c.op) ? null : c.value })),
      rate_limit: rule.action === "rate_limit" ? rule.rate_limit || { requests: 100, window_seconds: 60, by: "ip", ban_seconds: 0 } : null,
      ...(expiry ? { expires_at: new Date(Date.now() + expiry * 1000).toISOString() } : {}),
    };
    const done = await run(() => (isNew ? post(`${base}/firewall`, body) : patch(`${base}/firewall/${rule.id}`, body)), isNew ? "Rule added" : "Rule saved");
    if (done) onSaved();
  };
  const remove = async () => { if (confirm(`Delete "${rule.name}"?`) && await run(() => del(`${base}/firewall/${rule.id}`), "Rule deleted")) onSaved(); };
  const limit = rule.rate_limit || { requests: 100, window_seconds: 60, by: "ip", ban_seconds: 0 };
  return (
    <Sheet title={isNew ? "New firewall rule" : rule.name} subtitle="Conditions on the request, and what to do when they match" icon="networking" tone="peach" onClose={onClose}
      footer={<>{!isNew && <Button variant="danger" disabled={busy} onClick={remove}>Delete</Button>}<span className="grow flex-1 min-w-0" /><Button onClick={onClose}>Cancel</Button><Button variant="primary" disabled={busy || !rule.name || !rule.conditions.length} onClick={save}>{isNew ? "Add rule" : "Save"}</Button></>}>
      <section className="form-section bg-transparent rounded-none p-[20px_0] flex flex-col gap-3.5 border-0 [border-bottom:1px_solid_var(--line)] [&:last-child]:border-b-0">
        <div className="form-grid grid grid-cols-2 gap-[14px_16px] [&_.span]:col-span-full max-navigation:grid-cols-1">
          <Field label="Name"><input autoFocus value={rule.name} onChange={(e) => set({ name: e.target.value })} placeholder="Block admin from outside" /></Field>
          <Field label="Action"><select value={rule.action} onChange={(e) => set({ action: e.target.value })}>{ACTIONS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></Field>
        </div>
        <div className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5 wrap" style={{ gap: 18, marginTop: 6 }}>
          <Switch checked={rule.mode === "simulate"} onChange={(on) => set({ mode: on ? "simulate" : "enforce" })} label="Simulate" hint="Record what it would do, without doing it." />
          <Switch checked={rule.enabled} onChange={(enabled) => set({ enabled })} label="Enabled" />
        </div>
      </section>
      <section className="form-section bg-transparent rounded-none p-[20px_0] flex flex-col gap-3.5 border-0 [border-bottom:1px_solid_var(--line)] [&:last-child]:border-b-0">
        <div className="form-section-head flex items-start justify-between gap-3 [&_h3]:text-base [&_p]:m-[3px_0_0] [&_p]:text-ui-sm [&_p]:text-muted [&_.row]:shrink-0 [&_>_.btn]:shrink-0 [&_h3]:font-semibold"><div><h3>When</h3><p>Match {rule.logic === "any" ? "any" : "all"} of these.</p></div><select value={rule.logic} onChange={(e) => set({ logic: e.target.value })}><option value="all">All</option><option value="any">Any</option></select></div>
        <div className="fw-conditions flex flex-col gap-2 mb-2.5">
          {rule.conditions.map((c, index) => {
            const prefix = c.field.startsWith("header:") ? "header:" : c.field.startsWith("query:") ? "query:" : null;
            return (
              <div key={index} className="fw-condition flex items-start gap-2 [&_select]:w-auto [&_select]:min-w-[140px] [&_input:not(.grow)]:w-[140px]">
                <select value={prefix || c.field} onChange={(e) => setCondition(index, { field: e.target.value })}>{FIELDS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>
                {prefix && <input className="mono font-mono text-ui-sm" placeholder={prefix === "header:" ? "x-tenant" : "page"} value={c.field.slice(prefix.length)} onChange={(e) => setCondition(index, { field: `${prefix}${e.target.value}` })} />}
                <select value={c.op} onChange={(e) => setCondition(index, { op: e.target.value, value: LIST_OPS.has(e.target.value) ? (Array.isArray(c.value) ? c.value : c.value ? [c.value] : []) : Array.isArray(c.value) ? c.value.join(", ") : c.value })}>{OPS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>
                {VALUELESS.has(c.op) ? <span className="grow flex-1 min-w-0" /> : LIST_OPS.has(c.op) ? (
                  <div className="grow flex-1 min-w-0"><TagInput value={Array.isArray(c.value) ? c.value : []} onChange={(value) => setCondition(index, { value })} placeholder={c.op.includes("cidr") ? "10.0.0.0/8, then Enter" : "a value, then Enter"} /></div>
                ) : (
                  <input className="grow flex-1 min-w-0 mono font-mono text-ui-sm" value={c.value ?? ""} onChange={(e) => setCondition(index, { value: e.target.value })} placeholder={c.op === "regex" ? "^/rest/v1/(admin|internal)" : c.field === "country" ? "NG" : "value"} />
                )}
                <button type="button" className="icon-btn w-control h-control rounded-[calc(var(--r)_-_1px)] grid place-items-center border-0 bg-transparent text-text cursor-pointer [&:hover]:bg-brand-soft [&:hover]:text-brand-strong" aria-label="Remove condition" onClick={() => set({ conditions: rule.conditions.filter((_, i) => i !== index) })}><Icon name="x" size={14} /></button>
              </div>
            );
          })}
        </div>
        <Button size="sm" onClick={() => set({ conditions: [...rule.conditions, { field: "path", op: "starts_with", value: "" }] })}><Icon name="plus" size={13} />Add condition</Button>
      </section>
      {rule.action === "rate_limit" && (
        <section className="form-section bg-transparent rounded-none p-[20px_0] flex flex-col gap-3.5 border-0 [border-bottom:1px_solid_var(--line)] [&:last-child]:border-b-0">
          <div className="form-section-head flex items-start justify-between gap-3 [&_h3]:text-base [&_p]:m-[3px_0_0] [&_p]:text-ui-sm [&_p]:text-muted [&_.row]:shrink-0 [&_>_.btn]:shrink-0 [&_h3]:font-semibold"><div><h3>Limit</h3><p>Past the limit, requests get 429 until the window passes.</p></div></div>
          <div className="form-grid grid grid-cols-2 gap-[14px_16px] [&_.span]:col-span-full max-navigation:grid-cols-1">
            <Field label="Requests"><input type="number" min="1" value={limit.requests} onChange={(e) => set({ rate_limit: { ...limit, requests: Number(e.target.value) } })} /></Field>
            <Field label="Per (seconds)"><input type="number" min="1" value={limit.window_seconds} onChange={(e) => set({ rate_limit: { ...limit, window_seconds: Number(e.target.value) } })} /></Field>
            <Field label="Counted by"><select value={limit.by.startsWith("header:") ? "header" : limit.by} onChange={(e) => set({ rate_limit: { ...limit, by: e.target.value === "header" ? "header:x-user-id" : e.target.value } })}><option value="ip">IP address</option><option value="key">API key</option><option value="ip_path">IP and path</option><option value="header">A header…</option></select></Field>
            {limit.by.startsWith("header:") && <Field label="Header"><input className="mono font-mono text-ui-sm" value={limit.by.slice(7)} onChange={(e) => set({ rate_limit: { ...limit, by: `header:${e.target.value}` } })} /></Field>}
            <Field label="Then ban for" hint="0 only refuses the excess."><select value={limit.ban_seconds} onChange={(e) => set({ rate_limit: { ...limit, ban_seconds: Number(e.target.value) } })}>{[[0, "Don't ban"], [300, "5 minutes"], [900, "15 minutes"], [3600, "1 hour"], [86400, "1 day"]].map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></Field>
          </div>
        </section>
      )}
      <section className="form-section bg-transparent rounded-none p-[20px_0] flex flex-col gap-3.5 border-0 [border-bottom:1px_solid_var(--line)] [&:last-child]:border-b-0">
        <div className="form-grid grid grid-cols-2 gap-[14px_16px] [&_.span]:col-span-full max-navigation:grid-cols-1">
          <Field label="Expires" hint={rule.expires_at ? `Currently ${when(rule.expires_at)}` : "A temporary rule removes itself."}><select value={expiry} onChange={(e) => setExpiry(Number(e.target.value))}>{DURATIONS.map(([v, l]) => <option key={v} value={v}>{v ? `In ${l}` : rule.expires_at ? "Keep as is" : l}</option>)}</select></Field>
          <Field label="Note" optional><input value={rule.note || ""} onChange={(e) => set({ note: e.target.value })} placeholder="Why this rule exists" /></Field>
        </div>
      </section>
    </Sheet>
  );
}

// ── bans ────────────────────────────────────────────────────────────────

export function Bans({ base, prefill, onPrefilled }) {
  const [history, setHistory] = useState(false);
  const bans = useApi(`${base}/firewall-bans`, { params: history ? { all: 1 } : {}, interval: 10000 });
  const [adding, setAdding] = useState(null);
  const [run] = useAction();
  useEffect(() => {
    if (!prefill) return;
    setAdding({ ip: prefill, seconds: 86400, reason: "" });
    onPrefilled?.();
  }, [prefill]); // eslint-disable-line react-hooks/exhaustive-deps
  const now = Date.now();
  return (
    <>
      <PageHead title="IP bans" description="Addresses and ranges refused before any rule runs. Rate-limit rules and automatic bans add to this list; an allow rule still lets an address through." actions={<Button variant="primary" onClick={() => setAdding({ ip: "", seconds: 86400, reason: "" })}><Icon name="plus" />Ban an address</Button>} />
      <Card icon="clock" flush title={<Segmented options={[[false, "Active"], [true, "All, with expired"]]} value={history} onChange={setHistory} />}>
        <Loading state={bans} empty="No address is banned.">
          {(data) => (
            <Table rows={data.data} empty="No address is banned." columns={[
              { label: "Address", render: (b) => <b className="mono font-mono text-ui-sm">{b.ip}</b> },
              { label: "Why", render: (b) => <div><Badge tone={b.source === "manual" ? undefined : "yellow"}>{b.source === "auto" ? "automatic" : b.source === "rate_limit" ? "rate limit" : "manual"}</Badge> <span className="small text-ui-sm">{b.reason}</span></div> },
              { label: "Refused", render: (b) => b.hits },
              { label: "Since", render: (b) => when(b.created_at) },
              { label: "Until", render: (b) => (!b.expires_at ? "Removed by hand" : new Date(b.expires_at).getTime() < now ? <span className="faint text-faint">expired</span> : when(b.expires_at)) },
              { label: "", render: (b) => (!b.expires_at || new Date(b.expires_at).getTime() > now ? <Button size="sm" onClick={async () => { if (await run(() => del(`${base}/firewall-bans/${b.id}`), "Ban lifted")) bans.reload(); }}>Lift</Button> : null) },
            ]} />
          )}
        </Loading>
      </Card>
      {adding && (
        <Modal title="Ban an address" onClose={() => setAdding(null)} footer={<><Button onClick={() => setAdding(null)}>Cancel</Button><Button variant="primary" disabled={!adding.ip} onClick={async () => { if (await run(() => post(`${base}/firewall-bans`, adding), "Banned")) { setAdding(null); bans.reload(); } }}>Ban</Button></>}>
          <Field label="Address or range"><input autoFocus className="mono font-mono text-ui-sm" value={adding.ip} onChange={(e) => setAdding({ ...adding, ip: e.target.value })} placeholder="203.0.113.7 or 203.0.113.0/24" /></Field>
          <Field label="For"><select value={adding.seconds} onChange={(e) => setAdding({ ...adding, seconds: Number(e.target.value) })}>{[[3600, "1 hour"], [86400, "1 day"], [604800, "7 days"], [2592000, "30 days"], [0, "Until removed"]].map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></Field>
          <Field label="Reason" optional><input value={adding.reason} onChange={(e) => setAdding({ ...adding, reason: e.target.value })} placeholder="Credential stuffing" /></Field>
        </Modal>
      )}
    </>
  );
}

// ── events ──────────────────────────────────────────────────────────────

export function FirewallEvents({ base, onBan }) {
  const [filters, setFilters] = useState({ action: "", source: "", ip: "", path: "" });
  const params = Object.fromEntries(Object.entries(filters).filter(([, v]) => v));
  const events = useApi(`${base}/firewall-events`, { params: { ...params, limit: 200 }, interval: 5000 });
  const set = (key) => (value) => setFilters((current) => ({ ...current, [key]: value }));
  return (
    <>
      <PageHead title="Events" description="Every request the firewall blocked, limited or logged, newest first. Refreshes every few seconds." />
      <div className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5 wrap" style={{ marginBottom: 12 }}>
        <Segmented options={[["", "All"], ["block", "Blocked"], ["rate_limit", "Limited"], ["log", "Logged"]]} value={filters.action} onChange={set("action")} />
        <select value={filters.source} onChange={(e) => set("source")(e.target.value)} style={{ width: 150 }}><option value="">Every source</option><option value="rule">Rules</option><option value="managed">Managed rules</option><option value="ban">Bans</option><option value="rate_limit">Rate limits</option></select>
        <input placeholder="IP address" value={filters.ip} onChange={(e) => set("ip")(e.target.value.trim())} style={{ width: 160 }} />
        <input placeholder="Path contains" value={filters.path} onChange={(e) => set("path")(e.target.value.trim())} style={{ width: 200 }} />
      </div>
      <Card flush>
        <Loading state={events} empty="Nothing recorded yet.">
          {(data) => (
            <Table rows={data.data} empty="Nothing matches." columns={[
              { label: "When", render: (e) => when(e.created_at) },
              { label: "Action", render: (e) => <span><Badge tone={ACTION_TONES[e.action]}>{ACTION_LABELS[e.action] || e.action}</Badge>{e.simulated && <> <Badge tone="lavender">simulated</Badge></>}</span> },
              { label: "Request", render: (e) => <span className="mono font-mono text-ui-sm small text-ui-sm">{e.method} {e.path}</span> },
              { label: "Address", render: (e) => <span className="mono font-mono text-ui-sm">{e.ip}{e.country && <span className="faint text-faint"> · {e.country}</span>}</span> },
              { label: "Because", render: (e) => <span className="small text-ui-sm">{e.source === "ban" ? "banned address" : e.rule_name || e.source}</span> },
              { label: "", render: (e) => (e.source !== "ban" ? <Button size="sm" onClick={() => onBan(e.ip)}>Ban</Button> : null) },
            ]} />
          )}
        </Loading>
      </Card>
    </>
  );
}

// ── tester ──────────────────────────────────────────────────────────────

export function FirewallTester({ base }) {
  const [probe, setProbe] = useState({ ip: "203.0.113.7", method: "GET", path: "/rest/v1/", query: "", user_agent: "", country: "", role: "none", headers: {} });
  const [result, setResult] = useState(null);
  const [run, busy] = useAction();
  const set = (change) => setProbe((current) => ({ ...current, ...change }));
  return (
    <>
      <PageHead title="Test a request" description="Describe a request and see, step by step, what the firewall would do with it. Nothing is sent and nothing is counted." />
      <div className="grid grid-cols-[minmax(0,_1.15fr)_minmax(0,_1fr)] gap-3 items-start max-workbench:grid-cols-1">
        <Section icon="send" title="Request" actions={<Button variant="primary" disabled={busy || !probe.ip} onClick={async () => { const r = await run(() => post(`${base}/firewall-test`, probe)); if (r && r !== true) setResult(r); }}>Test</Button>}>
          <div className="form-grid grid grid-cols-2 gap-[14px_16px] [&_.span]:col-span-full max-tablet:grid-cols-1">
            <Field label="IP address"><input className="mono font-mono text-ui-sm" value={probe.ip} onChange={(e) => set({ ip: e.target.value })} /></Field>
            <Field label="Method"><select value={probe.method} onChange={(e) => set({ method: e.target.value })}>{["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "TRACE"].map((m) => <option key={m}>{m}</option>)}</select></Field>
            <Field label="Path" className="span-2"><input className="mono font-mono text-ui-sm" value={probe.path} onChange={(e) => set({ path: e.target.value })} /></Field>
            <Field label="Query string" optional className="span-2"><input className="mono font-mono text-ui-sm" value={probe.query} onChange={(e) => set({ query: e.target.value })} placeholder="id=1&sort=name" /></Field>
            <Field label="User agent" optional><input value={probe.user_agent} onChange={(e) => set({ user_agent: e.target.value })} placeholder="Mozilla/5.0" /></Field>
            <Field label="Country" optional><input value={probe.country} onChange={(e) => set({ country: e.target.value.toUpperCase() })} placeholder="NG" maxLength={2} /></Field>
            <Field label="Key role"><select value={probe.role} onChange={(e) => set({ role: e.target.value })}><option value="none">No key</option><option value="anon">Publishable key</option><option value="service">Secret key</option></select></Field>
            <Field label="Headers" optional className="span-2"><JsonInput value={probe.headers} rows={3} onChange={(headers) => set({ headers: headers || {} })} /></Field>
          </div>
        </Section>
        <Section icon="bolt" title="What happens">
          {!result ? <p className="m-0 text-ui-sm text-muted">Run a test to see the verdict.</p> : (
            <div className="flex flex-col gap-3">
              <div className={`fw-verdict flex items-center gap-2.5 p-[12px_14px] rounded-sm text-heading [&.block]:bg-danger-soft [&.block]:text-danger [&.allow]:bg-ok-soft [&.allow]:text-ok ${result.outcome}`}><Icon name={result.outcome === "block" ? "x" : "check"} size={18} /><b>{result.outcome === "block" ? "Blocked" : "Allowed through"}</b></div>
              {result.steps.length === 0 ? <p className="m-0 text-ui-sm text-muted">No ban, rule or managed signature applies.</p> : (
                <ol className="fw-steps m-0 pl-5 flex flex-col gap-2 text-ui">{result.steps.map((s, i) => <li key={i}><Badge>{s.step}</Badge> <b>{s.rule}</b> <span className="muted text-muted">— {s.result}</span></li>)}</ol>
              )}
            </div>
          )}
        </Section>
      </div>
    </>
  );
}
