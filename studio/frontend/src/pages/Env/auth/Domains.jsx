import { Field, Loading, TagInput } from "../../../components/ui";
import { SettingsPanel, useAuthConfig } from "./shared";

/** Where a browser may be sent after signing in. Anything else is refused, so tokens cannot be redirected to a stranger. */
export default function Domains({ env }) {
  const config = useAuthConfig(env);
  const { value, set } = config;
  return (
    <Loading state={config.state}>
      {() => (
        <SettingsPanel title="Redirects" description="Magic links and social sign-in send people back to your application. Only these addresses may receive them." config={config}>
          <Field label="Site URL" hint="Your application's address. It is always allowed, and is used in emails."><input value={value.site_url} onChange={(e) => set({ site_url: e.target.value })} placeholder="https://app.example.com" /></Field>
          <Field label="Additional redirect URLs" hint="Other addresses that may receive a sign-in, such as a staging site or a mobile app's link. A prefix covers everything beneath it."><TagInput value={value.redirect_urls || []} onChange={(v) => set({ redirect_urls: v })} placeholder="https://staging.example.com/auth/callback" /></Field>
        </SettingsPanel>
      )}
    </Loading>
  );
}
