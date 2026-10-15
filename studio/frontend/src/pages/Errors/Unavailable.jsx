import Layout from "../../components/Layout";
import { PageHead } from "../../components/ui";

export default function Unavailable({ message, service }) {
  return (
    <Layout title="Service unavailable">
      <PageHead
        title={service ? `The ${service} service is unavailable` : "Something went wrong"}
        description={<>Check that every Pawabase service is running (<code>docker compose ps</code>), then reload.</>}
        actions={<button className="btn" onClick={() => window.location.reload()}>Reload</button>}
      />
      {message && <div className="alert error">{message}</div>}
    </Layout>
  );
}
