import pytest
from pydantic import ValidationError

from pawabase_core.schemas import SchemaError, compile_model, compile_schemas, validate_payload
from pawabase_core.templating import render
from pawabase_core.transformers import (
    TransformerError,
    apply_transformer,
    transformer,
    validate_transformer,
)

FIELDS = [
    {"name": "title", "type": "string", "required": True, "max_length": 10},
    {"name": "email", "type": "email"},
    {"name": "status", "type": "string", "enum": ["draft", "live"], "default": "draft"},
    {"name": "views", "type": "integer", "minimum": 0},
    {"name": "tags", "type": "array", "items": {"type": "string"}},
    {"name": "id", "type": "integer", "read_only": True},
    {"name": "secret", "type": "string", "write_only": True},
]


def test_create_mode_enforces_required_and_constraints():
    model = compile_model("Post", FIELDS)
    post = model.model_validate({"title": "hi", "tags": ["a"]})
    assert post.status == "draft"
    with pytest.raises(ValidationError):
        model.model_validate({})
    with pytest.raises(ValidationError):
        model.model_validate({"title": "x" * 11})
    with pytest.raises(ValidationError):
        model.model_validate({"title": "a", "email": "not-an-email"})
    with pytest.raises(ValidationError):
        model.model_validate({"title": "a", "id": 3})  # read-only, unknown in create
    with pytest.raises(ValidationError):
        model.model_validate({"title": "a", "status": "gone"})


def test_update_and_read_modes():
    update = compile_model("PostUpdate", FIELDS, mode="update")
    assert update.model_validate({}).model_dump(exclude_unset=True) == {}
    read = compile_model("PostOut", FIELDS, mode="read")
    assert "secret" not in read.model_fields and "id" in read.model_fields
    assert validate_payload(FIELDS, {"views": 3}, mode="update") == {"views": 3}


def test_refs_and_errors():
    schemas = compile_schemas({"Address": [{"name": "city", "type": "string", "required": True}]})
    person = compile_model(
        "Person", [{"name": "home", "type": "ref", "schema": "Address"}], registry=schemas
    )
    assert person.model_validate({"home": {"city": "Lagos"}}).home.city == "Lagos"
    with pytest.raises(SchemaError):
        compile_model("Bad", [{"name": "1x", "type": "string"}])
    with pytest.raises(SchemaError):
        compile_model("Bad", [{"name": "a", "type": "string"}, {"name": "a", "type": "string"}])
    with pytest.raises(SchemaError):
        compile_model("Bad", [{"name": "a", "type": "ref", "schema": "Missing"}])


def test_templates():
    state = {"input": {"id": 3, "items": [1, 2]}, "user": {"name": "ada"}}
    assert render("{{ input.items }}", state) == [1, 2]
    assert render("id={{ input.id }}", state) == "id=3"
    assert render({"a": ["{{ user.name | upper }}"]}, state) == {"a": ["ADA"]}
    assert render('{{ missing | default:"x" }}', state) == "x"
    assert render("{{ input.items | length }}", state) == 2


async def test_transformers():
    definition = {
        "omit": ["password"],
        "rename": {"author_id": "author"},
        "set": {"url": "/p/{{ record.id }}"},
        "case": "camel",
    }
    validate_transformer(definition)
    out = await apply_transformer(
        {"id": 1, "author_id": 2, "password": "x", "created_at": "t"}, definition
    )
    assert out == {"id": 1, "author": 2, "url": "/p/1", "createdAt": "t"}
    many = await apply_transformer([{"id": 1}, {"id": 2}], {"pick": ["id"]})
    assert many == [{"id": 1}, {"id": 2}]

    @transformer("shout")
    def shout(record, context):
        return {k: str(v).upper() for k, v in record.items()}

    assert await apply_transformer({"a": "b"}, "shout") == {"a": "B"}
    with pytest.raises(TransformerError):
        validate_transformer({"explode": True})
    with pytest.raises(TransformerError):
        await apply_transformer({}, "unknown")


def test_array_items_can_be_unnamed_objects():
    model = compile_model(
        "Sale",
        [{"name": "lines", "type": "array", "items": {"type": "object", "fields": [
            {"name": "variant_id", "type": "integer", "required": True}]}}],
    )
    sale = model(lines=[{"variant_id": 3}])
    assert sale.lines[0].variant_id == 3


def test_one_broken_schema_does_not_take_the_others_down():
    problems: list[str] = []
    compiled = compile_schemas(
        {
            "Address": [{"name": "city", "type": "string", "required": True}],
            "Broken": [{"name": "x", "type": "ref", "schema": "Missing"}],
            "Order": [{"name": "ship_to", "type": "ref", "schema": "Address"}],
        },
        problems=problems,
    )
    assert set(compiled) == {"Address", "Order"}
    assert problems and problems[0].startswith("schema Broken:")
