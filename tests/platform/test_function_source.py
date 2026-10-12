"""Reading the code that is being served, and finding where a function is defined."""

import pytest
from tests.platform.test_function_branches import bundle, env, fn


@pytest.fixture
async def deployed(api):
    await api.studio.post(
        f"{env()}/function-deployments",
        json={
            "archive": bundle(
                **{
                    "functions/orders.py": fn("orders.total", "ok"),
                    "functions/notes.md": "# notes\n",
                }
            )
        },
    )
    return api


async def test_the_tree_lists_text_files_and_names_the_deployment(deployed):
    tree = await deployed.studio.get(f"{env()}/function-source")
    paths = [item["path"] for item in tree["files"]]
    assert "functions/orders.py" in paths and "functions/notes.md" in paths
    assert tree["deployment_id"] and tree["checksum"] and tree["mounted"] is False


async def test_a_file_can_be_read_and_nothing_outside_the_code_can(deployed):
    got = await deployed.studio.get(
        f"{env()}/function-source/file", params={"path": "functions/orders.py"}
    )
    assert '@function("orders.total"' in got["content"] and got["lines"] > 3
    from pawabase_server.core.clients import ServiceError

    for path in ("../../etc/passwd", "/etc/passwd", ".pawabase-stamp", "functions/missing.py"):
        with pytest.raises(ServiceError) as refused:
            await deployed.studio.get(f"{env()}/function-source/file", params={"path": path})
        assert refused.value.status == 404


async def test_functions_say_which_file_and_line_define_them(deployed):
    listed = await deployed.studio.get(f"{env()}/functions")
    orders = next(item for item in listed["data"] if item["name"] == "orders.total")
    assert orders["file"] == "functions/orders.py" and orders["line"] >= 1
