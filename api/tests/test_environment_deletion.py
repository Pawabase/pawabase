import pytest

from pawabase_core.clients import ServiceError

ENVS = "/platform/v1/envs"


async def test_deleting_an_environment_queues_the_purge_and_keeps_it_until_the_worker_is_done(api):
    await api.studio.post(ENVS, json={"name": "scratch"})
    answer = await api.studio.delete(f"{ENVS}/scratch")
    assert answer["queued"] is True and answer["job_id"]
    environment = await api.studio.get(f"{ENVS}/scratch")
    assert environment["settings"]["deletion_pending"] is True  # still there: the worker removes it

    with pytest.raises(ServiceError) as again:
        await api.studio.delete(f"{ENVS}/scratch")
    assert again.value.status == 409
