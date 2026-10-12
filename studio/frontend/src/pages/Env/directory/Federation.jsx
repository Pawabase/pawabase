import { usePage } from "@inertiajs/react";
import { Badge, Card, Field, Loading, Segmented, Switch, TagInput } from "../../../components/ui";
import { SettingsPanel, useAuthConfig } from "../auth/shared";

const isEnterprise = (conf) => conf && (conf.type === "oidc" || conf.type === "saml");

function slug(text, taken) {
  const base = text.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 30) || "company";
  let name = base;
  for (let n = 2; taken.has(name); n += 1) name = `${base}-${n}`;
  return name;
}

function Copy({ label, value }) {
  return (
    <Field label={label}>
      <input readOnly value={value} onFocus={(e) => e.target.select()} style={{ fontFamily: "var(--mono)", fontSize: 13 }} />
    </Field>
  );
}

function Connection({ env, base, name, conf, change, remove }) {
  const saml = conf.type === "saml";
  const map = conf.attribute_map || {};
  const setMap = (next) => change({ attribute_map: next });
  const entries = Object.entries(map);
  return (
    <Card
      title={<span className="row" style={{ gap: 10 }}><b>{name}</b><Badge>{saml ? "SAML 2.0" : "OpenID Connect"}</Badge>{conf.enabled === false && <Badge tone="yellow">disabled</Badge>}</span>}
      actions={<button type="button" className="btn sm" onClick={remove}>Remove</button>}
    >
      <div className="stack">
        <Switch checked={conf.enabled !== false} onChange={(v) => change({ enabled: v })} label="Enabled" />
        <Field label="Company email domains" hint="People who type an email at these domains are sent straight here, and addresses outside them are refused.">
          <TagInput value={conf.domains || []} onChange={(v) => change({ domains: v })} placeholder="acme.com" />
        </Field>
        {saml ? (
          <>
            <Field label="Identity provider entity ID"><input value={conf.idp_entity_id || ""} onChange={(e) => change({ idp_entity_id: e.target.value })} placeholder="https://idp.acme.com/metadata" /></Field>
            <Field label="Single sign-on URL"><input value={conf.idp_sso_url || ""} onChange={(e) => change({ idp_sso_url: e.target.value })} placeholder="https://idp.acme.com/sso" /></Field>
            <Field label="Signing certificate" hint="The identity provider's public certificate (PEM). Responses not signed by it are refused.">
              <textarea rows={5} value={conf.idp_cert || ""} onChange={(e) => change({ idp_cert: e.target.value })} placeholder={"-----BEGIN CERTIFICATE-----\n…\n-----END CERTIFICATE-----"} style={{ fontFamily: "var(--mono)", fontSize: 12 }} />
            </Field>
            <Switch checked={conf.email_verified !== false} onChange={(v) => change({ email_verified: v })} label="Trust the email address" hint="Treat the address the identity provider asserts as verified." />
            <Field label="Attribute mapping" hint="Which attribute in the assertion fills each field. name and username set those columns; anything else goes into the person's profile under that key.">
              <div className="stack">
                {entries.map(([target, source], i) => (
                  <div key={i} className="row">
                    <input value={target} onChange={(e) => { const next = {}; entries.forEach(([k, v], j) => { next[j === i ? e.target.value : k] = v; }); setMap(next); }} placeholder="Field (email, name, department…)" />
                    <input value={source} onChange={(e) => setMap({ ...map, [target]: e.target.value })} placeholder="Attribute in the assertion" />
                    <button type="button" className="btn sm" onClick={() => { const next = { ...map }; delete next[target]; setMap(next); }}>Remove</button>
                  </div>
                ))}
                <button type="button" className="btn sm" style={{ alignSelf: "flex-start" }} onClick={() => setMap({ ...map, [`field${entries.length + 1}`]: "" })}>Add a mapping</button>
              </div>
            </Field>
            <div className="fact-list">
              <Copy label="Entity ID (give this to the identity provider)" value={`${base}/auth/v1/saml/${env}/${name}`} />
              <Copy label="Assertion consumer service URL" value={`${base}/auth/v1/saml/${env}/${name}/acs`} />
              <Copy label="Metadata" value={`${base}/auth/v1/saml/${env}/${name}/metadata`} />
            </div>
          </>
        ) : (
          <>
            <Field label="Issuer" hint="Endpoints are discovered from <issuer>/.well-known/openid-configuration."><input value={conf.issuer || ""} onChange={(e) => change({ issuer: e.target.value })} placeholder="https://login.acme.com" /></Field>
            <div className="grid two">
              <Field label="Client ID"><input value={conf.client_id || ""} onChange={(e) => change({ client_id: e.target.value })} /></Field>
              <Field label="Client secret" hint="Use secret://NAME to read it from Secrets."><input value={conf.client_secret || ""} onChange={(e) => change({ client_secret: e.target.value })} placeholder="secret://ACME_OIDC_SECRET" /></Field>
            </div>
            <Copy label="Redirect URI (register this with the provider)" value={`${base}/auth/v1/callback/${env}/${name}`} />
          </>
        )}
      </div>
    </Card>
  );
}

/** Company sign-in: OpenID Connect and SAML 2.0 connections. */
export default function Federation({ env }) {
  const config = useAuthConfig(env);
  const { gateway_url: gatewayUrl } = usePage().props;
  const base = (gatewayUrl || "").replace(/\/$/, "");
  const providers = config.value.providers || {};
  const names = Object.keys(providers).filter((n) => isEnterprise(providers[n]));
  const setProvider = (name, values) => config.set({ providers: { ...providers, [name]: { ...providers[name], ...values } } });
  const add = (type) => {
    const name = slug(type === "saml" ? "company-saml" : "company-oidc", new Set(Object.keys(providers)));
    config.set({ providers: { ...providers, [name]: { type, enabled: true, domains: [] } } });
  };
  const remove = (name) => { const next = { ...providers }; delete next[name]; config.set({ providers: next }); };
  return (
    <Loading state={config.state}>
      {() => (
        <div className="stack lg">
          <SettingsPanel title="Company sign-in" description="Let a company's people sign in with their own identity provider (Okta, Entra ID, Google Workspace, ADFS, Keycloak…). Social providers like Google and GitHub are under Users & auth." config={config}>
            <div className="row" style={{ gap: 10 }}>
              <button type="button" className="btn" onClick={() => add("oidc")}>Add OpenID Connect</button>
              <button type="button" className="btn" onClick={() => add("saml")}>Add SAML 2.0</button>
            </div>
            {names.length === 0 && <p className="hint" style={{ margin: 0 }}>No company connections yet.</p>}
          </SettingsPanel>
          {names.map((name) => (
            <Connection key={name} env={env} base={base} name={name} conf={providers[name]} change={(values) => setProvider(name, values)} remove={() => remove(name)} />
          ))}
        </div>
      )}
    </Loading>
  );
}
