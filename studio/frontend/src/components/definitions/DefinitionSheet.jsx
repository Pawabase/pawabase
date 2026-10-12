// The create / edit panel for every definition kind: the form, any extra tabs
// a kind adds (records, schema, history), and kind-specific actions.
import { useState } from "react";
import { SECTION_TONES } from "../Layout";
import { Icon } from "../icons";
import { Button, Sheet, useAction } from "../ui";
import { KINDS } from "../../lib/kinds";
import { FORMS, problem } from "./forms";
import { useRefs } from "./refs";

export function singular(kind) {
  const title = KINDS[kind].title;
  if (title.endsWith("ies")) return `${title.slice(0, -3)}y`;
  return title.endsWith("s") ? title.slice(0, -1) : title;
}

export function DefinitionSheet({ kind, env, editing, onClose, onSave, onDelete, actions, extraTabs = {}, subtitle, initialTab = "form" }) {
  const meta = KINDS[kind];
  const Form = FORMS[kind];
  const refs = useRefs(env);
  const [body, setBody] = useState(editing.body);
  const [tab, setTab] = useState(extraTabs[initialTab] ? initialTab : "form");
  const [formKey, setFormKey] = useState(0);
  const [run, busy] = useAction();
  const patch = (changes) => setBody((b) => ({ ...b, ...changes }));
  const issue = problem(kind, body);

  const tabs = [
    { value: "form", label: "Configure" },
    ...Object.entries(extraTabs).map(([value, t]) => ({ value, label: t.label })),
  ];
  const save = async () => {
    const result = await run(() => onSave(body), "Saved");
    if (result) onClose(result);
  };
  const remove = async () => {
    if (!confirm(`Delete ${editing.key}? This cannot be undone.`)) return;
    if (await run(() => onDelete(), "Deleted")) onClose(null);
  };

  return (
    <Sheet
      title={editing.isNew ? `New ${singular(kind).toLowerCase()}` : String(editing.key)}
      subtitle={subtitle || meta.description}
      icon={kind}
      tone={SECTION_TONES[kind]}
      tabs={tabs}
      tab={tab}
      onTab={setTab}
      onClose={() => onClose(undefined)}
      footer={
        <>
          {!editing.isNew && onDelete && <Button variant="danger" onClick={remove} disabled={busy}><Icon name="trash" />Delete</Button>}
          {actions && actions(body, run)}
          <span className="grow" />
          {issue && tab === "form" && <span className="hint">{issue}</span>}
          <Button onClick={() => onClose(undefined)}>Cancel</Button>
          <Button variant="primary" onClick={save} disabled={busy || !!issue}>
            {editing.isNew ? `Create ${singular(kind).toLowerCase()}` : "Save changes"}
          </Button>
        </>
      }
    >
      {tab === "form" && (
        <>
          {editing.isNew && meta.template && (
            <div className="alert info row" style={{ justifyContent: "space-between" }}>
              <span>New here? Start from a worked example and adjust it.</span>
              <Button size="sm" variant="soft" onClick={() => { setBody(meta.template); setFormKey((k) => k + 1); }}><Icon name="sparkle" />Use example</Button>
            </div>
          )}
          <Form key={formKey} body={body} patch={patch} refs={refs} isNew={editing.isNew} env={env} />
        </>
      )}
      {extraTabs[tab] && extraTabs[tab].render()}
    </Sheet>
  );
}
