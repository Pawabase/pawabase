"""Every worker serves every queue, and no queue is first for all of them."""

from pawabase_server.automation import worker
from pawabase_server.platform import PLATFORM_QUEUES


def test_each_busy_queue_is_first_for_some_worker(monkeypatch):
    firsts = set()
    for host in range(30):
        monkeypatch.setattr(worker.socket, "gethostname", lambda host=host: f"container-{host}")
        queues = worker.worker_queues()
        assert sorted(queues) == sorted(PLATFORM_QUEUES)
        assert queues[3:] == ["mail", "default"]  # the quiet queues stay behind the busy ones
        firsts.add(queues[0])
    assert firsts == {"events", "webhooks", "flows"}
