"""An example function. Invoke it from Studio (Functions) or over HTTP:

    curl -X POST http://127.0.0.1:8080/functions/v1/hello \
      -H "apikey: <publishable key>" -H "content-type: application/json" \
      -d '{"name": "Ada"}'
"""

from pawabase_kit.functions import FunctionContext, function


@function("hello", policy="public", input_fields=[{"name": "name", "type": "string"}])
async def hello(ctx: FunctionContext):
    name = (ctx.input or {}).get("name") or "world"
    ctx.log("greeting", name=name)
    return {"message": f"Hello, {name}!", "project": ctx.project, "env": ctx.env}
