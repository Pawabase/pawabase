// Mail at a glance: how much went out, what failed and why, the templates in
// use, and whether delivery is set up.

import { useState } from "react";
import { Icon } from "../../../components/icons";
import { Button, Card, Loading, Segmented, when } from "../../../components/ui";
import { envPath, useApi } from "../../../lib/api";
import { MailStatus } from "./common";

const RANGES = [["7", "7 days"], ["30", "30 days"], ["90", "90 days"]];

function Bars({ series }) {
  const peak = Math.max(1, ...series.map((d) => d.sent + d.failed + d.suppressed));
  return (
    <div className="mail-bars" role="img" aria-label="Messages per day">
      {series.map((d) => {
        const total = d.sent + d.failed + d.suppressed;
        return (
          <div key={d.day} className="mail-bar" title={`${d.day}: ${d.sent} sent, ${d.suppressed} suppressed, ${d.failed} failed`}>
            <div className="mail-bar-stack" style={{ height: `${(total / peak) * 100}%` }}>
              {d.failed > 0 && <i className="failed" style={{ flex: d.failed }} />}
              {d.suppressed > 0 && <i className="suppressed" style={{ flex: d.suppressed }} />}
              {d.sent > 0 && <i className="sent" style={{ flex: d.sent }} />}
            </div>
          </div>
        );
      })}
    </div>
  );
}

export function Overview({ env, setup, onGo, onOpenTemplate, onOpenMessage }) {
  const base = envPath(env);
  const [days, setDays] = useState("30");
  const stats = useApi(`${base}/mail/stats`, { params: { days }, interval: 20000 });
  const recent = useApi(`${base}/mail/log`, { params: { limit: 6 }, interval: 15000 });
  const templates = useApi(`${base}/mail-templates`);
  const configured = Boolean(setup?.configured) && !setup?.suppressed;
  const host = (setup?.settings || []).find((r) => r.setting === "host")?.value;

  return (
    <div className="stack lg">
      <div className="mail-quick">
        <button type="button" className="mail-quick-item" onClick={() => onGo("templates", { create: true })}><Icon name="plus" size={18} /><span><b>New template</b><small>Start from a prebuilt design</small></span></button>
        <button type="button" className="mail-quick-item" onClick={() => onGo("compose")}><Icon name="send" size={18} /><span><b>Compose an email</b><small>Send one now or schedule it</small></span></button>
        <button type="button" className={`mail-quick-item ${configured ? "" : "warn"}`} onClick={() => onGo("delivery")}>
          <Icon name="settings" size={18} />
          <span><b>{configured ? "Delivery is on" : setup?.configured ? "Delivery is paused" : "Delivery is not set up"}</b><small>{configured ? `Through ${host}` : "Mail is recorded, not delivered"}</small></span>
        </button>
      </div>

      <Card icon="pulse" title="Sending" actions={<Segmented value={days} onChange={setDays} options={RANGES} />}>
        <Loading state={stats}>
          {(s) => (
            <div className="stack">
              <div className="mail-kpis">
                <div><b>{s.total.toLocaleString()}</b><span>Messages</span></div>
                <div><b>{(s.by_status.sent || 0).toLocaleString()}</b><span>Delivered to the server</span></div>
                <div><b className={s.by_status.failed ? "bad" : ""}>{(s.by_status.failed || 0).toLocaleString()}</b><span>Failed</span></div>
                <div><b>{(s.by_status.suppressed || 0).toLocaleString()}</b><span>Suppressed</span></div>
                <div><b>{s.scheduled.toLocaleString()}</b><span>Scheduled</span></div>
                <div><b>{s.total ? `${Math.round(((s.by_status.sent || 0) / Math.max(1, (s.by_status.sent || 0) + (s.by_status.failed || 0))) * 100)}%` : "—"}</b><span>Success rate</span></div>
              </div>
              <Bars series={s.series} />
              <div className="mail-legend"><span><i className="sent" />Sent</span><span><i className="suppressed" />Suppressed</span><span><i className="failed" />Failed</span></div>
            </div>
          )}
        </Loading>
      </Card>

      <div className="grid two">
        <Card icon="mail" flush title="Recent activity" actions={<Button size="sm" variant="ghost" onClick={() => onGo("activity")}>All activity</Button>}>
          <Loading state={recent} empty="Nothing sent yet.">
            {(data) => (
              <div className="mail-list">
                {data.data.map((m) => (
                  <button key={m.id} type="button" className="mail-list-row" onClick={() => onOpenMessage(m.id)}>
                    <span className="grow"><b>{m.subject || "(no subject)"}</b><small>{(m.to || []).join(", ")}{m.template ? ` · ${m.template}` : ""}</small></span>
                    <MailStatus value={m.status} />
                    <span className="faint">{when(m.created_at)}</span>
                  </button>
                ))}
              </div>
            )}
          </Loading>
        </Card>
        <Card icon="status" flush title="Recent sending errors">
          <Loading state={stats}>
            {(s) => s.errors.length === 0 ? <div className="empty">No failures in this period.</div> : (
              <div className="mail-list">
                {s.errors.map((e) => (
                  <button key={e.id} type="button" className="mail-list-row" onClick={() => onOpenMessage(e.id)}>
                    <span className="grow"><b>{e.subject || "(no subject)"}</b><small className="text-danger">{e.error}</small></span>
                    <span className="faint">{when(e.created_at)}</span>
                  </button>
                ))}
              </div>
            )}
          </Loading>
        </Card>
      </div>

      <div className="grid two">
        <Card icon="mail-templates" flush title="Most sent templates">
          <Loading state={stats}>
            {(s) => s.top_templates.length === 0 ? <div className="empty">No template has been sent in this period.</div> : (
              <div className="mail-list">
                {s.top_templates.map((t) => (
                  <button key={t.template} type="button" className="mail-list-row" onClick={() => onOpenTemplate(t.template)}>
                    <span className="grow mono">{t.template}</span>
                    <span className="mail-meter"><i style={{ width: `${(t.count / s.top_templates[0].count) * 100}%` }} /></span>
                    <b>{t.count}</b>
                  </button>
                ))}
              </div>
            )}
          </Loading>
        </Card>
        <Card icon="clock" flush title="Recently edited templates" actions={<Button size="sm" variant="ghost" onClick={() => onGo("templates")}>All templates</Button>}>
          <Loading state={templates} empty="No templates yet.">
            {(data) => (
              <div className="mail-list">
                {[...data.data].sort((a, b) => String(b.updated_at).localeCompare(String(a.updated_at))).slice(0, 6).map((t) => (
                  <button key={t.name} type="button" className="mail-list-row" onClick={() => onOpenTemplate(t.name)}>
                    <span className="grow"><b className="mono">{t.name}</b><small>{t.draft?.subject || t.subject || "No subject"}</small></span>
                    <span className="faint">{when(t.updated_at)}</span>
                  </button>
                ))}
              </div>
            )}
          </Loading>
        </Card>
      </div>
    </div>
  );
}
