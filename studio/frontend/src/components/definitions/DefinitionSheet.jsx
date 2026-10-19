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
      tabs={tabs.length > 1 ? tabs : undefined}
      tab={tab}
      onTab={setTab}
      tabsPlacement={kind === "resources" ? "top" : "side"}
      onClose={() => onClose(undefined)}
      footer={
        <>
          {!editing.isNew && onDelete && <Button variant="danger" onClick={remove} disabled={busy}><Icon name="trash" />Delete</Button>}
          {actions && actions(body, run)}
          <span className="grow flex-1 min-w-0" />
          {issue && tab === "form" && <span className="hint text-xs font-book text-muted leading-[1.45]">{issue}</span>}
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
            <div className="alert rounded-sm p-[11px_14px] text-ui [&.error]:text-danger [&.error]:bg-danger-soft [&.warn]:text-warn [&.warn]:bg-warn-soft [&.ok]:text-ok [&.ok]:bg-ok-soft [&.info]:text-info [&.info]:bg-info-soft [border:1px_solid_color-mix(in_srgb,_currentColor_22%,_transparent)] info row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5" style={{ justifyContent: "space-between" }}>
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
