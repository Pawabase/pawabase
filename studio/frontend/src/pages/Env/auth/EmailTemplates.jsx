import { Badge, Loading, Table } from "../../../components/ui";
import { envPath, useApi } from "../../../lib/api";
import { envHref } from "../../../components/Layout";
import { SettingsPanel, useAuthConfig } from "./shared";

// Every message the sign-in system sends, in the order a person meets them.
const MESSAGES = [
  ["verify", "Confirm your email", "Right after sign-up, when email confirmation is on."],
  ["magic", "Sign-in link", "When someone asks for a link instead of typing a password."],
  ["recovery", "Reset your password", "When someone says they forgot their password."],
  ["password_changed", "Password changed", "After a password is changed, as a safety notice."],
  ["mfa_code", "Verification code", "The code for a two-step sign-in sent by email."],
  ["new_device", "New sign-in", "When an account is signed in to from a device it has not used."],
  ["email_change", "Confirm a new address", "When someone changes the email on their account."],
  ["invite", "Invitation", "When a person is invited to an organization."],
];

/** Which message uses Pawabase's own wording, and which uses one of the project's mail templates. */
export default function EmailTemplates({ env }) {
  const config = useAuthConfig(env);
  const templates = useApi(`${envPath(env)}/mail-templates`);
  const published = (templates.data?.data || []).filter((t) => (t.status || "published") !== "draft" || t.html || t.text);
  const emails = config.value.emails || {};
  const chosen = (kind) => emails[kind]?.template || "";
  const choose = (kind, name) => {
    const { template: _drop, ...rest } = emails[kind] || {};
    config.set({ emails: { ...emails, [kind]: name ? { ...rest, template: name } : rest } });
  };

  return (
    <Loading state={config.state}>
      {() => (
        <SettingsPanel icon="mail" title="Messages" description="Pick Pawabase's default wording for each message, or one of your own mail templates. A template can read {{ link }}, {{ email }}, {{ project }} and {{ brand.logo_url }}, {{ brand.primary }}, {{ brand.name }}." config={config}>
          <Table
            bleed
            rows={MESSAGES}
            columns={[
              { label: "Message", render: ([, label]) => <b>{label}</b> },
              { label: "Sent when", render: ([, , when]) => <span className="muted text-muted">{when}</span> },
              {
                label: "Uses",
                render: ([kind]) => {
                  const name = chosen(kind);
                  const missing = name && !published.some((t) => t.name === name);
                  return (
                    <span className="flex items-center gap-2">
                      <select value={name} onChange={(e) => choose(kind, e.target.value)} aria-label={`Template for ${kind}`} style={{ minWidth: 190 }}>
                        <option value="">Default</option>
                        {published.map((t) => <option key={t.name} value={t.name}>{t.name}</option>)}
                        {missing && <option value={name}>{name} (not found)</option>}
                      </select>
                      {missing && <Badge tone="red">falls back to default</Badge>}
                    </span>
                  );
                },
              },
            ]}
          />
          <p className="hint text-xs font-book text-muted leading-[1.45]" style={{ margin: 0 }}>
            {published.length ? <>Templates come from <a href={envHref(env, "mail")}>Mail</a>. A draft that has never been published cannot be chosen.</> : <>You have no published mail templates yet. Make one in <a href={envHref(env, "mail")}>Mail</a> and it will appear here.</>}
          </p>
        </SettingsPanel>
      )}
    </Loading>
  );
}
