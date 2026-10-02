"""Sign in to Studio's Akountz, create the project, and hand back the API keys.

The blueprint can only be applied by *creating a project*, and only an operator
can do that. This script does exactly what Studio's "New project → A blueprint"
button does, in one call:

1. sign in to Akountz with the operator credentials from the environment
   (``PAWABASE_ADMIN_EMAIL`` / ``PAWABASE_ADMIN_PASSWORD``, as shipped in
   ``.env`` for local use);
2. mint a service token that *names* that operator, so the API's audit log
   records who created what;
3. ``POST /platform/v1/projects`` with the blueprint — which creates every
   environment, its publishable and secret keys, all 198 definitions and the
   sample store;
4. print the project ref, environment and the two keys (shown only once, by
   design) so the web UI can be wired to them.

Run it from the host, inside the compose network, with the blueprint piped in:

    docker compose cp examples/blueprints/omnistore/bootstrap.py api:/tmp/bootstrap.py
    cat examples/blueprints/omnistore/omnistore.blueprint.json \
        | docker compose exec -T api python /tmp/bootstrap.py

Add ``--reset`` to delete the project first, so the run is repeatable.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from pawabase_core.clients import ServiceClient, ServiceError
from pawabase_core.context import PlatformContext
from pawabase_core.settings import PLATFORM_ENV, PLATFORM_PROJECT

PLATFORM_CONTEXT = PlatformContext(
    project=PLATFORM_PROJECT, env=PLATFORM_ENV, role="anon", key_id="studio"
)


def claims_of(token: str) -> dict:
    """The JWT payload, without verifying it — Akountz already signed it."""
    import base64

    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))


async def release(api: ServiceClient, operator: dict, ref: str, env: str) -> str:
    """Snapshot → version → release → activate, so custom routes serve traffic.

    Definitions only reach the data plane through an *active release*: the
    gateway compiles the release's revision, and only then are ``/storefront/*``
    routes and flows live. Studio's "Release" button does exactly this.
    """
    revision = await api.request(
        "POST", f"/platform/v1/projects/{ref}/envs/{env}/branches/main/revisions",
        operator=operator, json={"message": "OmniStore blueprint"},
    )
    if revision.get("problems"):
        raise SystemExit(f"the revision is not valid: {revision['problems']}")

    versions = await api.request(
        "GET", f"/platform/v1/projects/{ref}/envs/{env}/api-versions", operator=operator
    )
    if not any(v["name"] == "v1" for v in versions.get("data", [])):
        await api.request(
            "POST", f"/platform/v1/projects/{ref}/envs/{env}/api-versions", operator=operator,
            json={"name": "v1", "is_default": True},
        )

    made = await api.request(
        "POST", f"/platform/v1/projects/{ref}/envs/{env}/releases", operator=operator,
        json={
            "revision_id": revision["id"], "api_version": "v1", "name": "v1.0",
            "notes": "OmniStore commerce blueprint", "allow_breaking": True,
        },
    )
    await api.request(
        "POST", f"/platform/v1/projects/{ref}/envs/{env}/releases/{made['id']}/activate",
        operator=operator,
    )
    return made["id"]


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("blueprint", nargs="?", help="path to the blueprint (default: stdin)")
    parser.add_argument("--ref", default="omnistore", help="project ref to create")
    parser.add_argument("--name", default="OmniStore", help="project name")
    parser.add_argument("--env", default="development", help="environment name")
    parser.add_argument("--store", default="omnistore-demo", help="store slug if the blueprint has no sample store")
    parser.add_argument("--reset", action="store_true", help="delete the project first if it exists")
    args = parser.parse_args()

    if args.blueprint:
        blueprint = json.loads(open(args.blueprint).read())
    else:
        blueprint = json.load(sys.stdin)

    secret = os.environ["PAWABASE_INTERNAL_SECRET"]
    email = os.environ["PAWABASE_ADMIN_EMAIL"]
    password = os.environ["PAWABASE_ADMIN_PASSWORD"]
    akountz = ServiceClient(
        os.environ["PAWABASE_AKOUNTZ_URL"], secret=secret, issuer="studio", audience="akountz"
    )
    api = ServiceClient(
        os.environ["PAWABASE_API_URL"], secret=secret, issuer="studio", audience="api", timeout=180.0
    )

    # 1. sign in the way Studio does
    tokens = await akountz.post(
        "/auth/v1/token",
        json={"grant_type": "password", "email": email, "password": password},
        context=PLATFORM_CONTEXT,
    )
    if tokens.get("mfa_required"):
        print("this operator has MFA enrolled; sign in through Studio first", file=sys.stderr)
        return 2
    claims = claims_of(tokens["access_token"])
    operator = {"sub": str(claims.get("sub")), "email": claims.get("email"), "roles": claims.get("roles") or []}
    print(f"signed in as {operator['email']} ({', '.join(operator['roles']) or 'no roles'})")

    # 2. an existing project is left alone unless --reset says otherwise
    existing = await api.request("GET", "/platform/v1/projects", operator=operator)
    found = next((p for p in existing.get("data", []) if p["ref"] == args.ref), None)
    if args.reset:
        # Clear the demo rows through the API rather than deleting the data file:
        # the running platform holds that file open, so a file delete would be
        # replayed out of the WAL on the next open and the rows would return.
        if found:
            base = f"/platform/v1/projects/{args.ref}/envs/{args.env}"
            for item in blueprint["definitions"]["resources"]:
                name = item["name"]
                rows = await api.request(
                    "GET", f"{base}/resources/{name}/records", operator=operator,
                    params={"per_page": 200},
                )
                for row in rows.get("data", []):
                    await api.request(
                        "DELETE", f"{base}/resources/{name}/records/{row['id']}", operator=operator
                    )
            print(f"deleted existing project {args.ref} and its rows")
            await api.request("DELETE", f"/platform/v1/projects/{args.ref}", operator=operator)
        found = None
    if found:
        print(f"project {args.ref} already exists — pass --reset to recreate it", file=sys.stderr)
        return 3

    # 3. create it from the blueprint
    created = await api.request(
        "POST",
        "/platform/v1/projects",
        operator=operator,
        json={
            "ref": args.ref,
            "name": args.name,
            "environments": [args.env],
            "blueprint": blueprint,
        },
    )
    store_slug = next(
        (row.get("ref") for row in blueprint["data"].get("stores", []) if row.get("ref")),
        args.store,
    )
    report = created.get("blueprint", {})
    keys = created.get("keys", {}).get(args.env, {})

    # 4. give every table its schema, then cut a release so the routes go live
    base = f"/platform/v1/projects/{args.ref}/envs/{args.env}"
    resources = [item["name"] for item in blueprint["definitions"]["resources"]]
    for name in sorted(resources):
        await api.request("POST", f"{base}/resources/{name}/migrate", operator=operator)
    print(f"migrated {len(resources)} tables")
    release_id = await release(api, operator, args.ref, args.env)
    print(f"activated release {release_id}")

    print(
        f"created {args.ref}/{args.env}: "
        f"{sum(v for v in report.get('definitions', {}).values())} definitions, "
        f"{report.get('data_environment') or 'no'} data"
    )
    for warning in report.get("warnings", []):
        print(f"  warning: {warning}")

    out = {
        "project": args.ref,
        "env": args.env,
        "gateway": os.environ.get("PAWABASE_PUBLIC_GATEWAY_URL", "http://localhost:8080"),
        "store": store_slug,
        "publishable_key": keys.get("publishable"),
        "secret_key": keys.get("secret"),
    }
    json.dump(out, sys.stdout, indent=2)
    print()

    # 5. wire the web UI to the keys that were just issued — but only when this
    # script is running from the blueprint directory. Inside the api container
    # (where it usually runs) there is no web/ to write to, so print the
    # one-liner to run on the host instead.
    here = Path(__file__).resolve().parent
    if (here / "web").is_dir():
        config = here / "web" / "config.js"
        config.write_text(
            "/* Generated by bootstrap.py — the publishable key is safe to ship. */\n"
            "window.OMNISTORE = " + json.dumps({
                "gateway": out["gateway"],
                "store": out["store"],
                "publishableKey": out["publishable_key"],
            }, indent=2) + ";\n"
        )
        print(f"wrote {config} — open web/index.html")
    else:
        print(
            "\nwire the UI (run on the host, in examples/blueprints/omnistore):\n"
            f"  python3 web_config.py --publishable {out['publishable_key']}"
        )
    return 0 if keys.get("publishable") else 1


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except ServiceError as exc:
        print(f"api said {exc.status}: {exc.body}", file=sys.stderr)
        raise SystemExit(1) from None
