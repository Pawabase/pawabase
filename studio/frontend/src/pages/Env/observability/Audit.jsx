import { Link } from "@inertiajs/react";
import { useState } from "react";
import { Card, Loading, Table, when } from "../../../components/ui";
import { useApi } from "../../../lib/api";

export default function AuditLog({ env }) {
  const [scope, setScope] = useState("env");
  const audit = useApi("/audit", { params: { limit: 100, env: scope === "env" ? env : undefined }, interval: 20000 });
  return (
    <Card icon="audit" flush title="Who changed what" actions={<div className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5">
      <select value={scope} onChange={(e) => setScope(e.target.value)} style={{ width: 190 }}><option value="env">this environment</option><option value="all">whole installation</option></select>
      <Link className="link-more inline-flex items-center gap-0.5 text-ui-sm font-semibold text-muted [&:hover]:text-text" href="/audit">Full audit log</Link>
    </div>}>
      <Loading state={audit} empty="No changes recorded yet.">
        {(d) => <Table rows={d.data || []} columns={[
          { label: "When", render: (r) => <span title={r.created_at}>{when(r.created_at)}</span> }, { label: "Actor", render: (r) => r.actor || <span className="faint text-faint">—</span> },
          { label: "Action", render: (r) => <code>{r.action}</code> }, { label: "Environment", render: (r) => r.env || "—" }, { label: "Target", key: "target" },
        ]} />}
      </Loading>
    </Card>
  );
}
