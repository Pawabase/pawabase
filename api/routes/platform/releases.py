"""Branches, immutable revisions, public API versions, and deployments."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field
from sillo import HttpContext, Router, created
from sillo.exceptions import HTTPException
from tortoise.transactions import in_transaction

from app.platform import Platform
from app.releases import (
    apply_snapshot,
    compatibility,
    snapshot_checksum,
    snapshot_environment,
    validate_snapshot,
)
from database.models import ApiVersion, Branch, DefinitionRevision, Deployment, Release
from pawabase_core.ids import new_ulid
from routes.common import NAME_PATTERN, MANAGE, actor, audit, dump, get_environment

VERSION_PATTERN = r"^v[1-9][0-9]*$"


class BranchCreate(BaseModel):
    name: str = Field(pattern=NAME_PATTERN)
    from_revision: str | None = None
    from_branch: str | None = Field(default=None, pattern=NAME_PATTERN)
    protected: bool = False


class BranchMerge(BaseModel):
    target: str = Field(default="main", pattern=NAME_PATTERN)
    force: bool = False


class RevisionCreate(BaseModel):
    message: str = Field(default="", max_length=2000)


class VersionCreate(BaseModel):
    name: str = Field(pattern=VERSION_PATTERN)
    is_default: bool = False


class VersionUpdate(BaseModel):
    status: Literal["draft", "active", "deprecated", "sunset", "disabled"] | None = None
    is_default: bool | None = None
    sunset_at: datetime | None = None


class ReleaseCreate(BaseModel):
    revision_id: str = Field(min_length=26, max_length=32)  # a ULID, or a pre-ULID 32-character hex id
    api_version: str = Field(pattern=VERSION_PATTERN)
    name: str = Field(min_length=1, max_length=128)
    notes: str = Field(default="", max_length=10000)
    allow_breaking: bool = False


def _revision_view(revision: DefinitionRevision, *, snapshot: bool = False):
    result = dump(revision, exclude=(() if snapshot else ("snapshot",)))
    result["environment_id"] = revision.environment_id
    return result


async def _release_snapshot(release_id: str | None, environment_id: str):
    if not release_id:
        return None
    release = await Release.get_or_none(id=release_id, environment_id=environment_id)
    if release is None:
        return None
    revision = await DefinitionRevision.get_or_none(
        id=release.revision_id, environment_id=environment_id
    )
    return revision.snapshot if revision else None


async def _branch_snapshot(environment, branch: Branch) -> dict:
    """The definitions a branch owns at this moment.

    ``main`` intentionally remains the environment's live working tree. Every
    other branch gets its own stored draft and never reads from live rows after
    creation.
    """
    if branch.name == "main":
        return await snapshot_environment(environment)
    return dict(branch.draft or {"format": 1, "definitions": {}})


def register(r: Router, platform: Platform) -> None:
    prefix = "/envs/{env}"

    @r.get(prefix + "/branches", auth=MANAGE, tags=["releases"])
    async def list_branches(ctx: HttpContext, env: str):
        environment = await get_environment(env)
        return {"data": [dump(item) for item in await Branch.filter(environment=environment)]}

    @r.post(prefix + "/branches", auth=MANAGE, tags=["releases"], request_model=BranchCreate)
    async def create_branch(ctx: HttpContext, env: str, body: BranchCreate):
        environment = await get_environment(env)
        if await Branch.filter(environment=environment, name=body.name).exists():
            raise HTTPException(status_code=409, detail=f"branch {body.name!r} already exists")
        if body.from_revision and body.from_branch:
            raise HTTPException(status_code=422, detail="choose a revision or branch as the source, not both")
        source = None
        if body.from_revision:
            source = await DefinitionRevision.get_or_none(id=body.from_revision, environment=environment)
            if source is None:
                raise HTTPException(status_code=404, detail="the source revision does not exist")
        source_branch = None
        if body.from_branch:
            source_branch = await Branch.get_or_none(environment=environment, name=body.from_branch)
            if source_branch is None:
                raise HTTPException(status_code=404, detail=f"source branch {body.from_branch!r} does not exist")
        # A new branch starts as an isolated copy. Its subsequent definition
        # edits belong to this snapshot, never to the environment's live tree.
        draft = dict(source.snapshot) if source else (await _branch_snapshot(environment, source_branch) if source_branch else await snapshot_environment(environment))
        branch = await Branch.create(
            environment=environment,
            name=body.name,
            head_revision_id=body.from_revision or (source_branch.head_revision_id if source_branch else None),
            draft=draft,
            base_snapshot=dict(draft),
            changes=[],
            protected=body.protected,
        )
        await audit(ctx, "branch.created", env=env, target=body.name)
        return created(dump(branch))

    @r.get(prefix + "/branches/{branch_name}/draft", auth=MANAGE, tags=["releases"])
    async def branch_draft(ctx: HttpContext, env: str, branch_name: str):
        """Read the isolated working tree used when a branch is checked out."""
        environment = await get_environment(env)
        branch = await Branch.get_or_none(environment=environment, name=branch_name)
        if branch is None:
            raise HTTPException(status_code=404, detail=f"branch {branch_name!r} does not exist")
        return {"branch": dump(branch), "snapshot": await _branch_snapshot(environment, branch)}

    @r.post(prefix + "/branches/{branch_name}/merge", auth=MANAGE, tags=["releases"], request_model=BranchMerge)
    async def merge_branch(ctx: HttpContext, env: str, branch_name: str, body: BranchMerge):
        """Merge a feature branch only when its target has not diverged.

        A divergent target is rejected rather than silently overwriting another
        author's definitions; callers can inspect the two drafts and retry
        with ``force`` only when that replacement is intentional.
        """
        environment = await get_environment(env)
        source = await Branch.get_or_none(environment=environment, name=branch_name)
        if source is None or source.name == "main":
            raise HTTPException(status_code=404, detail="choose an existing non-main source branch")
        target = await Branch.get_or_none(environment=environment, name=body.target)
        if body.target != "main" and target is None:
            raise HTTPException(status_code=404, detail=f"target branch {body.target!r} does not exist")
        current = await snapshot_environment(environment) if body.target == "main" else await _branch_snapshot(environment, target)
        if not body.force and snapshot_checksum(current) != snapshot_checksum(source.base_snapshot or {}):
            raise HTTPException(status_code=409, detail="target has changed since this branch was created; resolve or force the merge")
        if body.target == "main":
            await apply_snapshot(environment, source.draft)
            platform.envs.forget(env)
        else:
            target.draft = dict(source.draft)
            target.base_snapshot = dict(current)
            await target.save(update_fields=["draft", "base_snapshot"])
        source.changes = [*list(source.changes or []), {"action": "merged", "target": body.target, "actor": actor(ctx), "at": datetime.now(UTC).isoformat()}]
        await source.save(update_fields=["changes"])
        await audit(ctx, "branch.merged", env=env, target=branch_name, details={"target": body.target, "force": body.force})
        return {"source": branch_name, "target": body.target, "merged": True}

    @r.get(prefix + "/revisions", auth=MANAGE, tags=["releases"])
    async def list_revisions(ctx: HttpContext, env: str):
        environment = await get_environment(env)
        items = await DefinitionRevision.filter(environment=environment).all()
        return {"data": [_revision_view(item) for item in items]}

    @r.get(prefix + "/revisions/{revision_id}", auth=MANAGE, tags=["releases"])
    async def get_revision(ctx: HttpContext, env: str, revision_id: str):
        environment = await get_environment(env)
        revision = await DefinitionRevision.get_or_none(id=revision_id, environment=environment)
        if revision is None:
            raise HTTPException(status_code=404, detail="revision not found")
        return _revision_view(revision, snapshot=True)

    @r.post(
        prefix + "/branches/{branch_name}/revisions",
        auth=MANAGE,
        tags=["releases"],
        request_model=RevisionCreate,
    )
    async def create_revision(
        ctx: HttpContext, env: str, branch_name: str, body: RevisionCreate
    ):
        environment = await get_environment(env)
        branch = await Branch.get_or_none(environment=environment, name=branch_name)
        if branch is None:
            if branch_name != "main":
                raise HTTPException(
                    status_code=404, detail=f"branch {branch_name!r} does not exist"
                )
            branch = await Branch.create(environment=environment, name="main", protected=True)
        snapshot = await _branch_snapshot(environment, branch)
        problems = await validate_snapshot(platform, environment, snapshot)
        last = await DefinitionRevision.filter(environment=environment).order_by("-number").first()
        revision = await DefinitionRevision.create(
            id=new_ulid(),
            environment=environment,
            number=(last.number + 1) if last else 1,
            branch=branch.name,
            parent_revision_id=branch.head_revision_id,
            checksum=snapshot_checksum(snapshot),
            snapshot=snapshot,
            status="invalid" if problems else "valid",
            problems=problems,
            message=body.message,
            created_by=actor(ctx),
        )
        branch.head_revision_id = revision.id
        await branch.save(update_fields=["head_revision_id"])
        await audit(
            ctx,
            "revision.created",
            env=env,
            target=revision.id,
            details={"number": revision.number, "branch": branch.name, "problems": problems},
        )
        return created(_revision_view(revision, snapshot=True))

    @r.get(prefix + "/api-versions", auth=MANAGE, tags=["releases"])
    async def list_versions(ctx: HttpContext, env: str):
        environment = await get_environment(env)
        return {"data": [dump(item) for item in await ApiVersion.filter(environment=environment)]}

    @r.post(prefix + "/api-versions", auth=MANAGE, tags=["releases"], request_model=VersionCreate)
    async def create_version(ctx: HttpContext, env: str, body: VersionCreate):
        environment = await get_environment(env)
        if await ApiVersion.filter(environment=environment, name=body.name).exists():
            raise HTTPException(status_code=409, detail=f"API version {body.name!r} already exists")
        if body.is_default:
            await ApiVersion.filter(environment=environment).update(is_default=False)
        version = await ApiVersion.create(
            environment=environment, name=body.name, is_default=body.is_default
        )
        await audit(ctx, "api_version.created", env=env, target=body.name)
        return created(dump(version))

    @r.patch(
        prefix + "/api-versions/{version_name}",
        auth=MANAGE,
        tags=["releases"],
        request_model=VersionUpdate,
    )
    async def update_version(
        ctx: HttpContext, env: str, version_name: str, body: VersionUpdate
    ):
        environment = await get_environment(env)
        version = await ApiVersion.get_or_none(environment=environment, name=version_name)
        if version is None:
            raise HTTPException(status_code=404, detail="API version not found")
        values = body.model_dump(exclude_unset=True)
        if values.get("is_default"):
            await (
                ApiVersion.filter(environment=environment)
                .exclude(id=version.id)
                .update(is_default=False)
            )
        for key, value in values.items():
            setattr(version, key, value)
        await version.save()
        platform.envs.forget(env)
        await audit(
            ctx, "api_version.updated", env=env, target=version_name, details=values
        )
        return dump(version)

    @r.get(prefix + "/releases", auth=MANAGE, tags=["releases"])
    async def list_releases(ctx: HttpContext, env: str):
        environment = await get_environment(env)
        return {"data": [dump(item) for item in await Release.filter(environment=environment)]}

    @r.post(prefix + "/releases", auth=MANAGE, tags=["releases"], request_model=ReleaseCreate)
    async def create_release(ctx: HttpContext, env: str, body: ReleaseCreate):
        environment = await get_environment(env)
        revision = await DefinitionRevision.get_or_none(
            id=body.revision_id, environment=environment
        )
        if revision is None:
            raise HTTPException(status_code=404, detail="revision not found")
        if revision.status != "valid" or revision.problems:
            raise HTTPException(
                status_code=422,
                detail={"message": "revision is invalid", "problems": revision.problems},
            )
        version = await ApiVersion.get_or_none(environment=environment, name=body.api_version)
        if version is None:
            raise HTTPException(status_code=404, detail="API version not found")
        report = compatibility(
            await _release_snapshot(version.active_release_id, environment.id), revision.snapshot
        )
        if report["breaking"] and not body.allow_breaking:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "breaking changes require allow_breaking",
                    "compatibility": report,
                },
            )
        release = await Release.create(
            id=new_ulid(),
            environment=environment,
            revision_id=revision.id,
            api_version=version.name,
            name=body.name,
            notes=body.notes,
            compatibility=report,
            created_by=actor(ctx),
        )
        await audit(ctx, "release.created", env=env, target=release.id, details=report)
        return created(dump(release))

    async def activate_release(
        ctx: HttpContext, env: str, release: Release, *, action: str
    ):
        environment = await get_environment(env)
        revision = await DefinitionRevision.get_or_none(
            id=release.revision_id, environment=environment
        )
        if revision is None:
            raise HTTPException(status_code=409, detail="release revision is missing")
        # Compile before moving the public pointer. A bad release never receives traffic.
        state = await platform.state_for_release(env, release.id)
        problems = list((await state.compiled()).state.get("problems") or [])
        if problems:
            release.status = "failed"
            await release.save(update_fields=["status"])
            raise HTTPException(
                status_code=422, detail={"message": "release cannot run", "problems": problems}
            )
        version = await ApiVersion.get_or_none(environment=environment, name=release.api_version)
        if version is None:
            raise HTTPException(status_code=409, detail="release API version is missing")
        old_id = version.active_release_id
        async with in_transaction():
            if old_id and old_id != release.id:
                await Release.filter(id=old_id, environment=environment).update(status="superseded")
            version.previous_release_id = (
                old_id if old_id != release.id else version.previous_release_id
            )
            version.active_release_id = release.id
            version.status = "active"
            await version.save(update_fields=["previous_release_id", "active_release_id", "status"])
            release.status = "active"
            release.deployed_at = datetime.now(UTC)
            await release.save(update_fields=["status", "deployed_at"])
            deployment = await Deployment.create(
                environment=environment,
                api_version=version.name,
                release_id=release.id,
                previous_release_id=old_id,
                action=action,
                actor=actor(ctx),
                details={"revision_id": revision.id, "checksum": revision.checksum},
            )
        platform.envs.forget(env)
        await audit(
            ctx,
            f"release.{action}",
            env=env,
            target=release.id,
            details={"previous_release_id": old_id},
        )
        return {
            "release": dump(release),
            "api_version": dump(version),
            "deployment": dump(deployment),
        }

    @r.post(prefix + "/releases/{release_id}/activate", auth=MANAGE, tags=["releases"])
    async def activate(ctx: HttpContext, env: str, release_id: str):
        environment = await get_environment(env)
        release = await Release.get_or_none(id=release_id, environment=environment)
        if release is None:
            raise HTTPException(status_code=404, detail="release not found")
        return await activate_release(ctx, env, release, action="activated")

    @r.post(prefix + "/api-versions/{version_name}/rollback", auth=MANAGE, tags=["releases"])
    async def rollback(ctx: HttpContext, env: str, version_name: str):
        environment = await get_environment(env)
        version = await ApiVersion.get_or_none(environment=environment, name=version_name)
        if version is None:
            raise HTTPException(status_code=404, detail="API version not found")
        if not version.previous_release_id:
            raise HTTPException(status_code=409, detail="there is no previous release to restore")
        release = await Release.get_or_none(
            id=version.previous_release_id, environment=environment, api_version=version_name
        )
        if release is None:
            raise HTTPException(status_code=409, detail="the previous release is missing")
        return await activate_release(ctx, env, release, action="rolled_back")

    @r.get(prefix + "/deployments", auth=MANAGE, tags=["releases"])
    async def list_deployments(ctx: HttpContext, env: str):
        environment = await get_environment(env)
        return {"data": [dump(item) for item in await Deployment.filter(environment=environment)]}
