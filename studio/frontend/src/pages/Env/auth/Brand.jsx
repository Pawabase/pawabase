import { usePage } from "@inertiajs/react";
import { useMemo, useRef } from "react";
import { Badge, Button, Field, Loading, useAction } from "../../../components/ui";
import { envPath, post, uploadObject, useApi } from "../../../lib/api";
import { SettingsPanel, useAuthConfig } from "./shared";

const SWATCHES = ["#FF7049", "#6D5BD0", "#2563EB", "#0F9D75", "#D9467B", "#1F1B2E"];
const LOGO_TYPES = ["image/png", "image/jpeg", "image/svg+xml", "image/webp"];

function encode(value) {
  const bytes = new TextEncoder().encode(JSON.stringify(value));
  let binary = "";
  bytes.forEach((b) => { binary += String.fromCharCode(b); });
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

/** The look of every sign-in page and email: a name, a colour and a logo kept in one of the project's buckets. */
export default function Brand({ env }) {
  const config = useAuthConfig(env);
  const buckets = useApi(envPath(env, "/buckets"));
  const { gateway_url: gatewayUrl } = usePage().props;
  const [run, busy] = useAction();
  const input = useRef(null);
  const gateway = (gatewayUrl || "").replace(/\/$/, "");
  const branding = config.value.branding || {};
  const set = (values) => config.set({ branding: { ...branding, ...values } });
  const list = buckets.data?.data || [];
  const bucket = list.find((b) => b.name === branding.logo_bucket) || list.find((b) => b.public) || list[0];
  const primary = branding.primary || "#FF7049";
  const previewUrl = useMemo(
    () => `${gateway}/auth/v1/hosted/${env}/preview/login?b=${encode({ layout: "center", ...branding })}`,
    [gateway, env, branding],
  );

  const createBucket = async () => {
    await post(envPath(env, "/buckets"), {
      name: "brand",
      description: "Your logo and brand images. Public, because sign-in pages and emails load them without signing in.",
      public: true,
      accepts: ["image/*"],
      max_bytes: 5 * 1024 * 1024,
      signed_uploads: false,
    });
    buckets.reload();
    set({ logo_bucket: "brand" });
  };

  const upload = async (file) => {
    if (!file || !bucket) return;
    if (!LOGO_TYPES.includes(file.type)) throw new Error("Choose a PNG, JPEG, SVG or WebP image.");
    const extension = file.name.split(".").pop().toLowerCase().replace(/[^a-z0-9]/g, "") || "png";
    const result = await uploadObject(env, bucket.name, `brand/logo-${Date.now().toString(36)}.${extension}`, file);
    set({ logo_url: result.url, logo_bucket: bucket.name });
  };

  return (
    <Loading state={config.state}>
      {() => (
        <div className="grid grid-cols-[minmax(0,_1fr)_minmax(0,_1fr)] gap-6 items-start max-layout:grid-cols-1">
          <div className="flex flex-col gap-3">
            <SettingsPanel icon="sparkle" title="Brand" description="Used on your sign-in pages and available to your emails as {{ brand.name }}, {{ brand.logo_url }} and {{ brand.primary }}." config={config}>
              <Field label="Name" hint="Shown beside the logo, and in emails when a template does not say otherwise.">
                <input value={branding.name || ""} onChange={(e) => set({ name: e.target.value })} placeholder="Acme" maxLength={80} />
              </Field>

              <Field label="Colour">
                <div className="flex flex-wrap items-center gap-2">
                  {SWATCHES.map((color) => (
                    <button key={color} type="button" aria-label={color} onClick={() => set({ primary: color })}
                      className="h-8 w-8 cursor-pointer rounded-full border-2"
                      style={{ background: color, borderColor: primary.toLowerCase() === color.toLowerCase() ? "var(--text)" : "transparent" }} />
                  ))}
                  <input type="color" value={primary} onChange={(e) => set({ primary: e.target.value })} aria-label="Pick a colour" style={{ width: 46, height: 36, padding: 3 }} />
                  <input value={primary} onChange={(e) => set({ primary: e.target.value })} spellCheck={false} style={{ width: 110 }} />
                </div>
              </Field>

              <Field label="Logo" hint={bucket && !bucket.public ? "This bucket is private, so people signing in could not see the logo. Choose a public bucket." : "Kept in one of your buckets, so you can replace or remove it there."}>
                <div className="flex flex-col gap-3">
                  <div className="flex flex-wrap items-center gap-3">
                    <span className="grid h-14 w-14 place-items-center overflow-hidden rounded-sm border border-line bg-sunken">
                      {branding.logo_url ? <img src={branding.logo_url} alt="Logo" className="max-h-12 max-w-12" /> : <span className="text-faint text-ui-sm">None</span>}
                    </span>
                    <select value={bucket?.name || ""} onChange={(e) => set({ logo_bucket: e.target.value })} aria-label="Bucket" disabled={!list.length} style={{ minWidth: 180 }}>
                      {list.map((b) => <option key={b.name} value={b.name}>{b.name}{b.public ? " · public" : " · private"}</option>)}
                    </select>
                    <Button size="sm" disabled={busy || !bucket} onClick={() => input.current?.click()}>{branding.logo_url ? "Replace" : "Upload"}</Button>
                    {branding.logo_url && <Button size="sm" onClick={() => set({ logo_url: "", logo_bucket: "" })}>Remove</Button>}
                    <input ref={input} type="file" accept={LOGO_TYPES.join(",")} hidden onChange={(e) => { const file = e.target.files[0]; e.target.value = ""; run(() => upload(file), "Logo uploaded"); }} />
                  </div>
                  {!list.some((b) => b.public) && (
                    <div className="flex flex-wrap items-center gap-3">
                      <span className="hint text-xs font-book text-muted leading-[1.45]">The logo has to be in a public bucket so people can see it before they sign in.</span>
                      <Button size="sm" variant="primary" disabled={busy} onClick={() => run(createBucket, "Bucket created")}>Create a public “brand” bucket</Button>
                    </div>
                  )}
                </div>
              </Field>
            </SettingsPanel>
          </div>

          <div className="sticky top-4 max-layout:static">
            <div className="flex items-center justify-between" style={{ marginBottom: 10 }}>
              <span className="text-ui-sm font-medium-plus">Sign-in page <Badge tone="green">live preview</Badge></span>
            </div>
            <div className="overflow-hidden rounded-sm border border-line-2 bg-surface [&_iframe]:block [&_iframe]:h-[620px] [&_iframe]:w-full [&_iframe]:border-0">
              <iframe title="Sign-in preview" src={previewUrl} sandbox="allow-scripts allow-same-origin" />
            </div>
            <p className="hint text-xs font-book text-muted leading-[1.45]">Shows unsaved changes. Nothing is sent from it.</p>
          </div>
        </div>
      )}
    </Loading>
  );
}
