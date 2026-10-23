// Mail: an overview of sending, the template library and its visual editor, a
// composer for one-off messages, every sent message, and delivery settings.

import { useEffect, useState } from "react";
import Layout, { sectionNav, useSection } from "../../components/Layout";
import { Button, PageHead } from "../../components/ui";
import { Icon } from "../../components/icons";
import { envPath, useApi } from "../../lib/api";
import { Activity } from "./mail/Activity";
import { Composer } from "./mail/Composer";
import { Delivery, readMail } from "./mail/Delivery";
import { Kit } from "./mail/Kit";
import { Library } from "./mail/Library";
import { Overview } from "./mail/Overview";
import { TemplateWorkspace } from "./mail/TemplateWorkspace";
import "./mail/mail.css";

const SECTIONS = ["overview", "templates", "compose", "activity", "kit", "delivery"];

/** The template being edited lives in the address, so a refresh or a shared link reopens it. */
function useTemplateParam() {
  const read = () => new URLSearchParams(window.location.search).get("template");
  const [name, setName] = useState(read);
  useEffect(() => {
    const back = () => setName(read());
    window.addEventListener("popstate", back);
    return () => window.removeEventListener("popstate", back);
  }, []);
  const set = (next) => {
    const url = new URL(window.location.href);
    if (next) { url.searchParams.set("template", next); url.searchParams.set("section", "templates"); } else url.searchParams.delete("template");
    window.history.pushState({}, "", url);
    setName(next);
  };
  return [name, set];
}

export default function Mail({ env }) {
  const base = envPath(env);
  const [section, setSection] = useSection(SECTIONS);
  const [template, setTemplate] = useTemplateParam();
  const [creating, setCreating] = useState(false);
  const [message, setMessage] = useState(null);
  const setup = useApi(`${base}/mail/config`);
  const environment = useApi(base);
  const kitState = useApi(`${base}/mail/kit`);
  const kit = kitState.data ? { ...kitState.data, reload: kitState.reload } : null;
  const mail = readMail(environment.data?.infra?.mail);
  const sender = mail.senders.find((s) => s.default) || mail.senders[0];
  const defaultFrom = sender ? (sender.name ? `${sender.name} <${sender.email}>` : sender.email) : "";
  const configured = Boolean(setup.data?.configured) && !setup.data?.suppressed;

  const go = (next, opts = {}) => {
    setCreating(Boolean(opts.create));
    setSection(next);
  };

  return (
    <Layout title="Mail" subnav={sectionNav("Mail", [
      { title: "Mail", items: [
        ["overview", "Overview", null, { icon: "pulse" }],
        ["templates", "Templates", null, { icon: "mail-templates" }],
        ["compose", "Compose", null, { icon: "send" }],
        ["activity", "Activity", null, { icon: "mail" }],
      ] },
      { title: "Setup", items: [
        ["kit", "Brand kit", null, { icon: "sparkle" }],
        ["delivery", "Delivery", null, { icon: "settings", flag: setup.data && !configured ? "warn" : undefined }],
      ] },
    ], section, (next) => go(next))}>
      {section === "overview" && <>
        <PageHead title="Mail" description="What this environment sends, how it went, and the templates behind it." actions={<Button variant="primary" onClick={() => go("templates", { create: true })}><Icon name="plus" />New template</Button>} />
        <div className="mail-page"><Overview env={env} setup={setup.data} onGo={go} onOpenTemplate={setTemplate} onOpenMessage={(id) => { setMessage(id); setSection("activity"); }} /></div>
      </>}
      {section === "templates" && <>
        <PageHead title="Templates" description="Emails that flows and sign-in send by name. Edit visually, preview with sample values, publish when ready." />
        <div className="mail-page"><Library key={creating ? "create" : "browse"} env={env} kit={kit} startCreating={creating} onOpen={setTemplate} /></div>
      </>}
      {section === "compose" && <>
        <PageHead title="Compose" description="Write and send one message, now or later, with or without a template." />
        <div className="mail-page"><Composer env={env} kit={kit} senders={mail.senders} defaultFrom={defaultFrom} onSent={(result) => { setMessage(result.id); setSection("activity"); }} onSavedTemplate={(name) => setTemplate(name)} /></div>
      </>}
      {section === "activity" && <>
        <PageHead title="Activity" description="Every message this environment sent, or would have sent: what it said, where it went and what the server answered." />
        <div className="mail-page"><Activity env={env} openId={message} onOpened={() => setMessage(null)} /></div>
      </>}
      {section === "kit" && <>
        <PageHead title="Brand kit" description="Styles, colours, reusable blocks and folders shared by every template in this environment." />
        <div className="mail-page"><Kit env={env} kit={kit} /></div>
      </>}
      {section === "delivery" && <>
        <PageHead title="Delivery" description="The SMTP providers mail goes out through and the addresses it comes from." />
        <div className="mail-page"><Delivery env={env} environment={environment} setup={setup.data} onSaved={() => setup.reload()} /></div>
      </>}
      {template && <TemplateWorkspace env={env} name={template} kit={kit} onClose={() => setTemplate(null)} onRenamed={setTemplate} onDuplicated={setTemplate} />}
    </Layout>
  );
}
