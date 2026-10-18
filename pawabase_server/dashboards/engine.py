"""The dashboard widget registry and what makes a dashboard definition valid.

A dashboard never fetches data itself — it only describes, per widget, which resource to read or write and
how. The published page's own client-side code does every read and write through ``/rest/v1/<resource>``,
under the environment's own publishable key, so each resource's existing operation policies are what actually
decide access. This module is the single source of truth for what a widget binding is allowed to look like,
used both by the validator here and by Studio's editor to know which fields to offer.
"""

from __future__ import annotations

from typing import Any

AGGREGATES = ("count", "sum", "avg", "min", "max")
CHART_TYPES = ("bar", "line")
WIDTHS = ("full", "half", "third")
#: Exactly the REST API's own filter operators (``data/store.py:FILTER_OPERATORS``) — a widget's filter is
#: sent straight through as ``?filter[field]=op.value``, so there is only ever one implementation of "contains".
FILTER_OPS = ("eq", "neq", "gt", "gte", "lt", "lte", "like", "ilike")
FORM_MODES = ("create", "update")

#: Each widget type's required binding keys beyond the common ``source``/``resource``, and whether the
#: aggregate's ``field`` is required (not for ``count``).
WIDGET_TYPES: dict[str, dict[str, Any]] = {
    "metric": {
        "label": "Metric",
        "about": "A single number — a count, sum, average, minimum or maximum of a resource's rows.",
        "required": ("aggregate",),
    },
    "table": {
        "label": "Table",
        "about": "Rows from a resource, with optional column selection, filters and sorting.",
        "required": (),
    },
    "chart": {
        "label": "Chart",
        "about": "A bar or line chart: one aggregate value per group.",
        "required": ("group_by", "aggregate", "chart_type"),
    },
    "kanban": {
        "label": "Kanban board",
        "about": "Cards grouped by one field; dragging a card to another column updates that field.",
        "required": ("group_field",),
    },
    "form": {
        "label": "Form",
        "about": "Create or update one record, through the resource's own create/update policy.",
        "required": ("mode", "fields"),
    },
}


def _problem(where: str, message: str) -> str:
    return f"{where}: {message}"


def _validate_filter(where: str, flt: Any) -> list[str]:
    if not isinstance(flt, dict):
        return [_problem(where, "must be an object")]
    problems = []
    if not flt.get("field"):
        problems.append(_problem(where, "needs a field"))
    if flt.get("op", "eq") not in FILTER_OPS:
        problems.append(_problem(where, f"op must be one of {', '.join(FILTER_OPS)}"))
    return problems


def _validate_binding(where: str, widget_type: str, binding: Any) -> list[str]:
    if not isinstance(binding, dict):
        return [_problem(where, "needs a binding")]
    problems = []
    if binding.get("source") != "resource":
        problems.append(_problem(where, 'binding.source must be "resource"'))
    if not binding.get("resource"):
        problems.append(_problem(where, "binding.resource is required"))
    spec = WIDGET_TYPES[widget_type]
    for key in spec["required"]:
        if not binding.get(key):
            problems.append(_problem(where, f"binding.{key} is required for a {widget_type} widget"))
    if widget_type in ("metric", "chart"):
        aggregate = binding.get("aggregate")
        if aggregate and aggregate not in AGGREGATES:
            problems.append(_problem(where, f"aggregate must be one of {', '.join(AGGREGATES)}"))
        if aggregate and aggregate != "count" and not binding.get("field"):
            problems.append(_problem(where, f"binding.field is required for aggregate {aggregate!r}"))
    if widget_type == "chart" and binding.get("chart_type") not in (*CHART_TYPES, None):
        problems.append(_problem(where, f"chart_type must be one of {', '.join(CHART_TYPES)}"))
    if widget_type == "form" and binding.get("mode") not in (*FORM_MODES, None):
        problems.append(_problem(where, f"mode must be one of {', '.join(FORM_MODES)}"))
    if widget_type == "form" and binding.get("mode") == "update" and not binding.get("id_param"):
        problems.append(_problem(where, "binding.id_param is required to update a record"))
    for index, flt in enumerate(binding.get("filters") or []):
        problems.extend(_validate_filter(f"{where}.filters[{index}]", flt))
    return problems


def validate_dashboard(definition: Any) -> list[str]:
    """Problems that would stop *definition* from being published. Empty when it can be."""
    if not isinstance(definition, dict):
        return ["the dashboard needs a definition object"]
    pages = definition.get("pages")
    if not isinstance(pages, list) or not pages:
        return ["a dashboard needs at least one page"]
    problems: list[str] = []
    page_ids: set[str] = set()
    page_paths: set[str] = set()
    for page_index, page in enumerate(pages):
        where = f"pages[{page_index}]"
        if not isinstance(page, dict):
            problems.append(_problem(where, "must be an object"))
            continue
        page_id = page.get("id")
        if not page_id:
            problems.append(_problem(where, "needs an id"))
        elif page_id in page_ids:
            problems.append(_problem(where, f"duplicate page id {page_id!r}"))
        else:
            page_ids.add(page_id)
        path = page.get("path") or "/"
        if path in page_paths:
            problems.append(_problem(where, f"duplicate page path {path!r}"))
        page_paths.add(path)
        widgets = page.get("widgets")
        if not isinstance(widgets, list):
            problems.append(_problem(where, "needs a widgets list (may be empty)"))
            continue
        widget_ids: set[str] = set()
        for widget_index, widget in enumerate(widgets):
            wwhere = f"{where}.widgets[{widget_index}]"
            if not isinstance(widget, dict):
                problems.append(_problem(wwhere, "must be an object"))
                continue
            widget_id = widget.get("id")
            if not widget_id:
                problems.append(_problem(wwhere, "needs an id"))
            elif widget_id in widget_ids:
                problems.append(_problem(wwhere, f"duplicate widget id {widget_id!r}"))
            else:
                widget_ids.add(widget_id)
            widget_type = widget.get("type")
            if widget_type not in WIDGET_TYPES:
                problems.append(
                    _problem(wwhere, f"type must be one of {', '.join(WIDGET_TYPES)}")
                )
                continue
            if widget.get("width", "full") not in WIDTHS:
                problems.append(_problem(wwhere, f"width must be one of {', '.join(WIDTHS)}"))
            problems.extend(_validate_binding(f"{wwhere}.binding", widget_type, widget.get("binding")))
    return problems
