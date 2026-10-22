import { useState } from "react";
import Layout, { sectionNav, useSection } from "../../components/Layout";
import { Channels, Connections, Overview, Playground, Protocols, Subscriptions } from "./realtime/Realtime";
import RealtimeSettings from "../../components/settings/RealtimeSettings";
import { useRealtimeSocket } from "../../lib/realtime";

export default function Realtime({ env }) {
  const [tab, setTab] = useSection(["overview", "channels", "connections", "subscriptions", "protocols", "settings", "playground"], "overview");
  const [watching, setWatching] = useState(null);
  const socket = useRealtimeSocket(env);
  const watch = (channel) => { setWatching(channel); setTab("playground"); };
  return (
    <Layout title="Realtime" subnav={sectionNav("Realtime", [
      { title: "Monitor", items: [
        ["overview", "Overview", null, { icon: "overview" }],
        ["connections", "Connections", null, { icon: "users" }],
      ] },
      { title: "Configure", items: [
        ["channels", "Channels", null, { icon: "layers" }],
        ["subscriptions", "Subscriptions", null, { icon: "subscriptions" }],
        ["protocols", "Protocols", null, { icon: "link" }],
        ["settings", "Settings", null, { icon: "settings" }],
      ] },
      { title: "Tools", items: [["playground", "Playground", null, { icon: "play" }]] },
    ], tab, setTab)} full={tab === "playground"}>
      {tab === "overview" && <Overview env={env} socket={socket} />}
      {tab === "channels" && <Channels env={env} onWatch={watch} />}
      {tab === "connections" && <Connections env={env} />}
      {tab === "subscriptions" && <Subscriptions env={env} onWatch={watch} />}
      {tab === "protocols" && <Protocols env={env} />}
      {tab === "settings" && <div className="mail-page"><RealtimeSettings env={env} /></div>}
      {tab === "playground" && <Playground env={env} socket={socket} initialChannel={watching} onConsumedInitial={() => setWatching(null)} />}
    </Layout>
  );
}
