import { Link } from "@inertiajs/react";
import Layout from "../../components/Layout";
import { PageHead } from "../../components/ui";

export default function NotFound({ message }) {
  return (
    <Layout title="Not found">
      <PageHead title="Not found" description={message || "That page does not exist."} actions={<Link className="btn" href="/">Back to Studio</Link>} />
    </Layout>
  );
}
