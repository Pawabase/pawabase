import { router } from "@inertiajs/react";
import { useState } from "react";
import Layout from "../../components/Layout";
import { Badge, Button, Card, Field, JsonInput, Loading, PageHead, useAction } from "../../components/ui";
import { del, envPath, patch, useApi } from "../../lib/api";

const INFRA_EXAMPLE = {
  database_url: "secret://DATABASE_URL",
  storage: { driver: "s3", bucket: "my-app", region: "eu-west-1", endpoint: "https://s3.eu-west-1.amazonaws.com", access_key: "secret://S3_KEY", secret_key: "secret://S3_SECRET" },
  mail: { host: "smtp.example.com", port: 587, username: "apikey", password: "secret://SMTP_PASSWORD", from_email: "no-reply@example.com", use_tls: true },
};

export default function Settings({ project, env }) {
  const path = envPath(project.ref, env);
  const state = useApi(path);
  const [draft, setDraft] = useState({});
  const [run, busy] = useAction();
  const save = async (section) => {
    if (await run(() => patch(path, { [section]: draft[section] }), "Saved")) { setDraft({ ...draft, [section]: undefined }); state.reload(); }
  };
  return (
    <Layout title="Settings">
      <PageHead title="Settings" description="Bring your own infrastructure per environment. Reference secrets as secret://NAME rather than pasting credentials here." />
      <Loading state={state}>
        {(data) => (
          <div className="stack lg">
            <Card title="Infrastructure" actions={<Button variant="primary" size="sm" disabled={busy || draft.infra === undefined} onClick={() => save("infra")}>Save</Button>}>
              <Field label="database_url, storage, mail" hint={<>Leave a section out to use the platform default. Example: <code>{JSON.stringify(INFRA_EXAMPLE).slice(0, 140)}…</code></>}>
                <JsonInput value={draft.infra ?? data.infra ?? {}} onChange={(v) => setDraft({ ...draft, infra: v })} rows={14} />
              </Field>
            </Card>
            <Card title="Environment settings" actions={<Button variant="primary" size="sm" disabled={busy || draft.settings === undefined} onClick={() => save("settings")}>Save</Button>}>
              <Field label="Settings" hint='public_docs (publish API docs at /docs/v1), cors_origins (for publishable keys), realtime.channels (channel rules), and anything your flows read as $settings.'>
                <JsonInput value={draft.settings ?? data.settings ?? {}} onChange={(v) => setDraft({ ...draft, settings: v })} rows={12} />
              </Field>
            </Card>
            <Card title="Environment">
              <div className="spread">
                <div className="row">{data.is_default ? <Badge tone="green">default environment</Badge> : <Button onClick={async () => { if (await run(() => patch(path, { is_default: true }), "Now the default")) state.reload(); }}>Make default</Button>}<span className="muted">Definitions version {data.version}</span></div>
                {!data.is_default && <Button variant="danger" onClick={async () => { if (prompt(`Type ${env} to delete this environment`) === env && await run(() => del(path), "Environment deleted")) router.visit(`/projects/${project.ref}`); }}>Delete environment</Button>}
              </div>
            </Card>
          </div>
        )}
      </Loading>
    </Layout>
  );
}
