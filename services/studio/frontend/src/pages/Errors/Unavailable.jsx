import Layout from "../../components/Layout";

export default function Unavailable({ message, service }) {
  return (
    <Layout title="Service unavailable">
      <div className="card stack" style={{ maxWidth: 560 }}>
        <h1>{service ? `The ${service} service is unavailable` : "Something went wrong"}</h1>
        <div className="alert error">{message}</div>
        <p className="muted">Check that every Pawabase service is running (<code>docker compose ps</code>), then reload.</p>
        <button className="btn" onClick={() => window.location.reload()}>Reload</button>
      </div>
    </Layout>
  );
}
