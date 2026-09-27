# Project code

The API loads Python for a project from `<PAWABASE_CODE_PATH>/<project ref>/`:

| File | What it registers |
| --- | --- |
| `functions/*.py` | `@function` handlers: callable from routes, flows, schedules, events and `/functions/v1/<name>` |
| `policies.py` | `@policy` rules too involved for a JSON condition |
| `transformers.py` | `@transformer` response shapers |
| `routes.py` | a Sillo `router` mounted under `/rest/v1` |

`demo/` is loaded for a project whose reference is `demo`. Reload it from
Studio (project page → *Reload code*) after editing.
