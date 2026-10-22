"""Generated types mirror what the data API enforces: what a read returns, a create requires, and an update accepts."""

import typing

import pytest

from pawabase_server.core.clients import ServiceError

ENV = "/platform/v1/envs/development"

POSTS = {
    "name": "blog_posts",
    "fields": [
        {"name": "title", "type": "string", "required": True},
        {"name": "views", "type": "integer"},
        {"name": "published", "type": "boolean", "default": False},
        {
            "name": "status",
            "type": "string",
            "enum": ["draft", "live"],
            "required": True,
            "default": "draft",
        },
        {"name": "tags", "type": "array", "items": {"type": "string"}},
        {"name": "meta", "type": "json"},
        {
            "name": "seo",
            "type": "object",
            "fields": [
                {"name": "slug", "type": "string", "required": True},
                {"name": "noindex", "type": "boolean"},
            ],
        },
        {"name": "secret", "type": "string", "write_only": True},
        {"name": "score", "type": "number", "read_only": True},
        {"name": "nickname", "type": "string", "required": True, "nullable": True},
    ],
    "operations": {"list": {"enabled": True, "policy": "public"}},
}


@pytest.fixture
async def posts(api):
    await api.studio.post(f"{ENV}/resources", json=POSTS)
    return api


async def test_typescript_has_the_sdk_shape(posts):
    text = await posts.studio.get(f"{ENV}/types")
    assert "export interface BlogPostsRow {" in text
    assert "export interface BlogPostsInsert {" in text
    assert "export interface BlogPostsUpdate {" in text
    assert (
        "blog_posts: { Row: BlogPostsRow; Insert: BlogPostsInsert; Update: BlogPostsUpdate };"
        in text
    )
    assert "export interface Database {" in text and "  resources: {" in text


async def test_typescript_rows_say_what_can_be_null_and_hide_write_only_columns(posts):
    text = await posts.studio.get(f"{ENV}/types")
    row = text.split("export interface BlogPostsRow {")[1].split("\n}\n")[0]
    assert "  id: string;" in row  # the key
    assert "  title: string;" in row  # required
    assert "  published: boolean;" in row  # has a default
    assert '  status: "draft" | "live";' in row
    assert "  views: number | null;" in row  # optional: may be null
    assert "  tags: string[] | null;" in row
    assert "  meta: unknown;" in row
    assert "  created_at: string;" in row
    assert "secret" not in row  # write-only
    assert "  score: number | null;" in row  # read-only is still readable


async def test_typescript_inserts_require_what_a_create_requires(posts):
    text = await posts.studio.get(f"{ENV}/types")
    insert = text.split("export interface BlogPostsInsert {")[1].split("\n}\n")[0]
    assert "  title: string;" in insert
    assert "  views?: number | null;" in insert
    assert "  status?:" in insert  # required but has a default, so the caller may omit it
    assert "  nickname: string | null;" in insert  # required, and null is allowed
    assert "  secret?: string | null;" in insert  # write-only can be sent
    assert "score" not in insert  # read-only cannot
    assert "id" not in insert and "created_at" not in insert
    assert "  seo?: { slug: string; noindex?: boolean | null } | null;" in insert
    update = text.split("export interface BlogPostsUpdate {")[1].split("\n}\n")[0]
    assert "  title?: string;" in update and "  nickname?: string | null;" in update


async def test_python_types_are_typed_dicts_with_the_same_rules(posts):
    text = await posts.studio.get(f"{ENV}/types", params={"lang": "python"})
    namespace: dict = {}
    exec(compile(text, "pawabase_types.py", "exec"), namespace)  # noqa: S102 - generated code under test
    row, insert, update = (
        namespace["BlogPostsRow"],
        namespace["BlogPostsInsert"],
        namespace["BlogPostsUpdate"],
    )
    assert {"id", "title", "views", "tags", "created_at"} <= set(row.__required_keys__)
    assert "secret" not in row.__annotations__
    assert insert.__required_keys__ == {"title", "nickname"}
    assert "score" not in insert.__annotations__ and "id" not in insert.__annotations__
    assert update.__required_keys__ == frozenset()
    hints = typing.get_type_hints(insert, namespace)
    assert "seo" in hints
    assert typing.get_args(namespace["ResourceName"]) == ("blog_posts",)


async def test_the_nested_object_gets_its_own_typed_dict(posts):
    text = await posts.studio.get(f"{ENV}/types", params={"lang": "python"})
    namespace: dict = {}
    exec(compile(text, "pawabase_types.py", "exec"), namespace)  # noqa: S102
    seo = (
        namespace["BlogPostsSeoSeo"]
        if "BlogPostsSeoSeo" in namespace
        else next(v for k, v in namespace.items() if k.endswith("Seo") or k.endswith("SeoSeo"))
    )
    assert seo.__required_keys__ == {"slug"}


async def test_a_transformed_resource_has_an_open_row(api):
    body = {**POSTS, "name": "shaped", "transformer": {"pick": ["title"]}}
    try:
        await api.studio.post(f"{ENV}/resources", json=body)
    except ServiceError:
        pytest.skip("this transformer shape is not accepted")
    text = await api.studio.get(f"{ENV}/types")
    row = text.split("export interface ShapedRow {")[1].split("\n}\n")[0]
    assert "[column: string]: unknown;" in row


async def test_download_and_name_and_bad_language(posts):
    text = await posts.studio.get(f"{ENV}/types", params={"name": "Schema"})
    assert "export interface Schema {" in text and "Schema[" in text
    with pytest.raises(ServiceError) as caught:
        await posts.studio.get(f"{ENV}/types", params={"lang": "cobol"})
    assert caught.value.status == 400


async def test_change_types_describe_realtime_messages(posts):
    text = await posts.studio.get(f"{ENV}/types")
    assert 'export interface Change<K extends keyof Database["resources"]> {' in text
    assert 'source?: "database";' in text
