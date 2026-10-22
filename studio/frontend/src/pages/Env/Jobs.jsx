import { useEffect } from "react";
import { Spinner } from "../../components/ui";

// Background jobs now live under Activity. Old links and bookmarks land here and are sent on.
export default function Jobs({ env }) {
  useEffect(() => { window.location.replace(`/envs/${env}/activity#jobs`); }, [env]);
  return <div className="empty"><Spinner /></div>;
}
