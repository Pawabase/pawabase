"""Studio manages an environment's organizations: create, rename, members, delete."""

import pytest

from pawabase_core.clients import ServiceError

BASE = "/admin/v1/envs/development"


async def _user(akz, email):
    return await akz.admin.post(f"{BASE}/users", json={"email": email, "password": "correct-horse-1"})


async def test_an_operator_creates_and_manages_an_organization(akz):
    ada = await _user(akz, "ada@example.com")
    bob = await _user(akz, "bob@example.com")

    await akz.admin.post(f"{BASE}/orgs", json={"slug": "acme", "name": "Acme", "owner_email": "ada@example.com"})
    with pytest.raises(ServiceError) as taken:
        await akz.admin.post(f"{BASE}/orgs", json={"slug": "acme", "name": "Again"})
    assert taken.value.status == 409

    await akz.admin.post(f"{BASE}/orgs/acme/members", json={"email": "bob@example.com", "role": "admin"})
    detail = await akz.admin.get(f"{BASE}/orgs/acme")
    assert {m["email"]: m["role"] for m in detail["members"]} == {"ada@example.com": "owner", "bob@example.com": "admin"}

    await akz.admin.patch(f"{BASE}/orgs/acme/members/{bob['id']}", json={"role": "viewer"})
    await akz.admin.patch(f"{BASE}/orgs/acme", json={"name": "Acme Inc"})
    assert (await akz.admin.get(f"{BASE}/orgs/acme"))["name"] == "Acme Inc"

    # The only owner cannot be demoted or removed.
    with pytest.raises(ServiceError) as demote:
        await akz.admin.patch(f"{BASE}/orgs/acme/members/{ada['id']}", json={"role": "member"})
    assert demote.value.status == 422
    with pytest.raises(ServiceError) as leave:
        await akz.admin.delete(f"{BASE}/orgs/acme/members/{ada['id']}")
    assert leave.value.status == 422

    await akz.admin.delete(f"{BASE}/orgs/acme/members/{bob['id']}")
    await akz.admin.delete(f"{BASE}/orgs/acme")
    assert (await akz.admin.get(f"{BASE}/orgs"))["data"] == []
