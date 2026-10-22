import { useState } from "react";
import { envHref } from "./Layout";
import { envPath, useApi } from "../lib/api";

// Bottom-of-sidebar tools, in order. Pinned in place (see Layout.jsx's SubNav) so a long table list never pushes them out of view.
export const DATA_TOOLS = [["database", "SQL", "database"], ["types", "Types", "code"], ["schemas", "Schemas", "schemas"], ["transformers", "Transformers", "transformers"], ["topology", "Topology", "resources"]];

// A link to the Tables page focused on one table.
export function tablesHref(env, table) {
  const base = envHref(env, "tables");
  return `${base}${base.includes("?") ? "&" : "?"}table=${encodeURIComponent(table)}`;
}

// Fetches the environment's tables once and shapes the Data secondary sidebar.
// Callers: const { subnav, tables, loading, reload } = useDataSubnav(env, active, { action });
export function useDataSubnav(env, active, { action, create, tools = DATA_TOOLS } = {}) {
  const overview = useApi(env ? envPath(env, "/database") : null);
  const [search, setSearch] = useState("");
  const tables = overview.data?.tables || [];
  const shown = tables.filter((t) => t.name.toLowerCase().includes(search.toLowerCase().trim()));
  return {
    subnav: {
      title: "Data",
      loading: overview.loading,
      empty: "This database has no tables yet.",
      search: { value: search, onChange: setSearch, placeholder: "Search tables…" },
      create,
      action,
      active,
      groups: [
        {
          title: tables.length ? `Tables \u00b7 ${tables.length}` : "Tables",
          items: shown.map((t) => ({ key: t.name, label: t.name, mono: true, icon: "resources", href: tablesHref(env, t.name), tag: t.resource ? "resource" : undefined })),
          empty: overview.loading ? undefined : shown.length ? undefined : search.trim() ? "No tables match." : "No tables yet.",
        },
        { title: "Tools", pinned: true, items: tools.map(([key, label, icon]) => ({ key, label, icon, href: envHref(env, key) })) },
      ],
    },
    tables,
    loading: overview.loading,
    reload: overview.reload,
  };
}
