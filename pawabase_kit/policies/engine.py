"""Declarative, reusable access policies.

A policy answers "may this caller do this?" from what is known about the
request: the caller (``auth``), the record being touched (``record``), the
submitted data (``input``), the request itself (``request``) and the project
(``project``, ``env``).

Policies are data, so Studio can edit them and Pawabase can store them. A
condition is JSON::

    {"all": [
        {"authenticated": true},
        {"eq": ["$record.owner_id", "$auth.user_id"]}
    ]}

Operands beginning with ``$`` are paths into the evaluation context. Everything
else is a literal (write ``$$`` for a literal dollar). There are no functions and
no variables. When a rule needs code, it is written in Python and registered with
:func:`~pawabase_kit.policies.registry.policy`.

Two evaluation modes exist:

* :meth:`PolicyEngine.check` decides one request against one record.
* :meth:`PolicyEngine.plan` decides a *list* request before any rows are read.
  Conditions that do not mention ``record`` are decided at once. Equalities
  between a record field and a known value become SQL filters, so a list is
  filtered in the database and pagination stays correct. Anything else is
  left as a residual checked per row.
"""

from __future__ import annotations

import inspect
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

Condition = Any  # bool | dict

_MISSING = object()

BINARY_OPS = {
    "eq",
    "ne",
    "gt",
    "gte",
    "lt",
    "lte",
    "in",
    "not_in",
    "contains",
    "starts_with",
    "ends_with",
    "matches",
}
SHORTHANDS = {"authenticated", "role", "permission", "owner", "service", "kind", "scope", "aal"}
UNARY = {"exists", "truthy", "empty"}
COMBINATORS = {"all", "any", "not"}


class PolicyError(ValueError):
    """A policy definition that cannot be evaluated."""


@dataclass(slots=True)
class Decision:
    """The outcome of a policy check.

    Attributes:
        allowed: Whether the operation may proceed.
        policy: The policy that decided.
        reason: Why, in a sentence, for request inspection in Studio.
    """

    allowed: bool
    policy: str = ""
    reason: str = ""

    def __bool__(self) -> bool:
        return self.allowed


@dataclass(slots=True)
class ListPlan:
    """How to run a list request under a policy.

    Attributes:
        allowed: False when no row could ever be visible. The request is refused
            before any query runs.
        filters: ``{field: value}`` equalities to add to the SQL ``WHERE``.
        residual: A condition to check per row after the query, or ``None``.
        policy: The policy that produced this plan.
    """

    allowed: bool
    filters: dict[str, Any] = field(default_factory=dict)
    residual: Condition | None = None
    policy: str = ""


@dataclass(slots=True)
class Policy:
    """A named, reusable condition.

    Attributes:
        name: How routes, resources, buckets and channels refer to it.
        condition: The JSON condition, or ``None`` when *handler* decides.
        description: Shown in Studio.
        handler: A Python callable ``(context) -> bool``, sync or async.
    """

    name: str
    condition: Condition = None
    description: str = ""
    handler: Callable[[dict[str, Any]], Any] | None = None


def validate_condition(condition: Condition, *, path: str = "condition") -> None:
    """Refuse a malformed condition before it is stored.

    Raises:
        PolicyError: Naming where the condition is wrong.
    """
    if isinstance(condition, bool):
        return
    if not isinstance(condition, Mapping) or len(condition) != 1:
        raise PolicyError(f"{path}: a condition is true, false, or an object with one key")
    ((op, arg),) = condition.items()
    if op in ("all", "any"):
        if not isinstance(arg, list):
            raise PolicyError(f"{path}.{op}: expected a list of conditions")
        for index, child in enumerate(arg):
            validate_condition(child, path=f"{path}.{op}[{index}]")
    elif op == "not":
        validate_condition(arg, path=f"{path}.not")
    elif op in BINARY_OPS:
        if not isinstance(arg, list) or len(arg) != 2:
            raise PolicyError(f"{path}.{op}: expected [left, right]")
        if op == "matches" and isinstance(arg[1], str) and not arg[1].startswith("$"):
            try:
                re.compile(arg[1])
            except re.error as exc:
                raise PolicyError(f"{path}.matches: invalid pattern: {exc}") from exc
    elif op in UNARY or op in SHORTHANDS:
        return
    else:
        raise PolicyError(f"{path}: unknown operator {op!r}")


def resolve(operand: Any, context: Mapping[str, Any]) -> Any:
    """The value of an operand: a ``$path`` lookup, or the literal itself."""
    if isinstance(operand, str) and operand.startswith("$"):
        if operand.startswith("$$"):
            return operand[1:]
        return lookup(context, operand[1:])
    return operand


def lookup(context: Mapping[str, Any], path: str, default: Any = None) -> Any:
    """Read ``a.b.0.c`` out of nested mappings, lists and objects."""
    if path == "now":
        return datetime.now(UTC)
    current: Any = context
    for part in path.split("."):
        if current is None:
            return default
        if isinstance(current, Mapping):
            current = current.get(part, _MISSING)
        elif isinstance(current, (list, tuple)) and part.isdigit():
            index = int(part)
            current = current[index] if index < len(current) else _MISSING
        else:
            current = getattr(current, part, _MISSING)
        if current is _MISSING:
            return default
    return current


def _compare(op: str, left: Any, right: Any) -> bool:
    try:
        if op == "eq":
            return _norm(left) == _norm(right)
        if op == "ne":
            return _norm(left) != _norm(right)
        if op == "gt":
            return left is not None and right is not None and left > right
        if op == "gte":
            return left is not None and right is not None and left >= right
        if op == "lt":
            return left is not None and right is not None and left < right
        if op == "lte":
            return left is not None and right is not None and left <= right
        if op == "in":
            return right is not None and _norm(left) in [_norm(v) for v in right]
        if op == "not_in":
            return right is None or _norm(left) not in [_norm(v) for v in right]
        if op == "contains":
            return (
                left is not None and _norm(right) in [_norm(v) for v in left]
                if isinstance(left, (list, tuple, set))
                else (left is not None and str(right) in str(left))
            )
        if op == "starts_with":
            return left is not None and str(left).startswith(str(right))
        if op == "ends_with":
            return left is not None and str(left).endswith(str(right))
        if op == "matches":
            return left is not None and re.search(str(right), str(left)) is not None
    except TypeError:
        return False
    raise PolicyError(f"unknown operator {op!r}")


def _norm(value: Any) -> Any:
    # Ids arrive as ints from the database and as strings from tokens; a policy
    # comparing them must not depend on which side happened to be which.
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int):
        return str(value)
    return value


def _shorthand(op: str, arg: Any, context: Mapping[str, Any]) -> Condition:
    """Expand a shorthand into the condition it stands for."""
    if op == "authenticated":
        return {"eq": ["$auth.authenticated", bool(arg)]}
    if op == "role":
        roles = arg if isinstance(arg, list) else [arg]
        return {"any": [{"contains": ["$auth.roles", role]} for role in roles]}
    if op == "permission":
        perms = arg if isinstance(arg, list) else [arg]
        return {
            "all": [
                {
                    "any": [
                        {"contains": ["$auth.permissions", p]},
                        {"contains": ["$auth.permissions", "*"]},
                    ]
                }
                for p in perms
            ]
        }
    if op == "owner":
        column = arg if isinstance(arg, str) else "owner_id"
        return {"all": [{"authenticated": True}, {"eq": [f"$record.{column}", "$auth.user_id"]}]}
    if op == "service":
        return {"eq": ["$credential.is_service", bool(arg)]}
    if op == "kind":
        return {"eq": ["$auth.kind", arg]}
    if op == "scope":
        return {"contains": ["$credential.scopes", arg]}
    if op == "aal":
        return {"eq": ["$auth.aal", arg]}
    raise PolicyError(f"unknown shorthand {op!r}")


def evaluate(condition: Condition, context: Mapping[str, Any]) -> bool:
    """Evaluate *condition* fully against *context*."""
    if isinstance(condition, bool):
        return condition
    ((op, arg),) = condition.items()
    if op == "all":
        return all(evaluate(child, context) for child in arg)
    if op == "any":
        return any(evaluate(child, context) for child in arg)
    if op == "not":
        return not evaluate(arg, context)
    if op in BINARY_OPS:
        return _compare(op, resolve(arg[0], context), resolve(arg[1], context))
    if op == "exists":
        return resolve(arg, context) is not None
    if op == "truthy":
        return bool(resolve(arg, context))
    if op == "empty":
        return not resolve(arg, context)
    if op in SHORTHANDS:
        return evaluate(_shorthand(op, arg, context), context)
    raise PolicyError(f"unknown operator {op!r}")


def _mentions_record(operand: Any) -> bool:
    return isinstance(operand, str) and (operand == "$record" or operand.startswith("$record."))


def _deferred(operand: Any) -> bool:
    """Operands unknown when a request is first planned: the record, and the
    submitted body (a gate runs before the body is read). Conditions on them
    are left for the full check with the record and input in hand."""
    return _mentions_record(operand) or (
        isinstance(operand, str) and (operand == "$input" or operand.startswith("$input."))
    )


def partial(condition: Condition, context: Mapping[str, Any]) -> Condition:
    """Evaluate what can be decided without a record.

    Returns:
        ``True`` or ``False`` when decided, otherwise a residual condition that
        mentions only ``record``-dependent parts.
    """
    if isinstance(condition, bool):
        return condition
    ((op, arg),) = condition.items()
    if op in SHORTHANDS:
        return partial(_shorthand(op, arg, context), context)
    if op == "all":
        pending = []
        for child in arg:
            result = partial(child, context)
            if result is False:
                return False
            if result is not True:
                pending.append(result)
        if not pending:
            return True
        return pending[0] if len(pending) == 1 else {"all": pending}
    if op == "any":
        pending = []
        for child in arg:
            result = partial(child, context)
            if result is True:
                return True
            if result is not False:
                pending.append(result)
        if not pending:
            return False
        return pending[0] if len(pending) == 1 else {"any": pending}
    if op == "not":
        result = partial(arg, context)
        if isinstance(result, bool):
            return not result
        return {"not": result}
    if op in BINARY_OPS:
        if _deferred(arg[0]) or _deferred(arg[1]):
            # Pin the known side now, so the residual carries a literal.
            left = arg[0] if _deferred(arg[0]) else resolve(arg[0], context)
            right = arg[1] if _deferred(arg[1]) else resolve(arg[1], context)
            return {op: [_literal(left), _literal(right)]}
        return evaluate(condition, context)
    if op in UNARY:
        if _deferred(arg):
            return condition
        return evaluate(condition, context)
    raise PolicyError(f"unknown operator {op!r}")


def _literal(value: Any) -> Any:
    if isinstance(value, str) and value.startswith("$") and not _deferred(value):
        return "$" + value  # escape a resolved value that happens to start with $
    return value


def pushdown(residual: Condition) -> tuple[dict[str, Any], Condition | None]:
    """Split a residual into SQL equality filters and what is left.

    Only conjunctions of ``eq`` between one ``$record.<column>`` and a literal
    are pushed down. That is exactly the ownership and tenancy shape, and it is
    the part pagination depends on.
    """
    parts = residual["all"] if isinstance(residual, Mapping) and "all" in residual else [residual]
    filters: dict[str, Any] = {}
    remaining = []
    for part in parts:
        if isinstance(part, Mapping) and "eq" in part:
            left, right = part["eq"]
            if _mentions_record(left) and not _deferred(right) and left.count(".") == 1:
                column = left.split(".", 1)[1]
                value = resolve(right, {})
                if column not in filters:
                    filters[column] = value
                    continue
            if _mentions_record(right) and not _deferred(left) and right.count(".") == 1:
                column = right.split(".", 1)[1]
                value = resolve(left, {})
                if column not in filters:
                    filters[column] = value
                    continue
        remaining.append(part)
    if not remaining:
        return filters, None
    return filters, remaining[0] if len(remaining) == 1 else {"all": remaining}


# ── the engine ──────────────────────────────────────────────────────────────

BUILTIN_POLICIES: dict[str, Policy] = {
    "public": Policy("public", True, "Anyone, including anonymous callers."),
    "deny": Policy("deny", False, "Nobody except service credentials."),
    "authenticated": Policy("authenticated", {"authenticated": True}, "Any signed-in user."),
    "service": Policy("service", {"service": True}, "Secret keys and operators only."),
    "owner": Policy(
        "owner", {"owner": "owner_id"}, "The user whose id is in the record's owner_id."
    ),
    "mfa": Policy(
        "mfa",
        {"all": [{"authenticated": True}, {"aal": "aal2"}]},
        "Signed in with a second factor.",
    ),
}


class PolicyEngine:
    """Resolves policy references and evaluates them.

    A reference is a policy name (``"owner"``), a parameterised built-in
    (``"role:admin"``, ``"permission:posts.write"``, ``"owner:author_id"``,
    ``"scope:resource:write"``), an inline condition, or a list of references
    that must all pass.

    Args:
        policies: The project's stored policies by name.
        python: Python policies registered with ``@policy``.
    """

    def __init__(
        self,
        policies: Mapping[str, Policy] | None = None,
        python: Mapping[str, Policy] | None = None,
    ) -> None:
        self.policies: dict[str, Policy] = {
            **BUILTIN_POLICIES,
            **(python or {}),
            **(policies or {}),
        }

    def resolve_ref(self, ref: Any) -> Policy:
        """The :class:`Policy` a reference names."""
        if isinstance(ref, Policy):
            return ref
        if ref is None:
            return self.policies["authenticated"]
        if isinstance(ref, (bool, Mapping)):
            validate_condition(ref)
            return Policy("inline", ref)
        if isinstance(ref, list):
            resolved = [self.resolve_ref(item) for item in ref]
            if any(p.handler for p in resolved):
                raise PolicyError(
                    "Python policies cannot be combined in a list; call them from Python"
                )
            return Policy(
                "+".join(p.name for p in resolved), {"all": [p.condition for p in resolved]}
            )
        if isinstance(ref, str):
            if ref in self.policies:
                return self.policies[ref]
            kind, _, argument = ref.partition(":")
            if argument and kind in ("role", "permission", "owner", "scope", "kind"):
                return Policy(ref, {kind: argument})
        raise PolicyError(f"unknown policy {ref!r}")

    async def check(self, ref: Any, context: Mapping[str, Any]) -> Decision:
        """Decide one operation.

        Service credentials (secret keys, operators, services) always pass: they
        are how a developer's own servers and Studio operate on the data.
        """
        if lookup(context, "credential.is_service"):
            return Decision(True, "service", "service credential bypasses policies")
        policy = self.resolve_ref(ref)
        if policy.handler is not None:
            result = policy.handler(dict(context))
            if inspect.isawaitable(result):
                result = await result
            allowed = bool(result)
        else:
            allowed = evaluate(policy.condition, context)
        return Decision(
            allowed, policy.name, f"policy {policy.name!r} {'allowed' if allowed else 'refused'}"
        )

    def plan(self, ref: Any, context: Mapping[str, Any]) -> ListPlan:
        """Decide a list operation before reading rows. See the module docstring."""
        if lookup(context, "credential.is_service"):
            return ListPlan(True, policy="service")
        policy = self.resolve_ref(ref)
        if policy.handler is not None:
            # Python policies see each row; nothing can be pushed down.
            return ListPlan(True, residual=policy, policy=policy.name)
        outcome = partial(policy.condition, context)
        if outcome is True:
            return ListPlan(True, policy=policy.name)
        if outcome is False:
            return ListPlan(False, policy=policy.name)
        filters, residual = pushdown(outcome)
        return ListPlan(True, filters=filters, residual=residual, policy=policy.name)

    async def row_allowed(self, plan: ListPlan, context: Mapping[str, Any]) -> bool:
        """Check a residual against one row (``context`` includes ``record``)."""
        if plan.residual is None:
            return True
        if isinstance(plan.residual, Policy):
            result = plan.residual.handler(dict(context))  # type: ignore[misc]
            if inspect.isawaitable(result):
                result = await result
            return bool(result)
        return evaluate(plan.residual, context)


def policy_from_definition(name: str, definition: Mapping[str, Any]) -> Policy:
    """Build a :class:`Policy` from its stored form, validating it."""
    condition = definition.get("condition", definition.get("rules", True))
    validate_condition(condition)
    return Policy(name=name, condition=condition, description=definition.get("description", ""))
