// Plain values flows and policies read as `$settings.<name>`: a store name, a support phone number, a feature switch.
// (Anything secret belongs in Secrets, next to this.)

import { Card, KeyValue, Loading } from "../ui";
import { KNOWN_KEYS, RESERVED_KEYS, SaveButtons, useEnvSettings } from "../envSettings";

export default function CustomValues({ env }) {
  const editor = useEnvSettings(env);
  const { draft, replace } = editor;
  const keys = Object.keys(draft).filter((k) => !KNOWN_KEYS.includes(k) && !RESERVED_KEYS.includes(k));
  const values = Object.fromEntries(keys.map((k) => [k, draft[k]]));
  const setValues = (next) => replace((d) => {
    const kept = Object.fromEntries(Object.entries(d).filter(([k]) => KNOWN_KEYS.includes(k) || RESERVED_KEYS.includes(k)));
    return { ...kept, ...next };
  });
  return (
    <Loading state={editor.state}>
      {() => !editor.ready ? null : (
        <Card icon="braces" title="Plain values" description="Not secret, and visible to anyone who can open Studio. Flows and policies read them as $settings.name. Use a secret for anything private." actions={<SaveButtons editor={editor} />}>
          <KeyValue value={values} onChange={setValues} keyLabel="Name" valueLabel="Value" addLabel="Add a value" />
        </Card>
      )}
    </Loading>
  );
}
