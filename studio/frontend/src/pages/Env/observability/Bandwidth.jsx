import Chart, { compact } from "../../../components/Chart";
import { Card, Loading, Table } from "../../../components/ui";
import { envPath, useApi } from "../../../lib/api";
import { byteLabel } from "./shared";

const COLORS = { out: "var(--chart-mint)", in: "var(--info)" };

function Stat({ label, value, sub }) {
  return (
    <div className="flex min-w-0 flex-col gap-1 p-[16px_18px] [border-right:1px_solid_var(--line)] last:border-r-0">
      <span className="text-ui-sm text-muted">{label}</span>
      <b className="text-display-sm font-bold-plus tracking-[-0.03em]">{value}</b>
      {sub && <span className="text-ui-sm text-faint">{sub}</span>}
    </div>
  );
}

/** A bar scaled to the heaviest row, sent in the brand colour and received behind it. */
function Share({ row, max }) {
  const total = row.bytes_out + row.bytes_in;
  return (
    <div className="flex min-w-[160px] items-center gap-2">
      <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-sunken"><div className="h-full rounded-full bg-brand" style={{ width: `${(total / max) * 100}%` }} /></div>
      <b className="w-[64px] text-right">{byteLabel(total)}</b>
    </div>
  );
}

/** Bytes in and out of this environment, and what moved the most of them. */
export default function Bandwidth({ env, minutes, onTrace }) {
  const state = useApi(`${envPath(env)}/observability/bandwidth`, { params: { minutes }, interval: 15000 });
  return (
    <Loading state={state}>
      {(data) => {
        const s = data.summary;
        const max = (rows) => Math.max(1, ...rows.map((r) => r.bytes_out + r.bytes_in));
        return (
          <div className="stack flex flex-col gap-3 lg">
            <Card flush>
              <div className="grid grid-cols-[repeat(auto-fit,_minmax(170px,_1fr))]">
                <Stat label="Moved" value={byteLabel(s.total)} sub="in both directions" />
                <Stat label="Sent to clients" value={byteLabel(s.bytes_out)} sub={`${compact(s.responses)} responses`} />
                <Stat label="Received" value={byteLabel(s.bytes_in)} sub="request bodies" />
                <Stat label="Average response" value={byteLabel(s.average_response)} />
              </div>
            </Card>

            <Card icon="usage" title="Over time" description="Headers are not counted; only what the request and response carried.">
              <div style={{ height: 260 }}>
                <Chart height="fill" stepSeconds={data.step_seconds} data={data.series} format={byteLabel}
                  series={[{ key: "bytes_out", label: "Sent", color: COLORS.out, type: "area" }, { key: "bytes_in", label: "Received", color: COLORS.in, type: "line" }]}
                  empty="No bandwidth recorded in this window." />
              </div>
            </Card>

            <Card bleed icon="routes" flush title="Endpoints">
              <Table rows={data.endpoints} empty="No traffic in this window."
                columns={[
                  { label: "Endpoint", render: (r) => <code>{r.endpoint}</code> },
                  { label: "Requests", render: (r) => r.requests.toLocaleString() },
                  { label: "Sent", render: (r) => byteLabel(r.bytes_out) },
                  { label: "Received", render: (r) => byteLabel(r.bytes_in) },
                  { label: "Per request", render: (r) => byteLabel(Math.round((r.bytes_out + r.bytes_in) / Math.max(1, r.requests))) },
                  { label: "Share", render: (r) => <Share row={r} max={max(data.endpoints)} /> },
                ]} />
            </Card>

            <div className="grid grid-cols-2 gap-3 max-layout:grid-cols-1">
              <Card bleed icon="users" flush title="Callers">
                <Table rows={data.callers} empty="No signed-in callers moved data."
                  columns={[
                    { label: "Caller", render: (r) => <span className="truncate" title={r.caller}>{r.caller}</span> },
                    { label: "Requests", render: (r) => r.requests.toLocaleString() },
                    { label: "Total", render: (r) => <Share row={r} max={max(data.callers)} /> },
                  ]} />
              </Card>
              <Card bleed icon="usage" flush title="Largest responses">
                <Table rows={data.largest} empty="Nothing yet." onRowClick={(r) => onTrace(r.request_id)}
                  columns={[
                    { label: "Request", render: (r) => <span><b>{r.method}</b> <code>{r.path}</code></span> },
                    { label: "Status", render: (r) => r.status },
                    { label: "Sent", render: (r) => <b>{byteLabel(r.bytes_out)}</b> },
                  ]} />
              </Card>
            </div>
          </div>
        );
      }}
    </Loading>
  );
}
