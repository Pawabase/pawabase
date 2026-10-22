import { useEffect } from "react";
import { Spinner } from "../../components/ui";

// Events and flow runs now live under Activity. Old links and bookmarks land here and are sent on.
const TARGET = { events: "events", graph: "event-map", runs: "flows", mail: "mail" };

export default function Events({ env }) {
  useEffect(() => {
    const section = new URLSearchParams(window.location.search).get("section");
    window.location.replace(`/envs/${env}/activity#${TARGET[section] || "events"}`);
  }, [env]);
  return <div className="empty"><Spinner /></div>;
}
