import { Badge, Card, Loading } from "../../../components/ui";
import { useApi } from "../../../lib/api";
import { AUTH, useAuthConfig } from "../auth/shared";

function Row({ title, value, tone, onOpen, to }) {
  return (
    <button type="button" className="attr-row overview-row" onClick={() => onOpen(to)}>
      <span className="attr-name">{title}</span>
      <span className="row" style={{ gap: 8 }}>{value}{tone && <Badge tone={tone}>{tone === "green" ? "on" : "off"}</Badge>}</span>
    </button>
  );
}

/** What this environment's directory is set up to do, with a way into each part. */
export default function Overview({ env, base, onOpen }) {
  const config = useAuthConfig(env);
  const people = useApi(`${base}/users?limit=1`, AUTH);
  const v = config.value;
  const dir = v.directory || {};
  const form = v.signup_form || {};
  const asked = Object.keys(form.attributes || {}).length + (form.custom || []).filter((c) => c.ask !== false).length;
  const enterprise = Object.values(v.providers || {}).filter((p) => p && (p.type === "oidc" || p.type === "saml")).length;
  const rules = Object.values(v.password_rules || {}).filter(Boolean).length;
  return (
    <Loading state={config.state}>
      {() => (
        <div className="stack lg">
          <div className="stat-cards">
            <Card><span className="eyebrow">People</span><div className="stat-number">{people.data?.total ?? "—"}</div></Card>
            <Card><span className="eyebrow">Sign-in methods</span><div className="stat-number">{(form.sign_in_with || ["email"]).length + Object.keys(v.providers || {}).length}</div></Card>
            <Card><span className="eyebrow">Company connections</span><div className="stat-number">{enterprise}</div></Card>
          </div>
          <Card title="Configuration">
            <div className="attr-list">
              <Row title="Sign in with" value={(form.sign_in_with || ["email"]).join(", ")} onOpen={onOpen} to="signin" />
              <Row title="Self-registration" tone={v.signup_enabled ? "green" : "yellow"} value="" onOpen={onOpen} to="signin" />
              <Row title="Sign-up asks for" value={`${asked} detail${asked === 1 ? "" : "s"} besides email and password`} onOpen={onOpen} to="attributes" />
              <Row title="Password rules" value={`${v.password_min_length}+ characters${rules ? `, ${rules} character rule${rules === 1 ? "" : "s"}` : ""}${dir.passwords?.history ? `, remembers ${dir.passwords.history}` : ""}${dir.passwords?.max_age_days ? `, expires in ${dir.passwords.max_age_days} days` : ""}`} onOpen={onOpen} to="passwords" />
              <Row title="Password recovery" tone={dir.recovery?.enabled === false ? "yellow" : "green"} value="" onOpen={onOpen} to="recovery" />
              <Row title="Device tracking" tone={dir.devices?.track === false ? "yellow" : "green"} value={dir.devices?.remember_days ? `remembers a device for ${dir.devices.remember_days} days` : ""} onOpen={onOpen} to="devices" />
              <Row title="User API keys" tone={dir.api_keys?.enabled ? "green" : "yellow"} value="" onOpen={onOpen} to="apikeys" />
            </div>
          </Card>
        </div>
      )}
    </Loading>
  );
}
