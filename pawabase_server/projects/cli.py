"""``pawabase-server project …`` and ``pawabase-server env …``: managing projects from the machine the runtime runs on.

There is no sign-in: reaching this command line means having the installation's configuration and its database, which is the same
authority as running the server. It calls the project manager directly; nothing is exposed over HTTP.

    project create NAME [--slug S] [--id ID]
    project list | show REF | update REF --name N | delete REF
    project limits show REF | set REF [--daily-requests N] [--monthly-requests N] [--rate-limit N] [--rate-window N] [--max-active-connections N]
                                      [--max-function-timeout-seconds N] [--max-function-memory-mb N] [--max-function-cpu-seconds N]
                                      [--max-concurrent-runs-per-function N] [--max-function-body-mb N] [--max-function-workers N] [--reset]
    project db bind REF (--database-url-env VAR | --database-url URL) [--mode M] [--rebind] [--no-test]
    project db test REF | project db rotate REF (--database-url-env VAR | --database-url URL)
    project init                  make the default project and attach existing environments (what start-up does)
    env create SLUG --project REF | env list --project REF
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from pawabase_server.db.database import database
from pawabase_server.projects.manager import ProjectError, ProjectManager, ensure_default_project
from pawabase_server.settings import Settings


#: The project limits that hold every function run (``functions/limits.py``).
FUNCTION_LIMITS = (
    "max_function_timeout_seconds",
    "max_function_memory_mb",
    "max_function_cpu_seconds",
    "max_concurrent_runs_per_function",
    "max_function_body_mb",
    "max_function_workers",
)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="pawabase-server", add_help=False)
    root.add_argument("--json", action="store_true", help="print JSON")
    sub = root.add_subparsers(dest="group")

    project = sub.add_parser("project")
    pcmd = project.add_subparsers(dest="cmd", required=True)
    create = pcmd.add_parser("create")
    create.add_argument("name")
    create.add_argument("--slug")
    create.add_argument("--id")
    pcmd.add_parser("list")
    for name in ("show", "delete"):
        pcmd.add_parser(name).add_argument("ref")
    update = pcmd.add_parser("update")
    update.add_argument("ref")
    update.add_argument("--name", required=True)
    limits = pcmd.add_parser("limits")
    lcmd = limits.add_subparsers(dest="limits_cmd", required=True)
    lcmd.add_parser("show").add_argument("ref")
    lset = lcmd.add_parser("set")
    lset.add_argument("ref")
    lset.add_argument("--daily-requests", type=int)
    lset.add_argument("--monthly-requests", type=int)
    lset.add_argument("--rate-limit", type=int)
    lset.add_argument("--rate-window", type=int)
    lset.add_argument("--max-active-connections", type=int)
    # What every function run of the project may use (functions/limits.py); 0 returns a limit to the installation's default.
    for field in FUNCTION_LIMITS:
        lset.add_argument("--" + field.replace("_", "-"), type=int, dest=field)
    lset.add_argument("--reset", action="store_true")
    pcmd.add_parser("init")
    db = pcmd.add_parser("db")
    dcmd = db.add_subparsers(dest="dbcmd", required=True)
    for name in ("bind", "rotate"):
        c = dcmd.add_parser(name)
        c.add_argument("ref")
        c.add_argument("--database-url-env", dest="url_env")
        c.add_argument("--database-url", dest="url")
        if name == "bind":
            c.add_argument("--mode", default="external", choices=["managed", "external", "local"])
            c.add_argument("--rebind", action="store_true")
            c.add_argument("--no-test", action="store_true")
    dcmd.add_parser("test").add_argument("ref")

    env = sub.add_parser("env")
    ecmd = env.add_subparsers(dest="cmd", required=True)
    ecreate = ecmd.add_parser("create")
    ecreate.add_argument("slug")
    ecreate.add_argument("--project", required=True)
    ecreate.add_argument("--no-keys", action="store_true")
    ecmd.add_parser("list").add_argument("--project", required=True)
    return root


async def execute(args: argparse.Namespace, manager: ProjectManager, settings: Settings) -> Any:
    if args.group == "project":
        if args.cmd == "create":
            project, created = await manager.create_project(
                name=args.name, slug=args.slug, id=args.id
            )
            return {**(await manager.show(project.id)), "created": created}
        if args.cmd == "list":
            return await manager.list_projects()
        if args.cmd == "show":
            return await manager.show(args.ref)
        if args.cmd == "update":
            return await manager.update(args.ref, name=args.name)
        if args.cmd == "limits":
            if args.limits_cmd == "show":
                shown = await manager.show(args.ref)
                return {
                    "id": shown["id"],
                    "slug": shown["slug"],
                    "limits": shown["limits"],
                    "limits_version": shown["limits_version"],
                }
            changes = {
                field: value
                for field, value in {
                    "daily_request_limit": args.daily_requests,
                    "monthly_request_limit": args.monthly_requests,
                    "rate_limit": args.rate_limit,
                    "rate_window": args.rate_window,
                    "max_active_connections": args.max_active_connections,
                    **{field: getattr(args, field) for field in FUNCTION_LIMITS},
                }.items()
                if value is not None
            }
            if not changes and not args.reset:
                raise ProjectError("set at least one limit or use --reset")
            return await manager.patch_limits(args.ref, changes, reset=args.reset)
        if args.cmd == "delete":
            return await manager.delete(args.ref)
        if args.cmd == "init":
            return await ensure_default_project(settings, manager.box)
        if args.cmd == "db":
            if args.dbcmd == "bind":
                return await manager.bind_database(
                    args.ref,
                    url=args.url,
                    url_env=args.url_env,
                    mode=args.mode,
                    rebind=args.rebind,
                    test=not args.no_test,
                )
            if args.dbcmd == "rotate":
                return await manager.rotate_credentials(
                    args.ref, url=args.url, url_env=args.url_env
                )
            return await manager.test_database(args.ref)
    if args.group == "env":
        if args.cmd == "create":
            return await manager.create_environment(
                args.project, args.slug, make_keys=not args.no_keys
            )
        shown = await manager.show(args.project)
        return shown["environments"]
    raise ProjectError("unknown command")


def render(result: Any) -> str:
    return json.dumps(result, indent=2, default=str)


async def run(argv: list[str], settings: Settings) -> int:
    try:
        args = parser().parse_args(argv)
    except SystemExit as exit_:
        return int(exit_.code or 2)
    if not args.group:
        print(__doc__, file=sys.stderr)
        return 64
    async with database(settings):
        manager = ProjectManager(settings)
        try:
            result = await execute(args, manager, settings)
        except ProjectError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
    print(render(result))
    return 0
