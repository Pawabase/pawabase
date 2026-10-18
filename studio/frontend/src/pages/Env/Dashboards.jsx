import Layout from "../../components/Layout";
import { Dashboards } from "./dashboards/Dashboards";

export default function DashboardsPage({ env }) {
  return (
    <Layout title="Dashboards">
      <Dashboards env={env} />
    </Layout>
  );
}
