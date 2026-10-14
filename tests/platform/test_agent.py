"""The runtime agent: what it will carry out, once, and what it refuses."""

import asyncio
import json
import time

import httpx
import pytest

from pawabase_server.agent import protocol
from pawabase_server.agent.executor import Executor
from pawabase_server.agent.runner import Agent, ConfigurationError, check_configuration
from pawabase_server.core.clients import ServiceError
from pawabase_server.db.models import AgentOperation, Environment, Project, ProjectDatabaseBinding

SECRET = "agent-secret-for-tests-0123456789abcdef"
POOL = "pool-lagos-1"


class FakePlane:
    """The control plane, in the test: it hands out jobs and records what was reported."""

    def __init__(self, jobs=None, down=False):
        self.jobs = list(jobs or [])
        self.reports = []
        self.down = down

    async def poll(self):
        if self.down:
            raise httpx.ConnectError("control plane unreachable")
        jobs, self.jobs = self.jobs, []
        return jobs

    async def report(self, job, *, ok, result=None, error="", retryable=False):
        self.reports.append(
            {"id": job["id"], "ok": ok, "result": result, "error": error, "retryable": retryable}
        )

    async def close(self):
        pass


def job(
    kind,
    project_id="01JCLOUDPROJECT0000000001",
    *,
    id=None,
    payload=None,
    pool=POOL,
    expires=None,
    attempt=1,
):
    body = {
        "id": id or f"op-{kind}-{time.time_ns()}",
        "kind": kind,
        "pool": pool,
        "project_id": project_id,
        "payload": payload or {},
        "expires_at": expires or time.time() + 300,
        "attempt": attempt,
    }
    body["sig"] = protocol.sign_job(SECRET, body)
    return body


@pytest.fixture
def agent_settings(settings, monkeypatch, tmp_path):
    settings.agent_url = "https://cloud.test"
    settings.agent_id = "agent-1"
    settings.agent_api_key = "pb_pk_control_plane_key"
    settings.agent_token = SECRET
    settings.agent_pool = POOL
    settings.agent_secret_seed = "seed-for-derived-role-passwords-0123456789"
    monkeypatch.setenv(
        "PAWABASE_AGENT_DATABASES",
        json.dumps({"pool-db": f"sqlite://{tmp_path}/{{database}}/{{{{env}}}}.db"}),
    )
    return settings


def make_agent(settings, jobs=None, down=False):
    plane = FakePlane(jobs, down)
    return Agent(settings, plane=plane, executor=Executor(settings)), plane


# ── the protocol ─────────────────────────────────────────────────────────


def test_a_request_is_only_believed_with_its_signature_and_a_fresh_time():
    envelope = protocol.sign_request(SECRET, "agent-1", {"pool": POOL})
    assert protocol.verify_request(SECRET, envelope) == {"pool": POOL}
    with pytest.raises(protocol.ProtocolError):
        protocol.verify_request("another-secret", envelope)
    tampered = {**envelope, "body": {"pool": "pool-other"}}
    with pytest.raises(protocol.ProtocolError):
        protocol.verify_request(SECRET, tampered)
    with pytest.raises(protocol.ProtocolError):  # a recorded message, played back later
        protocol.verify_request(SECRET, envelope, now=time.time() + 3600)


def test_an_instruction_must_be_signed_allowed_unexpired_and_for_this_pool():
    good = job("project.create", payload={"name": "Shop", "slug": "shop"})
    assert protocol.verify_job(SECRET, good, pool=POOL)["kind"] == "project.create"
    for bad, why in (
        ({**good, "payload": {"name": "Evil", "slug": "evil"}}, "signature"),
        (job("project.create", pool="pool-other"), "pool"),
        (job("project.create", expires=time.time() - 5), "expired"),
        (job("shell.run"), "not an operation"),
    ):
        with pytest.raises(protocol.ProtocolError, match=why.split()[0]):
            protocol.verify_job(SECRET, bad, pool=POOL)


def test_the_agent_will_not_start_misconfigured(settings):
    settings.agent_url = "http://cloud.example.com"
    settings.agent_id, settings.agent_token, settings.agent_pool = "a", "t", "p"
    settings.agent_api_key = "k"
    with pytest.raises(ConfigurationError, match="https"):
        check_configuration(settings)
    settings.agent_url = ""
    with pytest.raises(ConfigurationError, match="AGENT_URL"):
        check_configuration(settings)


# ── provisioning ─────────────────────────────────────────────────────────


async def test_a_project_is_provisioned_from_instructions_with_no_password_in_them(
    api, agent_settings
):
    pid = "01JCLOUDPROJECT0000000001"
    jobs = [
        job("project.create", pid, id="op-1", payload={"name": "Shop", "slug": "shop"}),
        job(
            "project.bind_database",
            pid,
            id="op-2",
            payload={
                "database": {
                    "mode": "managed",
                    "host": "pool-db",
                    "database": "shop_db",
                    "role": "shop_role",
                }
            },
        ),
        job("environment.create", pid, id="op-3", payload={"slug": "development"}),
        job("project.status", pid, id="op-4"),
    ]
    agent, plane = make_agent(agent_settings, jobs)
    assert await agent.step() == 4
    assert [r["ok"] for r in plane.reports] == [True, True, True, True]
    status = plane.reports[-1]["result"]
    assert status["ready"] is True
    project = await Project.get(id=pid)
    assert project.slug == "shop" and project.origin == "cloud" and project.external_ref == pid
    binding = await ProjectDatabaseBinding.get(project_id=pid)
    assert binding.mode == "managed" and "shop_role" not in binding.connection_ref
    environment = await Environment.get(project_id=pid, slug="development")
    assert environment.status == "ready" and environment.name == "shop-development"
    # The keys were made but not sent back: the control plane did not ask for them.
    assert plane.reports[2]["result"]["keys"] == {"publishable": "(created)", "secret": "(created)"}


async def test_a_project_instruction_can_name_the_projects_bucket(api, agent_settings):
    pid = "01JCLOUDPROJECT0000000002"
    executor = Executor(agent_settings)
    await executor.execute(
        job(
            "project.create",
            pid,
            id="op-b",
            payload={"name": "Bucketed", "slug": "bucketed", "storage": {"bucket": "pb-bucketed"}},
        )
    )
    project = await Project.get(id=pid)
    assert project.details["storage"] == {"bucket": "pb-bucketed"}
    with pytest.raises(Exception, match="bucket name"):
        await executor.execute(
            job(
                "project.create",
                "01JCLOUDPROJECT0000000003",
                id="op-c",
                payload={"name": "Bad", "slug": "bad", "storage": {"bucket": "../x"}},
            )
        )


async def test_a_signed_instruction_can_be_pushed_to_the_runtime(api, agent_settings):
    pid = "01JCLOUDPROJECT0000000004"
    pushed = job("project.create", pid, id="op-push", payload={"name": "Pushed", "slug": "pushed"})
    answer = await api.studio.post("/platform/v1/agent/execute", json={"job": pushed})
    assert answer["ok"] is True
    assert (await Project.get(id=pid)).slug == "pushed"
    forged = {**pushed, "id": "op-forged", "payload": {"name": "X", "slug": "x"}}
    with pytest.raises(Exception, match="signature"):
        await api.studio.post("/platform/v1/agent/execute", json={"job": forged})


async def test_a_pushed_delete_purges_environments_before_unregistering(api, agent_settings):
    pid = "01JCLOUDPROJECT0000000005"
    steps = [
        ("project.create", {"name": "Gone", "slug": "gone"}),
        (
            "project.bind_database",
            {
                "database": {
                    "mode": "managed",
                    "host": "pool-db",
                    "database": "gone_db",
                    "role": "gone_role",
                }
            },
        ),
        ("environment.create", {"slug": "development"}),
    ]
    for n, (kind, payload) in enumerate(steps):
        await api.studio.post(
            "/platform/v1/agent/execute",
            json={"job": job(kind, pid, id=f"del-{n}", payload=payload)},
        )
    assert await Environment.filter(project_id=pid).count() == 1
    first = job("project.delete", pid, id="del-x", payload={"purge": True})
    answer = None
    with pytest.raises(ServiceError):
        answer = await api.studio.post("/platform/v1/agent/execute", json={"job": first})
    assert answer is None
    await api.drain()
    assert await Environment.filter(project_id=pid).count() == 0
    again = job("project.delete", pid, id="del-x", payload={"purge": True})
    done = await api.studio.post("/platform/v1/agent/execute", json={"job": again})
    assert done["ok"] is True
    assert await Project.filter(id=pid).count() == 0


async def test_an_instruction_done_twice_is_answered_not_done_again(api, agent_settings):
    create = job("project.create", id="op-once", payload={"name": "Shop", "slug": "shop"})
    agent, plane = make_agent(agent_settings, [create])
    await agent.step()
    plane.jobs = [create]
    await agent.step()
    assert [r["ok"] for r in plane.reports] == [True, True]
    assert plane.reports[1]["result"].get("replayed") is True
    assert await Project.filter(slug="shop").count() == 1
    assert (await AgentOperation.get(id="op-once")).status == "done"


async def test_what_the_agent_refuses_is_reported_and_changes_nothing(api, agent_settings):
    pid = "01JCLOUDPROJECT0000000002"
    tampered = job("project.create", pid, id="op-t", payload={"name": "Shop", "slug": "shop"})
    tampered["payload"] = {"name": "Evil", "slug": "evil"}
    other_pool = job(
        "project.create",
        pid,
        id="op-p",
        payload={"name": "Shop", "slug": "shop"},
        pool="pool-other",
    )
    bad_host = job(
        "project.bind_database",
        pid,
        id="op-h",
        payload={"database": {"mode": "managed", "host": "unknown", "database": "d", "role": "r"}},
    )
    with_password = job(
        "project.bind_database",
        pid,
        id="op-w",
        payload={
            "database": {"mode": "managed", "host": "pool-db", "url": "postgres://u:secret@h/d"}
        },
    )
    agent, plane = make_agent(agent_settings, [tampered, other_pool, bad_host, with_password])
    assert await agent.step() == 0
    assert all(not r["ok"] for r in plane.reports) and len(plane.reports) == 4
    assert not await Project.filter(slug__in=["shop", "evil"]).exists()
    assert "secret" not in json.dumps(plane.reports)


async def test_the_agent_survives_an_unreachable_control_plane_and_requests_do_not_need_it(
    api, agent_settings
):
    agent_settings.agent_poll_seconds = 0.01
    agent, plane = make_agent(agent_settings, down=True)
    stop = asyncio.Event()
    task = asyncio.create_task(agent.run(stop))
    await asyncio.sleep(0.15)
    assert not task.done()  # it keeps trying
    # Meanwhile the application answers as usual.
    assert (await api.http.get("/health")).status_code == 200
    health = await api.studio.get("/platform/v1/envs/development")
    assert health["name"] == "development"
    # And when the control plane comes back, the next round is served.
    plane.down = False
    plane.jobs = [job("project.create", id="op-late", payload={"name": "Late", "slug": "late"})]
    for _ in range(100):
        if plane.reports:
            break
        await asyncio.sleep(0.05)
    stop.set()
    await task
    assert plane.reports and plane.reports[0]["ok"]


async def test_the_default_project_cannot_be_deleted_by_an_instruction(api, agent_settings):
    default = await Project.get(slug="default")
    agent, plane = make_agent(agent_settings, [job("project.delete", default.id, id="op-d")])
    await agent.step()
    assert plane.reports[0]["ok"] is False and "default" in plane.reports[0]["error"]


def test_core_and_cloud_sign_and_derive_the_same_way():
    # The same vector is asserted in Pawabase Cloud's tests (cloudlib.agent_protocol, cloudlib.pools).
    envelope = protocol.sign_request(
        "vector-secret", "agent-1", {"pool": "p", "capacity": 4}, now=1760000000, nonce="n0nce"
    )
    assert envelope["sig"] == "deafb2144b86508fad2af714c3105933ec4300980f1a9cdad2a194abd73e91fb"
    from pawabase_server.agent.executor import derive_password

    assert derive_password("shared-seed", "p_abc123") == "12e73344e3c82357d1c866741ff3bc061039eeec"
