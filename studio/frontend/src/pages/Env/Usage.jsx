import { useEffect, useState } from "react";
import Layout from "../../components/Layout";
import { Badge, Loading, PageHead } from "../../components/ui";
import { envPath, useApi } from "../../lib/api";

const number = (value) => Number.isFinite(Number(value)) ? Number(value) : 0;
const count = (value) => number(value).toLocaleString();
const size = (value) => {
  const n = number(value);
  if (n < 1024) return `${n} B`;
  const units = ["KB", "MB", "GB", "TB"];
  const index = Math.min(Math.floor(Math.log(n) / Math.log(1024)) - 1, units.length - 1);
  const scaled = n / 1024 ** (index + 1);
  return `${scaled >= 100 ? scaled.toFixed(0) : scaled.toFixed(scaled >= 10 ? 1 : 2).replace(/\.?0+$/, "")} ${units[index]}`;
};
const format = (row, value) => row.unit === "bytes" ? size(value) : count(value);
const GROUPS = ["Traffic", "Data", "People", "Automation", "Project"];
const GROUP_NOTE = {
  Traffic: "Requests, bandwidth and realtime across every environment in this project.",
  Data: "What this project stores.",
  People: "Who signs in, and how.",
  Automation: "Work that runs without anyone clicking.",
  Project: "How this project is set up.",
};
const LIVE = new Set(["max_active_connections"]);

/** `ok`, `warn` (70%+) or `danger` (90%+) for a measured limit; `none` when the project has no limit to measure against. */
const tone = (row) => row.percent == null ? "none" : row.percent >= 90 ? "danger" : row.percent >= 70 ? "warn" : "ok";
const FILL = { ok: "bg-brand", warn: "bg-warn", danger: "bg-danger", none: "bg-brand" };
const TEXT = { ok: "text-muted", warn: "text-warn", danger: "text-danger", none: "text-faint" };
const ICONS = { Traffic: "networking", Data: "database", People: "team", Automation: "flows", Project: "settings" };

function when(row) {
  if (LIVE.has(row.limit)) return "Right now";
  if (!row.period) return "Total";
  const [year, month] = row.period.split("-").map(Number);
  const next = new Date(Date.UTC(year, month, 1));
  return `Resets ${next.toLocaleDateString(undefined, { month: "short", day: "numeric", timeZone: "UTC" })}`;
}

const status = (row) => {
  if (row.used == null) return row.allowed ? `${format(row, row.allowed)} each` : "No limit";
  if (!row.allowed) return "No limit";
  const state = tone(row);
  return state === "danger" && row.percent >= 100 ? "Limit reached" : `${Math.round(row.percent)}% used`;
};

function Bar({ row, height = "h-1.5" }) {
  const limited = row.allowed > 0 && row.used != null;
  const fill = limited ? Math.min(100, Math.max(row.used ? 1.5 : 0, row.percent ?? 0)) : 0;
  return (
    <div className={`${height} w-full overflow-hidden rounded-full bg-sunken`} role="progressbar" aria-label={`${row.label} used`} aria-valuenow={limited ? Math.round(row.percent ?? 0) : undefined}>
      {limited && <div className={`h-full rounded-full transition-[width] duration-300 ${FILL[tone(row)]}`} style={{ width: `${fill}%` }} />}
    </div>
  );
}

/** One resource per line: what it is, how much is used, the limit, and a bar. */
function Row({ row, onOpen }) {
  const measured = row.used != null;
  return (
    <button type="button" onClick={() => onOpen(row.limit)} className="grid w-full cursor-pointer grid-cols-[minmax(160px,1.2fr)_minmax(120px,1fr)_minmax(140px,1.4fr)_minmax(90px,.7fr)] items-center gap-4 border-0 bg-transparent p-[13px_4px] text-left font-inherit text-text [border-bottom:1px_solid_var(--line)] hover:bg-hover max-wide-tablet:grid-cols-[1fr_auto]">
      <span className="text-ui font-medium">{row.label}</span>
      <span className="text-ui tabular-nums">{measured ? format(row, row.used) : "-"}<span className="text-muted">{row.allowed ? ` / ${format(row, row.allowed)}` : ""}</span></span>
      <span className="max-wide-tablet:hidden">{measured && row.allowed ? <Bar row={row} /> : null}</span>
      <span className={`text-right text-ui-sm ${TEXT[tone(row)]}`}>{status(row)}</span>
    </button>
  );
}

function Overview({ rows, onOpen }) {
  const groups = GROUPS.filter((name) => rows.some((row) => row.group === name));
  return (
    <div className="grid gap-8">
      {groups.map((name) => (
        <section key={name}>
          <div className="mb-1 flex items-baseline justify-between gap-4 [border-bottom:1px_solid_var(--line-2)] pb-2">
            <h3 className="m-0 text-heading">{name}</h3>
            <span className="text-ui-sm text-muted max-wide-tablet:hidden">{GROUP_NOTE[name]}</span>
          </div>
          {rows.filter((row) => row.group === name).map((row) => <Row key={row.limit} row={row} onOpen={onOpen} />)}
        </section>
      ))}
    </div>
  );
}

/** A single resource, in full. */
function Detail({ row }) {
  const measured = row.used != null;
  const limited = row.allowed > 0;
  const left = measured && limited ? Math.max(0, row.allowed - row.used) : null;
  const facts = [
    ["Used", measured ? format(row, row.used) : "Not counted: this limit applies to each item"],
    ["Plan limit", limited ? format(row, row.allowed) : "No limit"],
    ...(left != null ? [["Remaining", format(row, left)]] : []),
    ["Period", when(row)],
  ];
  return (
    <div className="grid max-w-[760px] gap-6">
      <div>
        <div className="flex items-baseline gap-3">
          <b className="text-[34px] font-bold-plus leading-none tracking-[-0.04em] tabular-nums">{measured ? format(row, row.used) : limited ? format(row, row.allowed) : "No limit"}</b>
          {measured && limited && <span className="text-title text-muted">of {format(row, row.allowed)}</span>}
        </div>
        <div className="mt-4"><Bar row={row} height="h-2" /></div>
        <p className={`m-[8px_0_0] text-ui-sm ${TEXT[tone(row)]}`}>{status(row)}</p>
      </div>
      <dl className="m-0 [border-top:1px_solid_var(--line)]">
        {facts.map(([label, value]) => (
          <div key={label} className="flex justify-between gap-6 p-[12px_0] [border-bottom:1px_solid_var(--line)]">
            <dt className="text-ui text-muted">{label}</dt><dd className="m-0 text-right text-ui font-medium tabular-nums">{value}</dd>
          </div>
        ))}
      </dl>
      {!limited && <p className="m-0 text-ui text-muted">This project has no plan limit for {row.label.toLowerCase()}. It is counted so you can see how it is used, and never capped.</p>}
    </div>
  );
}

export default function Usage({ env }) {
  const plan = useApi(env ? envPath(env, "/plan-usage") : null, { interval: 15000 });
  const rows = plan.data?.data || [];
  const fromHash = () => { try { return window.location.hash.replace("#", "") || "overview"; } catch { return "overview"; } };
  const [section, setSection] = useState(fromHash);
  const open = (key) => { setSection(key); try { window.history.replaceState(null, "", `#${key}`); } catch { /* the page still works */ } };
  useEffect(() => { window.scrollTo?.(0, 0); }, [section]);
  const limited = rows.some((row) => row.allowed > 0);
  const near = rows.filter((row) => row.percent != null && row.percent >= 90);
  const current = rows.find((row) => row.limit === section);

  const subnav = {
    title: "Usage",
    groups: [
      { items: [{ key: "overview", label: "Overview", icon: "overview" }] },
      ...GROUPS.filter((name) => rows.some((row) => row.group === name)).map((name) => ({
        title: name,
        items: rows.filter((row) => row.group === name).map((row) => ({ key: row.limit, label: row.label, icon: ICONS[name], flag: row.percent >= 100 ? "critical" : row.percent >= 90 ? "warning" : undefined })),
      })),
    ],
    active: current ? section : "overview",
    onSelect: open,
  };

  return (
    <Layout title="Usage" subnav={subnav}>
      <PageHead
        title={current ? current.label : "Usage"}
        description={current ? `${current.group}. ${when(current)}.` : "What this project has used, against what its plan allows."}
        actions={<><Badge tone={limited ? "brand" : ""}>{limited ? "Plan limits on" : "No plan limits"}</Badge><button type="button" className="btn sm" onClick={plan.reload}>Refresh</button></>}
      />
      <Loading state={{ loading: plan.loading, error: plan.error, data: true }}>{() => (
        <div className="grid gap-6">
          {near.length > 0 && !current && (
            <div className="rounded-sm p-[12px_16px] [border:1px_solid_var(--danger)] bg-danger-soft text-ui" role="alert">
              <b>{near.some((row) => row.percent >= 100) ? "A limit has been reached. " : "Close to a limit. "}</b>
              <span className="text-muted">{near.map((row) => row.label).join(", ")}. Move to a larger plan to keep going.</span>
            </div>
          )}
          {!limited && !current && <p className="m-0 text-ui text-muted">This project was created without a plan, so nothing here is capped. Usage is still counted so you can see how it is used.</p>}
          {current ? <Detail row={current} /> : <Overview rows={rows} onOpen={open} />}
        </div>
      )}</Loading>
    </Layout>
  );
}
