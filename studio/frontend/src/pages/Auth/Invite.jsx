import { Head, Link, useForm, usePage } from "@inertiajs/react";
import { Icon } from "../../components/icons";
import { Logo } from "../../components/Logo";

const ROLES = { owner: "an owner", admin: "an admin", developer: "a developer", viewer: "a viewer" };

export default function Invite({ token, invitation, mismatch, operator }) {
  const { props } = usePage();
  const form = useForm({ password: "", name: "" });
  const submit = (e) => {
    e.preventDefault();
    form.post(`/invite/${token}`);
  };
  const error = form.errors.password || form.errors.invitation || props.errors?.invitation;
  return (
    <div className="login">
      <Head title="Join an organization" />
      <section className="login-art">
        <span className="blob" style={{ width: 380, height: 380, right: -120, bottom: -100, background: "var(--peach)" }} />
        <span className="blob" style={{ width: 220, height: 220, right: 150, bottom: 170, background: "var(--mint)" }} />
        <div style={{ position: "relative" }}><Logo sub="Studio" /></div>
        <div className="stack" style={{ position: "relative", gap: 14 }}>
          <h2>Work together on the same backends.</h2>
          <p>Organizations hold the projects and the people who build them.</p>
          <span className="badge" style={{ background: "rgba(255,255,255,.55)", color: "var(--pastel-ink)", padding: "6px 12px", width: "fit-content" }}><Icon name="team" size={14} />Team & roles</span>
        </div>
      </section>
      <div className="login-form">
        {!invitation ? (
          <div className="card stack" style={{ gap: 12 }}>
            <h1 style={{ fontSize: 24 }}>This invitation is no longer valid</h1>
            <p className="muted" style={{ margin: 0 }}>It may have expired, been revoked, or already been used. Ask the person who invited you to send a new one.</p>
            <Link href="/" className="btn">Go to Studio</Link>
          </div>
        ) : (
          <form className="card stack" style={{ gap: 16 }} onSubmit={submit}>
            <div>
              <h1 style={{ fontSize: 24 }}>Join {invitation.organization}</h1>
              <div className="muted" style={{ marginTop: 4 }}>
                {invitation.invited_by ? `${invitation.invited_by} invited` : "You are invited"} <b>{invitation.email}</b> to join as {ROLES[invitation.role] || invitation.role}.
              </div>
            </div>
            {mismatch ? (
              <>
                <div className="alert error">You are signed in as {operator?.email}, but this invitation is for {invitation.email}. Sign out and open the link again.</div>
                <Link href="/logout" method="post" as="button" className="btn primary" style={{ justifyContent: "center" }}>Sign out</Link>
              </>
            ) : operator ? (
              <>
                <div className="muted">You are signed in as {operator.email}.</div>
                {error && <div className="alert error">{error}</div>}
                <button className="btn primary" type="submit" disabled={form.processing} style={{ justifyContent: "center", padding: "11px 16px" }}>Accept invitation</button>
              </>
            ) : (
              <>
                <label className="field">Your name
                  <input autoFocus autoComplete="name" value={form.data.name} onChange={(e) => form.setData("name", e.target.value)} />
                </label>
                <label className="field">Choose a password
                  <input type="password" autoComplete="new-password" value={form.data.password} onChange={(e) => form.setData("password", e.target.value)} />
                  <span className="hint">At least 10 characters with upper- and lower-case letters, a digit and a symbol.</span>
                </label>
                {error && <div className="alert error">{error}</div>}
                <button className="btn primary" type="submit" disabled={form.processing || !form.data.password} style={{ justifyContent: "center", padding: "11px 16px" }}>Create account and join</button>
                <p className="hint" style={{ margin: 0, textAlign: "center" }}>
                  Already have an account? <Link href={`/login?next=${encodeURIComponent(`/invite/${token}`)}`}>Sign in</Link>
                </p>
              </>
            )}
          </form>
        )}
      </div>
    </div>
  );
}
