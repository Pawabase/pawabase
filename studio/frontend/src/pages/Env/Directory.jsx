import { useState } from "react";
import Layout from "../../components/Layout";
import { PageHead } from "../../components/ui";
import Pages from "./auth/Pages";
import ApiKeys from "./directory/ApiKeys";
import Attributes from "./directory/Attributes";
import Devices from "./directory/Devices";
import Federation from "./directory/Federation";
import Overview from "./directory/Overview";
import PasswordPolicy from "./directory/PasswordPolicy";
import Recovery from "./directory/Recovery";
import SignIn from "./directory/SignIn";

const GROUPS = [
  { items: [["overview", "Overview"]] },
  { title: "Identity", items: [["signin", "Sign-in & registration"], ["attributes", "Attributes"]] },
  { title: "Security", items: [["passwords", "Password policy"], ["recovery", "Account recovery"], ["devices", "Devices"], ["federation", "Company sign-in"], ["apikeys", "User API keys"]] },
  { title: "Appearance", items: [["branding", "Hosted pages"]] },
];
const ABOUT = {
  overview: "The rules this environment's people live under, in one place.",
  signin: "How people register and which identifiers they sign in with.",
  attributes: "The details a person has: which sign-up asks for, which they may change, and which ride in their token.",
  passwords: "Strength, reuse, expiry and breach checks.",
  recovery: "How someone gets back into an account.",
  devices: "Which browsers and apps people sign in from, and which are trusted.",
  federation: "Let a company's people sign in with their own identity provider: OpenID Connect or SAML 2.0.",
  apikeys: "Keys people create for themselves to call the API as themselves.",
  branding: "The hosted sign-in, sign-up and password-reset pages, in your brand.",
};
const LABELS = Object.fromEntries(GROUPS.flatMap((g) => g.items));

/** One environment's directory: its people's attributes and every rule that governs signing in. */
export default function Directory({ env }) {
  const [tab, setTab] = useState("overview");
  const base = `/envs/${env}`;
  const subnav = {
    title: "Directory",
    groups: GROUPS.map((g) => ({ title: g.title, items: g.items.map(([key, label]) => ({ key, label })) })),
    active: tab,
    onSelect: setTab,
  };
  return (
    <Layout title="Directory" subnav={subnav}>
      <PageHead title={LABELS[tab]} description={ABOUT[tab]} />
      {tab === "overview" && <Overview env={env} base={base} onOpen={setTab} />}
      {tab === "signin" && <SignIn env={env} />}
      {tab === "attributes" && <Attributes env={env} />}
      {tab === "passwords" && <PasswordPolicy env={env} />}
      {tab === "recovery" && <Recovery env={env} />}
      {tab === "devices" && <Devices env={env} base={base} />}
      {tab === "federation" && <Federation env={env} />}
      {tab === "apikeys" && <ApiKeys env={env} base={base} />}
      {tab === "branding" && <Pages env={env} />}
    </Layout>
  );
}
