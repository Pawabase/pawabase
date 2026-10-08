import { Button, Json, Modal, useAction } from "../../../components/ui";
import { post } from "../../../lib/api";

export default function JobDetail({ base, job, onClose }) {
  const [run] = useAction();
  return (
    <Modal wide title={`Job ${job.job}`} onClose={onClose} footer={["failed", "retrying"].includes(job.status) && <Button variant="primary" onClick={async () => { if (await run(() => post(`${base}/jobs/${job.id}/retry`), "Job re-queued")) onClose(); }}>Retry</Button>}>
      <Json value={job} />
    </Modal>
  );
}
