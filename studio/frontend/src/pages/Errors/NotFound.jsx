import { Link } from "@inertiajs/react";
import Layout from "../../components/Layout";

export default function NotFound({ message }) {
  return (
    <Layout title="Not found">
      <div className="card stack" style={{ maxWidth: 520 }}>
        <h1>Not found</h1>
        <p className="muted">{message || "That page does not exist."}</p>
        <Link className="btn" href="/">Back to projects</Link>
      </div>
    </Layout>
  );
}
