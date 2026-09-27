import { Head, useForm } from "@inertiajs/react";
import { Icon } from "../../components/icons";
import { Logo } from "../../components/Logo";

export default function Login({ mfa_token }) {
  const form = useForm(mfa_token ? { mfa_token, code: "" } : { email: "", password: "" });
  const submit = (e) => {
    e.preventDefault();
    form.post("/login", { preserveState: true });
  };
  return (
    <div className="login">
      <Head title="Sign in" />
      <section className="login-art">
        <span className="blob" style={{ width: 380, height: 380, right: -120, bottom: -100, background: "var(--peach)" }} />
        <span className="blob" style={{ width: 220, height: 220, right: 150, bottom: 170, background: "var(--mint)" }} />
        <span className="blob" style={{ width: 120, height: 120, right: 60, bottom: 330, background: "var(--butter)" }} />
        <div style={{ position: "relative" }}><Logo sub="Studio" /></div>
        <div className="stack" style={{ position: "relative", gap: 14 }}>
          <h2>Bring your infrastructure. Build your backend.</h2>
          <p>Resources, auth, storage, flows and realtime, on your own Postgres, Redis and S3, managed from one calm place.</p>
          <div className="row wrap" style={{ gap: 8 }}>
            {[["resources", "REST in minutes"], ["policies", "Policies, not glue"], ["flows", "Visual flows"]].map(([icon, label]) => (
              <span key={label} className="badge" style={{ background: "rgba(255,255,255,.55)", color: "var(--pastel-ink)", padding: "6px 12px" }}><Icon name={icon} size={14} />{label}</span>
            ))}
          </div>
        </div>
      </section>
      <div className="login-form">
      <form className="card stack" style={{ gap: 16 }} onSubmit={submit}>
        <div>
          <h1 style={{ fontSize: 24 }}>{mfa_token ? "Two-step check" : "Welcome back"}</h1>
          <div className="muted" style={{ marginTop: 4 }}>{mfa_token ? "Enter the code from your authenticator." : "Sign in to Pawabase Studio."}</div>
        </div>
        {mfa_token ? (
          <label className="field">
            Authenticator code
            <input autoFocus inputMode="numeric" autoComplete="one-time-code" value={form.data.code} onChange={(e) => form.setData("code", e.target.value)} />
            {form.errors.code && <span className="error-text">{form.errors.code}</span>}
          </label>
        ) : (
          <>
            <label className="field">
              Email
              <input type="email" autoFocus autoComplete="username" value={form.data.email} onChange={(e) => form.setData("email", e.target.value)} />
            </label>
            <label className="field">
              Password
              <input type="password" autoComplete="current-password" value={form.data.password} onChange={(e) => form.setData("password", e.target.value)} />
            </label>
            {form.errors.email && <div className="alert error">{form.errors.email}</div>}
          </>
        )}
        <button className="btn primary" type="submit" disabled={form.processing} style={{ justifyContent: "center", padding: "11px 16px" }}>
          {mfa_token ? "Verify" : "Sign in"}
        </button>
        <p className="hint" style={{ margin: 0 }}>
          Operators are users of the reserved <code>_platform</code> project. The first one comes from
          <code> PAWABASE_ADMIN_EMAIL</code> / <code>PAWABASE_ADMIN_PASSWORD</code>.
        </p>
      </form>
      </div>
    </div>
  );
}
