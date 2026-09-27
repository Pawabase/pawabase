import { Head, useForm } from "@inertiajs/react";

export default function Login({ mfa_token }) {
  const form = useForm(mfa_token ? { mfa_token, code: "" } : { email: "", password: "" });
  const submit = (e) => {
    e.preventDefault();
    form.post("/login", { preserveState: true });
  };
  return (
    <div className="login">
      <Head title="Sign in" />
      <form className="card stack" onSubmit={submit}>
        <div className="row" style={{ gap: 10 }}>
          <span className="brand-mark">P</span>
          <div>
            <h2>Pawabase Studio</h2>
            <div className="muted" style={{ fontSize: 12 }}>Bring your infrastructure, build your backend.</div>
          </div>
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
        <button className="btn primary" type="submit" disabled={form.processing} style={{ justifyContent: "center" }}>
          {mfa_token ? "Verify" : "Sign in"}
        </button>
        <p className="hint" style={{ margin: 0 }}>
          Operators are users of the reserved <code>_platform</code> project. The first one comes from
          <code> PAWABASE_ADMIN_EMAIL</code> / <code>PAWABASE_ADMIN_PASSWORD</code>.
        </p>
      </form>
    </div>
  );
}
