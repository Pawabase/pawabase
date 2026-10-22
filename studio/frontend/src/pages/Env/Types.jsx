// Types: TypeScript and Python types for the environment's resources, generated from their definitions. Paste or
// download them, or refresh them in CI with the secret key.

import { usePage } from "@inertiajs/react";
import { useEffect, useState } from "react";
import Layout from "../../components/Layout";
import { useDataSubnav } from "../../components/DataNav";
import { Icon } from "../../components/icons";
import { Button, Card, CopyText, Loading, PageHead, Segmented, Spinner, useToast } from "../../components/ui";
import { envPath, get, useApi } from "../../lib/api";
import "./sql/sql.css";

const LANGS = {
  typescript: { label: "TypeScript", file: "pawabase.types.ts", type: "text/typescript" },
  python: { label: "Python", file: "pawabase_types.py", type: "text/x-python" },
};

function download(filename, text, type) {
  const url = URL.createObjectURL(new Blob([text], { type }));
  Object.assign(document.createElement("a"), { href: url, download: filename }).click();
  URL.revokeObjectURL(url);
}

export default function Types({ env }) {
  const toast = useToast();
  const { subnav } = useDataSubnav(env, "types");
  const [lang, setLang] = useState("typescript");
  const [text, setText] = useState(null);
  const [error, setError] = useState(null);
  const resources = useApi(envPath(env, "/resources"), { params: { limit: 500 } });
  const count = resources.data?.data?.length;

  useEffect(() => {
    let live = true;
    setText(null);
    setError(null);
    get(envPath(env, "/types"), { params: { lang } })
      .then((out) => live && setText(typeof out === "string" ? out : JSON.stringify(out, null, 2)))
      .catch((e) => live && setError(e.message));
    return () => { live = false; };
  }, [env, lang]);

  const meta = LANGS[lang];
  const lines = text ? text.split("\n").length : 0;
  const gateway = usePage().props.gateway_url || "https://<your-gateway>";

  return (
    <Layout title="Types" subnav={subnav}>
      <PageHead
        title="Types"
        description="Types for your resources, generated from their definitions: what a read returns, what a create requires, what an update accepts. They update when your resources do."
        actions={
          <>
            <Segmented value={lang} onChange={setLang} options={Object.entries(LANGS).map(([k, v]) => [k, v.label])} />
            <Button disabled={!text} onClick={() => { navigator.clipboard.writeText(text); toast("Copied"); }}><Icon name="copy" />Copy</Button>
            <Button variant="primary" disabled={!text} onClick={() => download(meta.file, text, meta.type)}><Icon name="arrowDown" />Download {meta.file}</Button>
          </>
        }
      />
      <div className="sql-page" style={{ gridTemplateColumns: "minmax(0, 1fr) 340px" }}>
        <div className="sql-main">
          {error && <div className="alert error">{error}</div>}
          {!text && !error && <div className="empty"><Spinner /></div>}
          {text && (
            <Card flush title={meta.file} description={`${lines.toLocaleString()} lines${count !== undefined ? ` · ${count} resource${count === 1 ? "" : "s"}` : ""}`}>
              <pre className="code-block sql-plan" style={{ maxHeight: "70vh", borderRadius: 0, border: 0 }}>{text}</pre>
            </Card>
          )}
        </div>
        <div className="sql-main">
          <Card icon="code" title="Use them">
            <div className="stack">
              {lang === "typescript" ? (
                <>
                  <p className="hint">Save the file into your project, then hand it to the client. Rows, inserts, updates and filters are checked.</p>
                  <pre className="code-block">{`import { createClient } from "@pawabase/client";
import type { Database } from "./pawabase.types";

const pawabase = createClient<Database>({ url, apiKey });
await pawabase.from("orders").create({ … });`}</pre>
                  <p className="hint"><code>Change&lt;"orders"&gt;</code> types the messages on a resource's realtime channel.</p>
                </>
              ) : (
                <>
                  <p className="hint">Save the file into your project. The classes are <code>TypedDict</code>s, so they check dictionaries without changing them at runtime.</p>
                  <pre className="code-block">{`from pawabase_types import OrdersRow, OrdersInsert

def total(order: OrdersRow) -> int:
    return order["total_minor"]

new: OrdersInsert = {"customer_id": "…"}`}</pre>
                </>
              )}
            </div>
          </Card>
          <Card icon="refresh" title="Keep them current">
            <div className="stack">
              <p className="hint">Refresh the file in CI with this environment's secret key, so a change to a resource shows up as a type error before it ships.</p>
              <CopyText text={`curl -s -H "apikey: $PAWABASE_SECRET_KEY" "${gateway}/platform/v1/envs/${env}/types?lang=${lang}" -o ${meta.file}`} />
            </div>
          </Card>
        </div>
      </div>
    </Layout>
  );
}
