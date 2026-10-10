import { Link } from "@inertiajs/react";
import { Card, Loading, Table, when } from "../../../components/ui";
import { envHref } from "../../../components/Layout";
import { COLOR, RateBadge, SeriesCard, num } from "./system";
import { ms } from "./shared";

export default function Flows({ env, system }) {
  return (
    <Loading state={system}>
      {(s) => {
        const f = s.flows;
        const rate = f.totals.runs ? Math.round(((f.totals.runs - f.totals.failed) / f.totals.runs) * 1000) / 10 : null;
        return (
          <div className="stack lg">
            <SeriesCard title="Runs over time" window={s.window} data={f.series} lines={[["succeeded", "Succeeded", COLOR.ok], ["failed", "Failed", COLOR.bad, "line"]]} empty="No flow runs in this window." />
            <Card flush title="By flow" actions={<Link className="link-more" href={envHref(env, "events")}>All runs</Link>}>
              <Table rows={f.by_flow} empty="No flow runs in this window." columns={[
                { label: "Flow", render: (x) => <b>{x.flow}</b> }, { label: "Runs", key: "runs" },
                { label: "Failed", render: (x) => x.failed ? <span className="error-text">{x.failed}</span> : 0 },
                { label: "Success", render: (x) => <RateBadge value={x.success_rate} /> },
                { label: "Avg", render: (x) => ms(x.avg_ms) }, { label: "p95", render: (x) => x.p95_ms != null ? ms(x.p95_ms) : "—" },
              ]} />
            </Card>
            <Card flush title="Recent failures">
              <Table rows={f.recent_failures} empty="No flow failed in this window." columns={[
                { label: "When", render: (x) => when(x.at) }, { label: "Flow", render: (x) => <b>{x.flow}</b> }, { label: "Trigger", key: "trigger" },
                { label: "Error", render: (x) => <span className="error-text">{x.error.slice(0, 140)}</span> },
              ]} />
            </Card>
          </div>
        );
      }}
    </Loading>
  );
}
