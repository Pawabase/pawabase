import { usePage } from "@inertiajs/react";
import { useEffect, useMemo, useState } from "react";
import { Badge, Button, Card, Field, Loading, Segmented } from "../../../components/ui";
import { post } from "../../../lib/api";
import { AUTH, SettingsPanel, useAuthConfig } from "./shared";

const DEFAULTS = {
  name: "", logo_url: "", primary: "#FF7049", background: "#f3f1fb", card: "#ffffff", text: "#1f1b2e",
  radius: 14, font: "system", theme: "light", layout: "split", tagline: "", hero_url: "", footer: "",
  login_title: "Welcome back", login_subtitle: "", login_button: "Sign in",
  signup_title: "Create your account", signup_subtitle: "", signup_button: "Create account", terms_url: "", privacy_url: "",
  forgot_title: "Reset your password", forgot_subtitle: "Enter your email address and we'll send you a link to choose a new password.",
  reset_title: "Choose a new password", reset_subtitle: "Pick something you haven't used before.",
  custom_css: "",
};
const FONTS = [["system", "System"], ["rounded", "Rounded"], ["serif", "Serif"], ["mono", "Mono"]];

// Every screen a person can see, in the order they meet them. `live` ones have an address of their own.
const SCREENS = [
  { key: "login", label: "Sign in", about: "Email and password, social buttons, sign-in link.", live: true, words: ["login_title", "login_subtitle", "login_button"] },
  { key: "signup", label: "Create account", about: "The details sign-up asks for, and the terms.", live: true, words: ["signup_title", "signup_subtitle", "signup_button"], terms: true },
  { key: "mfa", label: "Two-step code", about: "Shown after the password when a person has MFA." },
  { key: "forgot", label: "Forgot password", about: "Asks for an email address to send the link to.", live: true, words: ["forgot_title", "forgot_subtitle"] },
  { key: "check-email", label: "Check your email", about: "After a link is sent." },
  { key: "reset", label: "New password", about: "Opened from the emailed link.", live: true, words: ["reset_title", "reset_subtitle"] },
  { key: "signed-in", label: "All set", about: "When a link is used and there is nowhere to send the person." },
  { key: "error", label: "Link not allowed", about: "When the sending app is not on your allowed addresses." },
];
const WORDS = {
  login_title: ["Heading", 80], login_subtitle: ["Subheading", 200], login_button: ["Button", 40],
  signup_title: ["Heading", 80], signup_subtitle: ["Subheading", 200], signup_button: ["Button", 40],
  forgot_title: ["Heading", 80], forgot_subtitle: ["Subheading", 200],
  reset_title: ["Heading", 80], reset_subtitle: ["Subheading", 200],
};

function encode(value) {
  const bytes = new TextEncoder().encode(JSON.stringify(value));
  let binary = "";
  bytes.forEach((b) => { binary += String.fromCharCode(b); });
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function Color({ label, value, onChange }) {
  return (
    <Field label={label}>
      <div className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5">
        <input type="color" value={value} onChange={(e) => onChange(e.target.value)} style={{ width: 46, height: 40, padding: 3 }} aria-label={label} />
        <input value={value} onChange={(e) => onChange(e.target.value)} spellCheck={false} style={{ fontFamily: "var(--mono)" }} />
      </div>
    </Field>
  );
}

/** The page's HTML split into what a person would edit by hand: markup, styles and script. */
function split(source) {
  const styles = [...source.matchAll(/<style[^>]*>([\s\S]*?)<\/style>/g)].map((m) => m[1].trim()).join("\n\n");
  const script = [...source.matchAll(/<script[^>]*>([\s\S]*?)<\/script>/g)].map((m) => m[1].trim()).join("\n\n");
  const markup = source.replace(/<style[^>]*>[\s\S]*?<\/style>/g, "<style>/* see Styles */</style>").replace(/<script[^>]*>[\s\S]*?<\/script>/g, "<script>/* see Script */</script>");
  return { markup: pretty(markup), styles: styles.replace(/\}\s*/g, "}\n").replace(/;(?=[^}\n])/g, ";"), script };
}

function pretty(html) {
  let depth = 0;
  const voids = /^(meta|link|img|input|br|hr)$/i;
  return html
    .replace(/>\s*</g, ">\n<")
    .split("\n")
    .map((line) => {
      const trimmed = line.trim();
      if (/^<\//.test(trimmed)) depth = Math.max(0, depth - 1);
      const out = "  ".repeat(depth) + trimmed;
      const open = trimmed.match(/^<([a-zA-Z0-9-]+)/);
      if (open && !voids.test(open[1]) && !/\/>$/.test(trimmed) && !new RegExp(`</${open[1]}>$`).test(trimmed) && !/^<!/.test(trimmed)) depth += 1;
      return out;
    })
    .join("\n");
}

function CodeBlock({ text }) {
  const [copied, setCopied] = useState(false);
  const lines = text.split("\n");
  return (
    <div className="codeview [border:1px_solid_var(--line)] rounded-sm bg-[#121119] text-[#e6e3f7] [font:12.5px/1.65_var(--mono)] overflow-hidden max-h-[62vh] p-[10px_0]">
      <div className="codeview-bar flex justify-between items-center p-[10px_14px] [border-bottom:1px_solid_var(--line)] rounded-none">
        <span className="muted text-muted small text-ui-sm">{lines.length} lines</span>
        <Button size="sm" onClick={() => { navigator.clipboard?.writeText(text); setCopied(true); setTimeout(() => setCopied(false), 1200); }}>{copied ? "Copied" : "Copy"}</Button>
      </div>
      <pre className="codeview-body text-ink m-0 p-[14px_0] max-h-[640px] overflow-auto font-mono text-ui-sm leading-[1.6] [&_code]:text-ink"><code>{lines.map((line, i) => <span key={i} className="codeview-line block whitespace-pre [&.target]:bg-brand-soft p-[0_16px_0_0] [&_i]:inline-block [&_i]:w-12 [&_i]:pr-3.5 [&_i]:text-right [&_i]:text-[var(--faint,_#9a96ad)] [&_i]:not-italic [&_i]:select-none"><i>{i + 1}</i>{line || " "}{"\n"}</span>)}</code></pre>
    </div>
  );
}

function Source({ env, base, page, branding }) {
  const [state, setState] = useState({ loading: true });
  const [part, setPart] = useState("markup");
  const signature = JSON.stringify([page, branding]);
  useEffect(() => {
    let alive = true;
    setState((s) => ({ ...s, loading: true }));
    const timer = setTimeout(() => {
      post(`${base}/hosted-source`, { page, branding }, AUTH)
        .then((data) => alive && setState({ data: split(data.html), live: data.live }))
        .catch((error) => alive && setState({ error: error.message || "Could not load the source" }));
    }, 250);
    return () => { alive = false; clearTimeout(timer); };
  }, [signature]); // eslint-disable-line react-hooks/exhaustive-deps
  if (state.error) return <p className="note bad">{state.error}</p>;
  if (!state.data) return <p className="muted text-muted">Loading the source…</p>;
  return (
    <div className="stack flex flex-col gap-3 [&.sm]:gap-1.5 [&.lg]:gap-5">
      <div className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5 spread flex items-center justify-between gap-3">
        <Segmented options={[["markup", "HTML"], ["styles", "Styles"], ["script", "Script"]]} value={part} onChange={setPart} />
        <span className="hint text-xs font-book text-muted leading-[1.45]">What a person's browser receives. Change it from the Edit tab.</span>
      </div>
      <CodeBlock text={state.data[part] || "(nothing)"} />
    </div>
  );
}

/** Every sign-in screen: preview it, read its code, and change how it looks and what it says. */
export default function Screens({ env, base }) {
  const config = useAuthConfig(env);
  const { gateway_url: gatewayUrl } = usePage().props;
  const [selected, setSelected] = useState("brand");
  const [mode, setMode] = useState("preview");
  const gateway = (gatewayUrl || "").replace(/\/$/, "");
  const branding = { ...DEFAULTS, ...(config.value.branding || {}) };
  const set = (values) => config.set({ branding: { ...(config.value.branding || {}), ...values } });
  const screen = SCREENS.find((s) => s.key === selected);
  const previewPage = screen ? screen.key : "login";
  const previewUrl = useMemo(
    () => `${gateway}/auth/v1/hosted/${env}/preview/${previewPage}?b=${encode(config.value.branding || {})}`,
    [gateway, env, previewPage, config.value.branding],
  );
  const modes = selected === "brand" ? [["preview", "Preview"], ["edit", "Edit"]] : [["preview", "Preview"], ["code", "Code"], ["edit", "Edit"]];
  const effectiveMode = modes.some(([k]) => k === mode) ? mode : "preview";

  const frame = <div className="pages-frame [border:1px_solid_var(--line-2)] rounded-sm overflow-hidden bg-surface [&_iframe]:block [&_iframe]:w-full [&_iframe]:h-[640px] [&_iframe]:border-0"><iframe title="Screen preview" src={previewUrl} sandbox="allow-scripts allow-same-origin" /></div>;

  const brandFields = (
    <SettingsPanel icon="sparkle" title="Brand" description="Applies to every screen." config={config}>
      <Field label="Product name" hint="Shown above the form when there is no logo."><input value={branding.name} onChange={(e) => set({ name: e.target.value })} placeholder="Acme" maxLength={80} /></Field>
      <Field label="Logo address" hint="An https image, up to 44px tall."><input value={branding.logo_url} onChange={(e) => set({ logo_url: e.target.value })} placeholder="https://acme.com/logo.svg" /></Field>
      <div className="grid2 grid grid-cols-2 gap-[14px_16px] max-tablet:grid-cols-1">
        <Color label="Brand colour" value={branding.primary} onChange={(v) => set({ primary: v })} />
        <Color label="Page background" value={branding.background} onChange={(v) => set({ background: v })} />
        <Color label="Card" value={branding.card} onChange={(v) => set({ card: v })} />
        <Color label="Text" value={branding.text} onChange={(v) => set({ text: v })} />
      </div>
      <div className="grid2 grid grid-cols-2 gap-[14px_16px] max-tablet:grid-cols-1">
        <Field label={`Corner radius: ${branding.radius}px`}><input type="range" min="0" max="28" value={branding.radius} onChange={(e) => set({ radius: Number(e.target.value) })} /></Field>
        <Field label="Font"><select value={branding.font} onChange={(e) => set({ font: e.target.value })}>{FONTS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></Field>
      </div>
      <Field label="Layout" hint="Split puts your tagline and image beside the form. Centered is a single card."><Segmented options={[["split", "Split"], ["center", "Centered"]]} value={branding.layout} onChange={(v) => set({ layout: v })} /></Field>
      {branding.layout === "split" && (
        <>
          <Field label="Tagline" hint="A sentence beside the form."><input value={branding.tagline} onChange={(e) => set({ tagline: e.target.value })} maxLength={140} placeholder="Ship faster with Acme" /></Field>
          <Field label="Background image" optional hint="An https image behind the tagline, tinted with your brand colour."><input value={branding.hero_url} onChange={(e) => set({ hero_url: e.target.value })} placeholder="https://acme.com/hero.jpg" /></Field>
        </>
      )}
      <Field label="Theme"><Segmented options={[["light", "Light"], ["dark", "Dark"], ["auto", "Match the device"]]} value={branding.theme} onChange={(v) => set({ theme: v })} /></Field>
      <Field label="Footer line" optional><input value={branding.footer} onChange={(e) => set({ footer: e.target.value })} maxLength={200} placeholder="© Acme Inc." /></Field>
    </SettingsPanel>
  );

  const screenFields = screen && (
    <div className="stack flex flex-col gap-3 [&.sm]:gap-1.5 [&.lg]:gap-5 lg">
      <SettingsPanel icon="book" title={`${screen.label}: words`} description={screen.about} config={config}>
        {(screen.words || []).map((key) => (
          <Field key={key} label={WORDS[key][0]}><input value={branding[key]} maxLength={WORDS[key][1]} onChange={(e) => set({ [key]: e.target.value })} /></Field>
        ))}
        {screen.terms && (
          <>
            <p className="hint text-xs font-book text-muted leading-[1.45]" style={{ margin: 0 }}>Which details this screen asks for is set under Attributes, and the terms and privacy links under Legal.</p>
          </>
        )}
        {!(screen.words || []).length && !screen.terms && <p className="hint text-xs font-book text-muted leading-[1.45]" style={{ margin: 0 }}>The words on this screen are fixed. Change how it looks under Brand, or with custom styles below.</p>}
      </SettingsPanel>
    </div>
  );

  const cssFields = (
    <SettingsPanel icon="code" title="Custom styles" description="Plain CSS added after Pawabase's own, to every screen. No @import, no url(), no escapes: it can restyle, not load or run anything." config={config}>
      <textarea rows={8} value={branding.custom_css} onChange={(e) => set({ custom_css: e.target.value })} spellCheck={false} placeholder={".primary { letter-spacing: .02em; }\nh1 { font-weight: 800; }"} style={{ fontFamily: "var(--mono)", fontSize: 13 }} />
      <p className="hint text-xs font-book text-muted leading-[1.45]" style={{ margin: 0 }}>Useful hooks: <code>.primary</code> (buttons), <code>.fld</code> (a field), <code>.card</code>, <code>.brand</code> (the side panel), <code>h1</code>, <code>.sub</code>.</p>
    </SettingsPanel>
  );

  return (
    <Loading state={config.state}>
      {() => (
        <div className="screens grid grid-cols-[260px_minmax(0,_1fr)] gap-6 items-start max-search:grid-cols-1">
          <nav className="screens-list flex flex-col gap-1.5 sticky top-4 max-search:static max-search:flex-row max-search:flex-wrap" aria-label="Screens">
            <button type="button" className={`screens-item text-left bg-surface [border:1px_solid_var(--line)] rounded-sm p-[12px_14px] cursor-pointer font-inherit text-inherit flex flex-col gap-[3px] [&_b]:flex [&_b]:items-center [&_b]:gap-2 [&_b]:text-base [&_span]:text-muted [&_span]:text-ui-sm [&_span]:leading-[1.4] [&:hover]:border-line-2 [&.active]:[border-color:var(--primary,_#FF7049)] [&.active]:shadow-none max-search:[flex:1_1_200px] ${selected === "brand" ? "active" : ""}`} onClick={() => setSelected("brand")}>
              <b>Brand & theme</b><span>Logo, colours, layout, fonts.</span>
            </button>
            <div className="nav-title text-2xs font-semibold uppercase tracking-[0.08em] text-faint p-[16px_10px_6px] [&::after]:hidden">Screens</div>
            {SCREENS.map((s) => (
              <button key={s.key} type="button" className={`screens-item text-left bg-surface [border:1px_solid_var(--line)] rounded-sm p-[12px_14px] cursor-pointer font-inherit text-inherit flex flex-col gap-[3px] [&_b]:flex [&_b]:items-center [&_b]:gap-2 [&_b]:text-base [&_span]:text-muted [&_span]:text-ui-sm [&_span]:leading-[1.4] [&:hover]:border-line-2 [&.active]:[border-color:var(--primary,_#FF7049)] [&.active]:shadow-none max-search:[flex:1_1_200px] ${selected === s.key ? "active" : ""}`} onClick={() => setSelected(s.key)}>
                <b>{s.label}{s.live ? <Badge tone="green">live</Badge> : null}</b><span>{s.about}</span>
              </button>
            ))}
          </nav>
          <div className="screens-main">
            <div className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5 spread flex items-center justify-between gap-3" style={{ marginBottom: 14 }}>
              <h2 style={{ margin: 0 }}>{selected === "brand" ? "Brand & theme" : screen.label}</h2>
              <div className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5">
                {screen?.live && <a className="hint text-xs font-book text-muted leading-[1.45]" href={`${gateway}/auth/v1/hosted/${env}/${screen.key}`} target="_blank" rel="noopener noreferrer">Open the live page</a>}
                <Segmented options={modes} value={effectiveMode} onChange={setMode} />
              </div>
            </div>
            {effectiveMode === "preview" && <div className="screens-preview [&_.pages-frame_iframe]:h-[720px]">{frame}<p className="hint text-xs font-book text-muted leading-[1.45]">Shows unsaved changes. Nothing is sent from it.</p></div>}
            {effectiveMode === "code" && <Source env={env} base={base} page={previewPage} branding={config.value.branding || {}} />}
            {effectiveMode === "edit" && (
              <div className="pages-grid grid grid-cols-[minmax(0,_1fr)_minmax(0,_1fr)] gap-6 items-start max-layout:grid-cols-1">
                <div className="stack flex flex-col gap-3 [&.sm]:gap-1.5 [&.lg]:gap-5 lg">{selected === "brand" ? brandFields : screenFields}{cssFields}</div>
                <div className="pages-preview sticky top-4 max-layout:static">{frame}</div>
              </div>
            )}
          </div>
        </div>
      )}
    </Loading>
  );
}
