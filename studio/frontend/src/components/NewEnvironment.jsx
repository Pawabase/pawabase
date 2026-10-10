import { useState } from "react";
import { Button, Field, Modal, useAction } from "./ui";
import { post } from "../lib/api";

/** Creates an environment, optionally copying another's definitions. `onDone` gets the new environment's name. */
export default function NewEnvironment({ envs, onClose, onDone }) {
  const [data, setData] = useState({ name: "", copy_from: "" });
  const [run, busy] = useAction();
  return (
    <Modal title="New environment" onClose={onClose} footer={<Button variant="primary" disabled={busy || !data.name} onClick={async () => {
      if (await run(() => post("/envs", { name: data.name, copy_from: data.copy_from || null }), "Environment created")) { onClose(); onDone(data.name); }
    }}>Create</Button>}>
      <Field label="Name"><input autoFocus value={data.name} onChange={(e) => setData({ ...data, name: e.target.value })} placeholder="staging" /></Field>
      <Field label="Copy definitions from">
        <select value={data.copy_from} onChange={(e) => setData({ ...data, copy_from: e.target.value })}>
          <option value="">Start empty</option>
          {envs.map((e) => <option key={e.name}>{e.name}</option>)}
        </select>
      </Field>
    </Modal>
  );
}
