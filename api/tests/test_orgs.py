import pytest

from pawabase_core.clients import ServiceError

P = "/platform/v1"


def operator(sub: str, email: str) -> dict:
    return {"sub": sub, "email": email, "roles": ["admin"]}


ADA = operator("1", "ada@example.com")
BOB = operator("2", "bob@example.com")
CAT = operator("3", "cat@example.com")


async def make_org(api, who, slug="acme", name="Acme"):
    return await api.studio.post(f"{P}/orgs", json={"slug": slug, "name": name}, operator=who)


async def refuses(status, call):
    with pytest.raises(ServiceError) as raised:
        await call
    assert raised.value.status == status


async def test_creator_owns_the_new_organization(api):
    org = await make_org(api, ADA)
    assert org["role"] == "owner"
    mine = await api.studio.get(f"{P}/orgs", operator=ADA)
    assert [o["slug"] for o in mine["data"]] == ["acme"]
    assert (await api.studio.get(f"{P}/orgs", operator=BOB))["data"] == []
    await refuses(409, make_org(api, BOB))


async def test_first_organization_adopts_existing_projects(api):
    from database.models import Organization, Project

    await api.studio.post(f"{P}/projects", json={"ref": "legacy", "name": "Legacy"})
    await Project.filter(ref="legacy").update(organization_id=None)
    await Organization.all().delete()  # a database from before organizations existed
    await make_org(api, ADA)
    project = await api.studio.get(f"{P}/projects/legacy", operator=ADA)
    assert project["org"] == "acme"


async def test_services_put_projects_in_the_default_organization(api):
    created = await api.studio.post(f"{P}/projects", json={"ref": "shop", "name": "Shop"})
    assert created["org"] == "default"


async def test_operators_must_choose_an_organization(api):
    await make_org(api, ADA)
    await refuses(
        422, api.studio.post(f"{P}/projects", json={"ref": "shop", "name": "Shop"}, operator=ADA)
    )
    made = await api.studio.post(
        f"{P}/projects", json={"ref": "shop", "name": "Shop", "org": "acme"}, operator=ADA
    )
    assert made["org"] == "acme"


async def test_projects_are_invisible_outside_their_organization(api):
    await make_org(api, ADA)
    await make_org(api, BOB, "bobs", "Bobs")
    await api.studio.post(
        f"{P}/projects", json={"ref": "shop", "name": "Shop", "org": "acme"}, operator=ADA
    )
    assert [p["ref"] for p in (await api.studio.get(f"{P}/projects", operator=ADA))["data"]] == [
        "shop"
    ]
    assert (await api.studio.get(f"{P}/projects", operator=BOB))["data"] == []
    # Reading, changing and reaching deep into another organization's project.
    for path in ("", "/envs", "/envs/development/keys", "/envs/development/overview"):
        await refuses(404, api.studio.get(f"{P}/projects/shop{path}", operator=BOB))
    await refuses(
        404,
        api.studio.post(
            f"{P}/projects/shop/envs/development/schemas",
            json={"name": "x", "fields": []},
            operator=BOB,
        ),
    )
    await refuses(404, api.studio.delete(f"{P}/projects/shop", operator=BOB))
    await refuses(404, api.studio.get(f"{P}/orgs/acme", operator=BOB))
    await refuses(
        404,
        api.studio.post(
            f"{P}/projects", json={"ref": "sneak", "name": "S", "org": "acme"}, operator=BOB
        ),
    )
    stats = await api.studio.get(f"{P}/overview", operator=BOB)
    assert stats["projects"] == 0


async def test_roles_limit_what_members_can_do(api):
    await make_org(api, ADA)
    await api.studio.post(
        f"{P}/projects", json={"ref": "shop", "name": "Shop", "org": "acme"}, operator=ADA
    )
    invite = await api.studio.post(
        f"{P}/orgs/acme/invitations",
        json={"email": "bob@example.com", "role": "viewer"},
        operator=ADA,
    )
    await api.studio.post(f"{P}/invitations/{invite['token']}/accept", operator=BOB)
    # A viewer reads but changes nothing.
    assert (await api.studio.get(f"{P}/projects/shop", operator=BOB))["ref"] == "shop"
    await refuses(
        403,
        api.studio.patch(f"{P}/projects/shop", json={"name": "Renamed"}, operator=BOB),
    )
    await refuses(403, api.studio.get(f"{P}/orgs/acme/invitations", operator=BOB))
    await refuses(403, api.studio.delete(f"{P}/projects/shop", operator=BOB))
    # A developer changes projects but does not delete them or manage people.
    await api.studio.put(f"{P}/orgs/acme/members/2", json={"role": "developer"}, operator=ADA)
    renamed = await api.studio.patch(f"{P}/projects/shop", json={"name": "Renamed"}, operator=BOB)
    assert renamed["name"] == "Renamed"
    await refuses(403, api.studio.delete(f"{P}/projects/shop", operator=BOB))
    await refuses(
        403, api.studio.put(f"{P}/orgs/acme/members/2", json={"role": "admin"}, operator=BOB)
    )
    # An admin deletes projects.
    await api.studio.put(f"{P}/orgs/acme/members/2", json={"role": "admin"}, operator=ADA)
    await api.studio.delete(f"{P}/projects/shop", operator=BOB)


async def test_an_organization_keeps_an_owner(api):
    await make_org(api, ADA)
    await refuses(
        409, api.studio.put(f"{P}/orgs/acme/members/1", json={"role": "admin"}, operator=ADA)
    )
    await refuses(409, api.studio.delete(f"{P}/orgs/acme/members/1", operator=ADA))


async def test_only_owners_grant_ownership(api):
    await make_org(api, ADA)
    invite = await api.studio.post(
        f"{P}/orgs/acme/invitations",
        json={"email": "bob@example.com", "role": "admin"},
        operator=ADA,
    )
    await api.studio.post(f"{P}/invitations/{invite['token']}/accept", operator=BOB)
    await refuses(
        403, api.studio.put(f"{P}/orgs/acme/members/2", json={"role": "owner"}, operator=BOB)
    )
    await refuses(403, api.studio.delete(f"{P}/orgs/acme/members/1", operator=BOB))
    await refuses(
        403,
        api.studio.post(
            f"{P}/orgs/acme/invitations",
            json={"email": "c@example.com", "role": "owner"},
            operator=BOB,
        ),
    )
    await api.studio.put(f"{P}/orgs/acme/members/2", json={"role": "owner"}, operator=ADA)
    await api.studio.put(f"{P}/orgs/acme/members/1", json={"role": "admin"}, operator=BOB)


async def test_invitations(api):
    await make_org(api, ADA)
    invite = await api.studio.post(
        f"{P}/orgs/acme/invitations", json={"email": "Bob@Example.com"}, operator=ADA
    )
    token = invite["token"]
    # The link previews without signing in, and only the hash is stored.
    preview = await api.studio.get(f"{P}/invitations/{token}")
    assert preview["organization"] == "Acme" and preview["email"] == "bob@example.com"
    from database.models import OrgInvitation

    assert (await OrgInvitation.get(id=invite["id"])).token_hash != token
    pending = await api.studio.get(f"{P}/orgs/acme/invitations", operator=ADA)
    assert [i["email"] for i in pending["data"]] == ["bob@example.com"]
    # Someone else cannot take it; the invitee can, once.
    await refuses(403, api.studio.post(f"{P}/invitations/{token}/accept", operator=CAT))
    joined = await api.studio.post(f"{P}/invitations/{token}/accept", operator=BOB)
    assert joined["role"] == "developer"
    await refuses(404, api.studio.get(f"{P}/invitations/{token}"))
    members = await api.studio.get(f"{P}/orgs/acme/members", operator=BOB)
    assert sorted(m["email"] for m in members["data"]) == ["ada@example.com", "bob@example.com"]
    await refuses(
        409,
        api.studio.post(
            f"{P}/orgs/acme/invitations", json={"email": "bob@example.com"}, operator=ADA
        ),
    )


async def test_revoked_and_replaced_invitations_stop_working(api):
    await make_org(api, ADA)
    first = await api.studio.post(
        f"{P}/orgs/acme/invitations", json={"email": "bob@example.com"}, operator=ADA
    )
    second = await api.studio.post(
        f"{P}/orgs/acme/invitations", json={"email": "bob@example.com"}, operator=ADA
    )
    await refuses(404, api.studio.get(f"{P}/invitations/{first['token']}"))
    await api.studio.delete(f"{P}/orgs/acme/invitations/{second['id']}", operator=ADA)
    await refuses(404, api.studio.post(f"{P}/invitations/{second['token']}/accept", operator=BOB))


async def test_leaving_and_deleting(api):
    await make_org(api, ADA)
    invite = await api.studio.post(
        f"{P}/orgs/acme/invitations", json={"email": "bob@example.com"}, operator=ADA
    )
    await api.studio.post(f"{P}/invitations/{invite['token']}/accept", operator=BOB)
    await api.studio.delete(f"{P}/orgs/acme/members/2", operator=BOB)
    await refuses(404, api.studio.get(f"{P}/orgs/acme", operator=BOB))
    await api.studio.post(
        f"{P}/projects", json={"ref": "shop", "name": "Shop", "org": "acme"}, operator=ADA
    )
    await refuses(409, api.studio.delete(f"{P}/orgs/acme", operator=ADA))
    await api.studio.delete(f"{P}/projects/shop", operator=ADA)
    await api.studio.delete(f"{P}/orgs/acme", operator=ADA)
    assert (await api.studio.get(f"{P}/orgs", operator=ADA))["data"] == []


async def test_audit_is_scoped_to_the_operators_organizations(api):
    await make_org(api, ADA)
    await make_org(api, BOB, "bobs", "Bobs")
    await api.studio.post(
        f"{P}/projects", json={"ref": "shop", "name": "Shop", "org": "acme"}, operator=ADA
    )
    ada = await api.studio.get(f"{P}/audit", operator=ADA)
    assert {e["org"] for e in ada["data"]} == {"acme"}
    assert {"org.created", "project.created"} <= {e["action"] for e in ada["data"]}
    bob = await api.studio.get(f"{P}/audit", operator=BOB)
    assert {e["org"] for e in bob["data"]} == {"bobs"}
    await refuses(404, api.studio.get(f"{P}/audit", params={"org": "acme"}, operator=BOB))
