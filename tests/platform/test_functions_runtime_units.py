"""The functions runtime's parts that need no Deno: grants, HTTP function config, the egress guard, import maps, routing."""

import asyncio
import json
import time
from pathlib import Path

import httpcore
import pytest

from pawabase_server.core.tokens import TokenInvalid
from pawabase_server.functions import vendor
from pawabase_server.functions.http_dispatch import split_path
from pawabase_server.functions.http_functions import (
    Cors,
    HttpFunctionSpec,
    config_from_dict,
    parse_config,
)
from pawabase_server.functions.sandbox import typescript
from pawabase_server.functions.sandbox.confine import seccomp_program
from pawabase_server.functions.service import grants
from pawabase_server.functions.service.egress import Budget, EgressRefused, GuardedBackend, address_allowed

SECRET = "x" * 40
LIMITS = {"max_timeout": 400, "max_memory": 512, "max_concurrency": 256}


# ── grants ───────────────────────────────────────────────────────────────────


def test_a_grant_round_trips_and_names_its_invocation():
    grant = grants.Grant(env="production", branch="main", function="orders", invocation="abc", auth={"user_id": "7"}, depth=1)
    back = grants.verify(SECRET, grants.issue(SECRET, grant, seconds=30))
    assert (back.env, back.function, back.invocation, back.auth, back.depth) == ("production", "orders", "abc", {"user_id": "7"}, 1)
    assert back.expires_at > time.time() + 30


def test_a_grant_cannot_be_forged_or_reused_after_it_expires():
    grant = grants.Grant(env="e", branch="main", function="f", invocation="i")
    with pytest.raises(TokenInvalid):
        grants.verify("another-secret-of-forty-characters-long!!", grants.issue(SECRET, grant, seconds=30))
    expired = grants.issue(SECRET, grant, seconds=-grants.SLACK_SECONDS - 60)
    with pytest.raises(TokenInvalid):
        grants.verify(SECRET, expired)
    with pytest.raises(TokenInvalid):
        grants.verify(SECRET, "not a token")


# ── HTTP function config ─────────────────────────────────────────────────────


def test_config_defaults_and_supabase_verify_jwt():
    config, problems = parse_config({}, **LIMITS)
    assert not problems and config.auth == "key" and config.timeout == 150 and "OPTIONS" in config.methods
    assert parse_config({"verify_jwt": False}, **LIMITS)[0].auth == "public"
    assert parse_config({"verifyJwt": True}, **LIMITS)[0].auth == "user"


def test_config_problems_are_named():
    _config, problems = parse_config(
        {"timeout": 9999, "memory": 10, "warm": 99, "methods": ["GET", "BREW"], "cors": {"origins": ["app.example.com"]}, "nope": 1},
        **LIMITS,
    )
    text = " | ".join(problems)
    for expected in ("config.timeout", "config.memory", "config.warm", "BREW", "full origin", "config.nope"):
        assert expected in text


def test_cors_allows_listed_origins_only():
    cors = Cors(origins=("https://app.example.com",))
    assert cors.allows("https://app.example.com") == "https://app.example.com"
    assert cors.allows("https://evil.example") is None
    assert ("vary", "origin") in cors.response_headers("https://app.example.com")
    assert Cors().allows("https://anything.example") == "*"


def test_a_spec_survives_the_trip_to_the_runtime_service():
    config, _ = parse_config({"auth": "user", "timeout": 20, "warm": 1, "cors": {"origins": ["https://a.example"]}}, **LIMITS)
    spec = HttpFunctionSpec(name="f", entry="functions/f/index.ts", config=config, style="pawabase", env="e", branch="main", deployment_id="d")
    back = HttpFunctionSpec.from_wire(json.loads(json.dumps(spec.wire())))
    assert back == spec
    assert config_from_dict(config.as_dict()) == config


# ── routing ──────────────────────────────────────────────────────────────────


def test_function_paths_split_into_name_and_rest():
    assert split_path("/functions/v1/hello") == (None, "hello", "/", False)
    assert split_path("/functions/v1/hello/a/b") == (None, "hello", "/a/b", False)
    assert split_path("/functions/v1/public/production/hook/x") == ("production", "hook", "/x", True)
    assert split_path("/functions/v1/public/production") is None
    assert split_path("/rest/v1/things") is None


def test_bundles_with_http_functions_are_typescript():
    assert typescript.language_of(["functions/hello/index.ts"]) == "typescript"
    assert typescript.language_of(["functions/hello/index.ts", "functions/x.py"]) == "mixed"
    assert typescript.language_of(["functions/_shared/index.ts"]) is None
    assert typescript.is_http_entry("functions/hello/index.mjs") and not typescript.is_http_entry("functions/hello/other.ts")


# ── egress ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("address", "allowed"),
    [
        ("8.8.8.8", True),
        ("2606:4700:4700::1111", True),
        ("127.0.0.1", False),
        ("10.1.2.3", False),
        ("172.16.0.1", False),
        ("192.168.1.1", False),
        ("169.254.169.254", False),  # cloud metadata
        ("100.64.0.1", False),  # carrier-grade NAT
        ("0.0.0.0", False),
        ("::1", False),
        ("::ffff:127.0.0.1", False),  # IPv4-mapped loopback
        ("fd00::1", False),
        ("224.0.0.1", False),
        ("not an address", False),
    ],
)
def test_only_global_unicast_addresses_are_reachable(address, allowed):
    assert address_allowed(address) is allowed


async def test_the_guard_checks_the_address_it_connects_to():
    class Recorder(httpcore.AsyncNetworkBackend):
        def __init__(self):
            self.connected = []

        async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
            self.connected.append(host)
            raise httpcore.ConnectError("not really connecting in a test")

        async def sleep(self, seconds):
            await asyncio.sleep(seconds)

    inner = Recorder()
    guard = GuardedBackend(inner=inner)
    with pytest.raises(httpcore.ConnectError, match="private"):
        await guard.connect_tcp("localhost", 80)
    assert inner.connected == []  # refused before any connection
    trusted = GuardedBackend(["localhost"], inner=inner)
    with pytest.raises(httpcore.ConnectError):
        await trusted.connect_tcp("localhost", 80)
    assert inner.connected and all(address in ("127.0.0.1", "::1") for address in inner.connected)


def test_a_budget_counts_requests_and_bytes():
    budget = Budget(requests=2, bytes=10)
    budget.take_request()
    budget.take_request()
    with pytest.raises(EgressRefused):
        budget.take_request()
    budget.take_bytes(10)
    with pytest.raises(EgressRefused):
        budget.take_bytes(1)


def test_direct_network_lets_sockets_through_the_filter_and_guarded_does_not():
    socket_number = 41  # x86_64
    guarded = seccomp_program("x86_64", 1)
    direct = seccomp_program("x86_64", 1, sockets=True)
    assert any(k == socket_number for _, _, _, k in guarded)
    assert not any(k == socket_number for _, _, _, k in direct) and len(direct) == len(guarded) - 1


# ── import maps and vendoring ────────────────────────────────────────────────


def _deployment(tmp_path: Path, files: dict[str, str]) -> Path:
    for name, text in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return tmp_path


def test_import_maps_merge_shared_then_per_function_and_keep_the_kit(tmp_path):
    artifact = _deployment(
        tmp_path,
        {
            "functions/deno.json": json.dumps({"imports": {"@lib/": "./_shared/", "lodash": "npm:lodash@4", "pawabase": "./evil.ts"}}),
            "functions/hello/deno.jsonc": '{\n  // the function overrides one\n  "imports": {"lodash": "npm:lodash-es@4",},\n}',
            "functions/hello/index.ts": "",
        },
    )
    kit = Path("/kit")
    shared = vendor.import_map(artifact, kit)
    assert shared["imports"]["@lib/"] == (artifact / "functions" / "_shared").resolve().as_uri() + "/"
    assert shared["imports"]["pawabase"] == (kit / "pawabase.ts").as_uri()  # the kit cannot be remapped
    assert vendor.import_map(artifact, kit, "hello")["imports"]["lodash"] == "npm:lodash-es@4"


def test_an_import_map_cannot_point_outside_the_deployment(tmp_path):
    artifact = _deployment(tmp_path / "d", {"functions/deno.json": json.dumps({"imports": {"x": "../../etc/passwd"}})})
    with pytest.raises(vendor.VendorError, match="outside"):
        vendor.import_map(artifact, Path("/kit"))


def test_remote_specifiers_are_found_in_sources_and_maps(tmp_path):
    artifact = _deployment(
        tmp_path,
        {
            "functions/a/index.ts": 'import Stripe from "npm:stripe@16";\nconst m = await import("jsr:@std/path@1");\nimport "./local.ts";',
            "functions/deno.json": json.dumps({"imports": {"zod": "https://deno.land/x/zod@v3.23.8/mod.ts"}}),
        },
    )
    assert vendor.remote_specifiers(artifact, Path("/kit")) == [
        "https://deno.land/x/zod@v3.23.8/mod.ts",
        "jsr:@std/path@1",
        "npm:stripe@16",
    ]


def test_a_deployment_with_remote_imports_is_refused_when_vendoring_is_off(tmp_path):
    artifact = _deployment(tmp_path / "d", {"functions/a/index.ts": 'import x from "npm:left-pad@1";'})
    off = vendor.Vendor(tmp_path / "root", enabled=False, kit=Path("/kit"))
    with pytest.raises(vendor.VendorError, match="does not fetch"):
        off.vendor(artifact, "c" * 64, deno="deno")
    nothing = _deployment(tmp_path / "e", {"functions/a/index.ts": 'import "./x.ts";'})
    assert off.vendor(nothing, "c" * 64, deno="deno").digest is None
