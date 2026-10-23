// The guided tour of Studio: what the steps are, and where the person got to.
//
// A step highlights the element marked `data-tour="<target>"` (the sidebar items, the top bar, the terminal button) and, when it names a
// `section`, opens that page first so there is something real to look at. `advance` makes a step interactive: the person can do the
// thing it describes, and the tour moves on when they do.

export const CHAPTERS = [
  { id: "welcome", label: "Welcome", tone: "lavender" },
  { id: "around", label: "Get around", tone: "lavender" },
  { id: "build", label: "Build", tone: "mint" },
  { id: "automate", label: "Automate", tone: "peach" },
  { id: "services", label: "Services", tone: "sky" },
  { id: "operate", label: "Operate", tone: "butter" },
  { id: "finish", label: "Done", tone: "rose" },
];

export const STEPS = [
  { chapter: "welcome", icon: "sparkle", title: "Welcome to Studio", body: "This is the control room for your backend. In about three minutes you will see where everything lives: your data, who can reach it, the work that runs on its own, and how you keep an eye on it. Skip any time; you can come back from the project menu.", place: "center" },

  { chapter: "around", icon: "org", title: "Project and environments", body: "Everything you see belongs to the project and environment named here. Environments (development, staging, production) each have their own data, keys and settings. Open this menu to switch or create one.", target: "env-menu", place: "bottom", advance: { on: "click" }, hint: "Click it to look" },
  { chapter: "around", icon: "gitBranch", title: "Branches and history", body: "Try a change on a branch without touching what is live, then merge it. Releases and API versions live here too, so you can ship something stable and keep working.", target: "history", place: "bottom" },
  { chapter: "around", icon: "search", title: "Jump to anything", body: "Search jumps to any page or resource. It is the fastest way around Studio once you know what you want.", target: "search", place: "bottom", advance: { on: "key", key: "k" }, hint: "Press ⌘K (or Ctrl K)" },
  { chapter: "around", icon: "terminal", title: "The terminal", body: "A terminal for your backend. List data, run a flow, emit an event, or test an endpoint with http get /health. Type help to see every command.", target: "terminal", place: "top", advance: { on: "key", key: "j" }, hint: "Press ⌘J (or Ctrl J)" },
  { chapter: "build", icon: "overview", title: "Overview", body: "The state of this environment at a glance: traffic, errors, jobs and what changed recently. Start here when something feels off.", target: "nav-overview", section: "overview", place: "right" },
  { chapter: "build", icon: "database", title: "Data", body: "Your data model. Describe a table once and Pawabase builds the REST API, validation and docs for it. Everything under Data lives in this section.", target: "nav-data", section: "tables", place: "right" },
  { chapter: "build", icon: "resources", title: "Tables", body: "Each table has its own API. Open one to edit its fields and access rules, see records, and check its Columns tab: changes you save here are not applied to the database until you apply them, and History lets you roll back.", target: "page-actions", section: "tables", place: "left" },
  { chapter: "build", icon: "layers", title: "Topology", body: "A map of how your resources connect. Declared relations are solid lines; guessed ones, from fields like author_id, can be turned into real relations from here.", target: "sub-topology", section: "topology", place: "right" },
  { chapter: "build", icon: "code", title: "SQL", body: "Run SQL against this environment's database. If a statement changes the shape of a table, Studio tells you what that means for your resources and how to fix it.", target: "sub-database", section: "database", place: "right" },
  { chapter: "build", icon: "policies", title: "Policies", body: "Named rules that say who can do what: public, signed in, owner, a role. Resources and routes point at them, so access is one readable place.", target: "nav-policies", section: "policies", place: "right" },
  { chapter: "build", icon: "explorer", title: "API Explorer", body: "Try your API as an anonymous caller or a signed-in user, without writing a client. The same requests work from the terminal with http.", target: "nav-explorer", section: "explorer", place: "right" },

  { chapter: "automate", icon: "flows", title: "Flows", body: "Build logic visually from blocks: query, branch, send mail, call out. Run a flow from an event, a schedule or an API call.", target: "nav-flows", section: "flows", place: "right" },
  { chapter: "automate", icon: "subscriptions", title: "Event subscriptions", body: "React to things that happen, such as a record being created. Connect an event to a flow and it runs on its own.", target: "nav-subscriptions", section: "subscriptions", place: "right" },
  { chapter: "automate", icon: "schedules", title: "Schedules", body: "Run something every night, every five minutes, or on a cron expression. Schedules keep running whether or not anyone has Studio open.", target: "nav-schedules", section: "schedules", place: "right" },
  { chapter: "automate", icon: "webhooks", title: "Webhooks", body: "Tell other systems when something happens (outgoing), or let them tell you with a signed inbound hook. Failed deliveries are retried.", target: "nav-webhooks", section: "webhooks", place: "right" },

  { chapter: "services", icon: "users", title: "Users and auth", body: "The people who sign in to your app: accounts, roles, organizations, two-step verification and invitations. Policies read from here.", target: "nav-users", section: "users", place: "right" },
  { chapter: "services", icon: "mail", title: "Mail", body: "Templates and a delivery log for the email your app sends. Preview a template before you rely on it.", target: "nav-mail", section: "mail", place: "right" },
  { chapter: "services", icon: "storage", title: "Storage", body: "Buckets for files, with the same rules as your data. Uploads, expiring download links, on disk or any S3-compatible service.", target: "nav-storage", section: "storage", place: "right" },
  { chapter: "services", icon: "realtime", title: "Realtime", body: "Channels with broadcast, presence and recent history over WebSockets. See who is connected and what is flowing.", target: "nav-realtime", section: "realtime", place: "right" },

  { chapter: "operate", icon: "activity", title: "Activity", body: "Everything your app did and everything that went wrong: requests and errors, background jobs and flow runs, events, webhooks, mail and logs. Set alarms that email you when a number crosses a line. The first place to look when something feels off.", target: "nav-activity", section: "activity", place: "right" },
  { chapter: "operate", icon: "usage", title: "Usage and plan", body: "How much of each limit in your plan you are using: requests, connections, storage. Set limits here before a runaway client sets them for you.", target: "nav-usage", section: "usage", place: "right" },
  { chapter: "operate", icon: "keys", title: "API keys", body: "A publishable key for your apps and a secret key for your servers, per environment. Restrict, rotate or revoke them here.", target: "nav-keys", section: "keys", place: "right" },
  { chapter: "operate", icon: "secrets", title: "Secrets", body: "Values your flows need, such as a payment provider key. They are stored encrypted and never shown again.", target: "nav-secrets", section: "secrets", place: "right" },
  { chapter: "operate", icon: "backups", title: "Backups", body: "Snapshots of this environment, on a schedule or on demand, and a way to restore them. Take one before a risky change.", target: "nav-backups", section: "backups", place: "right" },

  { chapter: "finish", icon: "check", title: "You know your way around", body: "That is the whole system. A good first move: create a resource, apply it to the table, and call it from the terminal with http get /todos. You can replay this tour from the project menu, or by typing tour in the terminal.", place: "center", last: true },
];

const KEY = "pawabase.tour.v1";

/** `{status: "new" | "running" | "paused" | "skipped" | "done", step}` */
export function readTour() {
  try {
    const value = JSON.parse(localStorage.getItem(KEY) || "null");
    if (value && typeof value.step === "number") return value;
  } catch {
    /* unreadable: treat as never seen */
  }
  return { status: "new", step: 0 };
}

export function writeTour(value) {
  try {
    localStorage.setItem(KEY, JSON.stringify({ ...value, at: Date.now() }));
  } catch {
    /* private mode: the tour then restarts on the next page, which is acceptable */
  }
}

/** Ask the mounted tour to start (the project menu, the terminal's `tour` command). */
export function startTour(detail = {}) {
  window.dispatchEvent(new CustomEvent("pawabase:tour", { detail }));
}
