"""Dashboards: CRUD and validation against the real definition shape, publishing (sealing a publishable key
once, never returning it), and the published page serving correctly — or refusing to — against a real database.
"""

import pytest

from pawabase_server.core.clients import ServiceError

ENV = "/platform/v1/envs/development"

METRIC_DASHBOARD = {
    "name": "sales",
    "title": "Sales",
    "definition": {
        "pages": [
            {
                "id": "p1",
                "name": "Home",
                "path": "/",
                "widgets": [
                    {
                        "id": "w1",
                        "type": "metric",
                        "title": "Orders",
                        "binding": {
                            "source": "resource",
                            "resource": "orders",
                            "aggregate": "count",
                        },
                    }
                ],
            }
        ]
    },
}


async def create_dashboard(api, body=None):
    return await api.studio.post(f"{ENV}/dashboards", json=body or METRIC_DASHBOARD)


async def test_create_rejects_an_invalid_definition(api):
    bad = {
        **METRIC_DASHBOARD,
        "definition": {
            "pages": [
                {
                    "id": "p1",
                    "name": "Home",
                    "path": "/",
                    "widgets": [
                        {
                            "id": "w1",
                            "type": "metric",
                            "binding": {"source": "resource", "resource": "orders"},
                        }
                    ],
                }
            ]
        },
    }
    with pytest.raises(ServiceError) as err:
        await create_dashboard(api, bad)
    assert err.value.status == 422
    assert "aggregate" in str(err.value.body)


async def test_create_and_list(api):
    created = await create_dashboard(api)
    assert created["name"] == "sales"
    listed = await api.studio.get(f"{ENV}/dashboards")
    assert any(row["name"] == "sales" for row in listed["data"])


async def test_duplicate_name_is_refused(api):
    await create_dashboard(api)
    with pytest.raises(ServiceError) as err:
        await create_dashboard(api)
    assert err.value.status == 409


async def test_publishing_without_a_key_the_first_time_is_refused(api):
    created = await create_dashboard(api)
    with pytest.raises(ServiceError) as err:
        await api.studio.put(f"{ENV}/dashboards/{created['id']}/publish", json={"public": True})
    assert err.value.status == 422


async def test_publishing_seals_the_key_and_the_public_page_embeds_it(api):
    created = await create_dashboard(api)
    published = await api.studio.put(
        f"{ENV}/dashboards/{created['id']}/publish",
        json={"public": True, "publishable_key": "pk_test_abcdefgh"},
    )
    assert published["public"] is True

    page = await api.http.get("/dashboards/v1/development/sales")
    assert page.status_code == 200
    assert "pk_test_abcdefgh" in page.text
    assert "resource" in page.text and "orders" in page.text


async def test_unpublished_dashboard_404s(api):
    await create_dashboard(api)
    response = await api.http.get("/dashboards/v1/development/sales")
    assert response.status_code == 404


async def test_disabling_a_published_dashboard_takes_the_page_down(api):
    created = await create_dashboard(api)
    await api.studio.put(
        f"{ENV}/dashboards/{created['id']}/publish",
        json={"public": True, "publishable_key": "pk_test_abcdefgh"},
    )
    await api.studio.patch(f"{ENV}/dashboards/{created['id']}", json={"enabled": False})
    response = await api.http.get("/dashboards/v1/development/sales")
    assert response.status_code == 404


async def test_republishing_without_a_new_key_keeps_the_sealed_one(api):
    created = await create_dashboard(api)
    base = f"{ENV}/dashboards/{created['id']}/publish"
    await api.studio.put(base, json={"public": True, "publishable_key": "pk_test_abcdefgh"})
    await api.studio.put(base, json={"public": False})
    again = await api.studio.put(base, json={"public": True})
    assert again["public"] is True
    page = await api.http.get("/dashboards/v1/development/sales")
    assert page.status_code == 200 and "pk_test_abcdefgh" in page.text


async def test_missing_dashboard_404s(api):
    response = await api.http.get("/dashboards/v1/development/nothing-here")
    assert response.status_code == 404


async def test_missing_environment_404s(api):
    response = await api.http.get("/dashboards/v1/no-such-env/sales")
    assert response.status_code == 404
