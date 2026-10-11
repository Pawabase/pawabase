// The product as a catalog of services, the way a cloud console presents it. A service is a few pages that belong together.
export const CATEGORIES = [
  { id: "data", label: "Data & files", blurb: "Where your information lives" },
  { id: "compute", label: "Compute & API", blurb: "Code and the interfaces that reach it" },
  { id: "identity", label: "Identity & access", blurb: "Who can do what" },
  { id: "integration", label: "Integration & events", blurb: "Things that happen, and what reacts" },
  { id: "operations", label: "Operations", blurb: "Watching, protecting and configuring it" },
];

export const SERVICES = [
  { key: "data", category: "data", label: "Data model", icon: "data", description: "Collections on real tables, their relations, SQL and the history of schema changes.", pages: [["resources", "Collections"], ["topology", "Relations"], ["schemas", "Schemas"], ["transformers", "Transformers"], ["database", "SQL"]] },
  { key: "files", category: "data", label: "Files", icon: "files", description: "Buckets and uploads with the same access rules as your data.", pages: [["storage", "Storage"]] },
  { key: "functions", category: "compute", label: "Functions & API", icon: "functions", description: "Python functions, HTTP routes, and a console to try your API.", pages: [["functions", "Functions"], ["routes", "Routes"], ["explorer", "API Explorer"]] },
  { key: "access", category: "identity", label: "Users & access", icon: "access", description: "Accounts, organizations and roles, and the policies that decide access.", pages: [["users", "Users"], ["policies", "Policies"]] },
  { key: "secrets", category: "identity", label: "Keys & secrets", icon: "secrets", description: "API keys for apps and servers, and encrypted secrets for your code.", pages: [["keys", "API keys"], ["secrets", "Secrets"]] },
  { key: "automation", category: "integration", label: "Automation", icon: "automation", description: "Visual flows, with schedules, event triggers and webhooks.", pages: [["flows", "Flows"], ["subscriptions", "Event triggers"], ["schedules", "Schedules"], ["webhooks", "Webhooks out"], ["inbound-hooks", "Webhooks in"]] },
  { key: "events", category: "integration", label: "Queue & events", icon: "events", description: "Background jobs with retries, and everything that was emitted or run.", pages: [["jobs", "Jobs & queues"], ["events", "Events & runs"]] },
  { key: "realtime", category: "integration", label: "Realtime", icon: "realtime", description: "Channels with broadcast and presence over WebSockets.", pages: [["realtime", "Channels"]] },
  { key: "mail", category: "integration", label: "Mail", icon: "mail", description: "Templates and a delivery log for the email your app sends.", pages: [["mail", "Mail"]] },
  { key: "monitoring", category: "operations", label: "Monitoring", icon: "monitoring", description: "Requests, errors, traces, alarms, usage limits and a public status page.", pages: [["observability", "Observability"], ["usage", "Usage & limits"], ["status", "Status page"]] },
  { key: "settings", category: "operations", label: "Settings & domains", icon: "settings", description: "Environment settings, networking and domains, and backups.", pages: [["settings", "General"], ["networking", "Networking"], ["backups", "Backups"]] },
];

export function serviceOf(section) {
  if (section === "overview") return null;
  return SERVICES.find((service) => service.pages.some(([key]) => key === section)) || null;
}

const PINS = "pawabase.pins.v1";
const RECENT = "pawabase.recent.v1";

export function readPins() {
  try { return JSON.parse(localStorage.getItem(PINS) || "[]"); } catch { return []; }
}
export function writePins(pins) {
  try { localStorage.setItem(PINS, JSON.stringify(pins)); } catch { /* kept for this page only */ }
}
export function readRecent() {
  try { return JSON.parse(localStorage.getItem(RECENT) || "[]"); } catch { return []; }
}
export function remember(serviceKey) {
  if (!serviceKey) return;
  try {
    const next = [serviceKey, ...readRecent().filter((key) => key !== serviceKey)].slice(0, 5);
    localStorage.setItem(RECENT, JSON.stringify(next));
  } catch { /* nothing to remember with */ }
}
