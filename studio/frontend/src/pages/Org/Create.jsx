import { Head, Link, router } from "@inertiajs/react";
import { useState } from "react";
import Layout from "../../components/Layout";
import { Icon } from "../../components/icons";
import { Logo } from "../../components/Logo";
import { Button, Card, Field, PageHead, useAction, ToastProvider } from "../../components/ui";
import { post } from "../../lib/api";

function slugify(name) {
  return name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").replace(/^[^a-z]+/, "").slice(0, 40);
}

function useCreateOrg() {
  const [data, setData] = useState({ name: "", slug: "" });
  const [run, busy] = useAction();
  const slug = data.slug || slugify(data.name);
  const ready = data.name.trim() && slug.length >= 2;
  const submit = async (e) => {
    e?.preventDefault();
    if (!ready) return;
    const org = await run(() => post("/orgs", { name: data.name.trim(), slug }), "Organization created");
    if (org) router.visit(`/orgs/${org.slug}`);
  };
  return { data, setData, slug, ready, busy, submit };
}

// The hook lives here, below the toast provider, so failures are shown.
function Form({ first }) {
  const { data, setData, slug, ready, busy, submit } = useCreateOrg();
  return (
    <form className="stack" style={{ gap: 16 }} onSubmit={submit}>
      <Field label="Organization name" hint="Your company or team. You can rename it later.">
        <input autoFocus value={data.name} onChange={(e) => setData({ ...data, name: e.target.value })} placeholder="Acme Inc." />
      </Field>
      <Field label="URL name" hint="Lower-case, used in links. It cannot be changed later.">
        <input className="mono" value={data.slug} placeholder={slugify(data.name) || "acme"} onChange={(e) => setData({ ...data, slug: e.target.value })} />
      </Field>
      <Button variant="primary" type="submit" disabled={busy || !ready} style={{ justifyContent: "center", padding: "11px 16px" }}>
        {first ? "Create organization and continue" : "Create organization"}
      </Button>
      {slug && <p className="hint" style={{ margin: 0 }}>You will be its owner and can invite your team next.</p>}
    </form>
  );
}

export default function OrgCreate({ first, operator }) {
  if (!first) {
    return (
      <Layout title="New organization">
        <PageHead title="New organization" description="A separate home for a team's projects, members and audit log." />
        <div style={{ maxWidth: 520 }}>
          <Card><Form first={false} /></Card>
        </div>
      </Layout>
    );
  }
  return (
    <ToastProvider>
      <div className="login">
        <Head title="Set up your organization" />
        <section className="login-art">
          <span className="blob" style={{ width: 380, height: 380, right: -120, bottom: -100, background: "var(--peach)" }} />
          <span className="blob" style={{ width: 220, height: 220, right: 150, bottom: 170, background: "var(--mint)" }} />
          <span className="blob" style={{ width: 120, height: 120, right: 60, bottom: 330, background: "var(--butter)" }} />
          <div style={{ position: "relative" }}><Logo sub="Studio" /></div>
          <div className="stack" style={{ position: "relative", gap: 14 }}>
            <h2>Everything lives in an organization.</h2>
            <p>Projects belong to an organization, and so do the people who work on them. Set yours up first; add teammates and create projects right after.</p>
            <div className="row wrap" style={{ gap: 8 }}>
              {[["org", "Organizations"], ["team", "Team & roles"], ["projects", "Projects"]].map(([icon, label]) => (
                <span key={label} className="badge" style={{ background: "rgba(255,255,255,.55)", color: "var(--pastel-ink)", padding: "6px 12px" }}><Icon name={icon} size={14} />{label}</span>
              ))}
            </div>
          </div>
        </section>
        <div className="login-form">
          <div className="card stack" style={{ gap: 16 }}>
            <div>
              <h1 style={{ fontSize: 24 }}>Create your organization</h1>
              <div className="muted" style={{ marginTop: 4 }}>Signed in as {operator?.email}. This is the first step.</div>
            </div>
            <Form first />
            <Link href="/logout" method="post" as="button" className="btn ghost" style={{ justifyContent: "center" }}>Sign out</Link>
          </div>
        </div>
      </div>
    </ToastProvider>
  );
}
