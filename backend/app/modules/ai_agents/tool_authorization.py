# DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
# Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
"""Mandatory authorization for every agent tool call.

Until this module existed, tool authorization was a convention. The runner
injected a trusted ``__agent_context__`` into each call and the tools that cared
used it - ``_tool_read_boq`` resolves the BOQ to its owning project and refuses a
caller who is not a member, ``search_documents`` does the same - but nothing made
a tool do so. A tool that forgot the check still ran, and the only thing standing
between a model and a privileged read was whether the tool's author remembered.
The runner's own comment described the intent ("tools sniff ``__agent_context__``
and re-verify the user's permission") and the module docstring of
:mod:`app.modules.ai_agents.service` promised that "the runner still re-verifies
the invoking user's permission inside each privileged tool". This makes that
promise a property of the platform rather than of each tool.

The gate runs in :meth:`app.modules.ai_agents.base.AgentRunner.run`, on the one
line where a tool is actually invoked, so there is no alternate registry path
that reaches a tool without passing it. It resolves, in order:

1. the **principal**, from the trusted run context only - never from tool
   arguments, because arguments are model-authored;
2. the **required permission**, from :data:`triggers.TOOL_PERMISSIONS`;
3. the principal's **role**, from the database, so the answer honours the
   current role rather than a snapshot taken when the run was queued;
4. the **project scope**, from the trusted context and from any project the
   arguments name, verified through
   :func:`agents._access.assert_user_can_access_project`.

Every one of those steps fails closed. A missing principal, an unknown user, an
unmapped privileged tool, an unparseable project id, or an error anywhere in the
policy evaluation all deny, and the denial happens before the tool runs, so a
denied call has no side effects.

What the gate does **not** do, deliberately: it does not resolve a
resource identifier to its owning project. ``boq_id`` still belongs to the tool,
which is the only place that knows what the identifier refers to. The gate
enforces who the caller is, what they may do, and which projects they may act
in; a tool that accepts a resource id must keep resolving it to a project and
checking membership, as ``_tool_read_boq`` already does.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any, Protocol

from app.modules.ai_agents.triggers import DEFAULT_TOOL_PERMISSION, required_permission_for_tool

logger = logging.getLogger(__name__)

# ── Denial codes ────────────────────────────────────────────────────────────
# Stable strings: they are persisted into the run's observation step and read by
# the frontend and by tests, so they are part of the contract, not log text.
ALLOWED: str = "allowed"
#: No authorizer was supplied, and the tool needs more than the run floor.
NO_AUTHORIZER: str = "authorization_unavailable"
#: The trusted context carried no usable invoking user.
MISSING_PRINCIPAL: str = "missing_principal"
#: The caller's role does not carry the tool's required permission.
PERMISSION_DENIED: str = "permission_denied"
#: The caller may not act in one of the projects the call names.
PROJECT_DENIED: str = "project_access_denied"
#: The arguments tried to supply an identity the runner already owns.
IDENTITY_OVERRIDE: str = "identity_override_rejected"
#: Policy evaluation itself failed. Denied, because an unevaluated policy is not
#: a granted one.
POLICY_ERROR: str = "policy_error"

#: Argument keys that would let a model name the principal it acts as. No
#: registered tool takes them, so their presence is an override attempt rather
#: than a legitimate parameter - and the gate answers it by denying instead of
#: by ignoring it, so the attempt is visible in the run record.
IDENTITY_ARGUMENT_KEYS: tuple[str, ...] = ("user_id", "tenant_id", "actor_id", "on_behalf_of")

#: Argument keys that name a project the call will act in.
PROJECT_ARGUMENT_KEYS: tuple[str, ...] = ("project_id", "project")


@dataclass(frozen=True)
class ToolCallDecision:
    """The outcome of one authorization decision.

    ``reason`` is for logs and tests. It never reaches the model or the persisted
    observation, which carry only ``code`` and ``permission``: a denial must not
    become a channel for describing what the caller almost reached.
    """

    allowed: bool
    code: str
    tool: str
    permission: str
    reason: str = ""


def allow(tool: str, permission: str, reason: str = "") -> ToolCallDecision:
    return ToolCallDecision(True, ALLOWED, tool, permission, reason)


def deny(code: str, tool: str, permission: str, reason: str) -> ToolCallDecision:
    return ToolCallDecision(False, code, tool, permission, reason)


class ToolAuthorizer(Protocol):
    """What the runner needs in order to execute a tool call."""

    async def authorize(
        self,
        *,
        tool_name: str,
        args: dict[str, Any],
        context: dict[str, Any] | None,
    ) -> ToolCallDecision: ...  # pragma: no cover - protocol


# ── Pure decisions (no I/O, so they can be pinned without a database) ───────


def decide_without_authorizer(tool_name: str) -> ToolCallDecision:
    """What the runner does when no authorizer was supplied.

    A tool mapped to a module permission is denied: nobody has established that
    the caller holds it. A tool at the run floor (``ai_agents.run``) is allowed,
    because the route that started the run is itself gated on exactly that
    permission, so the caller demonstrably holds it - this is what keeps a
    prompt-only or read-only agent working on a call site that predates the gate
    instead of failing closed into uselessness.

    The asymmetry is deliberate and is the reason an unmapped *privileged* tool
    is a bug: it would fall to the floor and run unverified. Every registered
    tool is therefore required to name its permission explicitly, which
    ``test_every_registered_tool_names_the_permission_it_requires`` pins.
    """
    required = required_permission_for_tool(tool_name)
    if required == DEFAULT_TOOL_PERMISSION:
        return allow(tool_name, required, "run-floor permission was established at the request edge")
    return deny(
        NO_AUTHORIZER,
        tool_name,
        required,
        f"no authorizer supplied and the tool requires {required}",
    )


def identity_override_in(args: dict[str, Any]) -> list[str]:
    """Identity keys a model tried to supply. Empty when the args are clean."""
    return sorted(key for key in IDENTITY_ARGUMENT_KEYS if key in (args or {}))


def principal_from(context: dict[str, Any] | None) -> uuid.UUID | None:
    """The trusted invoking user, or None.

    Reads only the runner-supplied context. The runner strips any
    ``__agent_context__`` the model put in its tool arguments before re-injecting
    this one, so a non-empty answer here is the real caller - but this function
    is written to be correct even if that stripping were ever removed, by never
    looking at ``args`` at all.
    """
    from app.modules.ai_agents.agents._access import coerce_user_id

    return coerce_user_id(context)


def project_scopes(
    args: dict[str, Any],
    context: dict[str, Any] | None,
) -> list[tuple[str, uuid.UUID | None]]:
    """Every project this call would act in, as ``(origin, parsed_or_None)``.

    Both sources are checked, and for different reasons. The trusted context
    project is the run's scope. A project named in the arguments is where the
    tool will actually look - ``search_documents`` takes one and falls back to
    the context - so verifying only the context would let a model point a tool at
    a project the caller cannot see.

    A value that is present but does not parse is returned as ``None`` rather
    than dropped, so the caller denies on it: a malformed identifier is not a
    reason to skip the check, and a tool handed one would fail anyway.
    """
    scopes: list[tuple[str, uuid.UUID | None]] = []
    seen: set[str] = set()

    def add(origin: str, raw: Any) -> None:
        if raw is None:
            return
        text = str(raw).strip()
        if not text or text in seen:
            return
        seen.add(text)
        try:
            scopes.append((origin, uuid.UUID(text)))
        except (ValueError, AttributeError, TypeError):
            scopes.append((origin, None))

    if isinstance(context, dict):
        for key in PROJECT_ARGUMENT_KEYS:
            add(f"context.{key}", context.get(key))
    for key in PROJECT_ARGUMENT_KEYS:
        if key in (args or {}):
            add(f"args.{key}", args.get(key))
    return scopes


def log_denial(decision: ToolCallDecision, *, user_id: Any = None, scopes: Any = None) -> None:
    """Record a denial in the repository's structured-log style.

    At WARNING, unlike :class:`app.dependencies.RequirePermission`, which logs
    its denials at DEBUG because a viewer navigating the UI routinely hits gated
    routes. A denied *tool* call is not routine: either an agent was granted a
    tool its operator should not have been able to grant, or a model tried to
    reach something the caller cannot. Both are worth an operator's attention and
    neither happens at navigation frequency.

    The tool's arguments are never logged. They are model-authored and routinely
    carry pasted document text, descriptions and identifiers that belong to the
    project, and a denial is exactly the case where the content is least
    trustworthy. Names, codes and ids are enough to triage.
    """
    logger.warning(
        "ai_agent.tool_denied tool=%s code=%s permission=%s user=%s projects=%s",
        decision.tool,
        decision.code,
        decision.permission,
        user_id if user_id is not None else "-",
        ",".join(str(s[1]) for s in scopes) if scopes else "-",
    )


# ── The production authorizer ───────────────────────────────────────────────


class SessionToolAuthorizer:
    """Authorizes tool calls against the live role registry and project membership.

    Constructed by the call site that runs the agent, which is the only place
    that knows which database session the run owns. Pass that session to reuse
    it; omit it and each decision opens one, which is what a scheduled or
    event-triggered run wants.

    The two database questions are split out as methods so a test can answer them
    without a cluster, which matters because the answers, not the querying, are
    the policy.
    """

    def __init__(self, session: Any | None = None) -> None:
        self._session = session

    async def authorize(
        self,
        *,
        tool_name: str,
        args: dict[str, Any],
        context: dict[str, Any] | None,
    ) -> ToolCallDecision:
        required = required_permission_for_tool(tool_name)

        # A model that tries to name its own principal is denied outright rather
        # than silently corrected: the attempt is the interesting part, and
        # ignoring it would leave the run record looking like an ordinary call.
        override = identity_override_in(args)
        if override:
            return deny(
                IDENTITY_OVERRIDE,
                tool_name,
                required,
                f"tool arguments supplied identity keys {override}",
            )

        user_id = principal_from(context)
        if user_id is None:
            return deny(MISSING_PRINCIPAL, tool_name, required, "no trusted invoking user in the run context")

        scopes = project_scopes(args, context)

        try:
            async with self._session_scope() as session:
                role = await self._role_of(session, user_id)
                if not role:
                    return deny(MISSING_PRINCIPAL, tool_name, required, "invoking user is unknown")

                from app.core.permissions import permission_registry

                if not permission_registry.role_has_permission(role, required):
                    return deny(
                        PERMISSION_DENIED,
                        tool_name,
                        required,
                        f"role {role!r} does not carry {required}",
                    )

                for origin, project_id in scopes:
                    if project_id is None:
                        return deny(
                            PROJECT_DENIED,
                            tool_name,
                            required,
                            f"{origin} is not a usable project id",
                        )
                    if not await self._project_access(session, project_id, user_id):
                        return deny(
                            PROJECT_DENIED,
                            tool_name,
                            required,
                            f"caller may not act in the project named by {origin}",
                        )
        except Exception as exc:  # noqa: BLE001 - an unevaluated policy is not a grant
            logger.warning(
                "ai_agent.tool_authorization_error tool=%s user=%s error=%s",
                tool_name,
                user_id,
                type(exc).__name__,
            )
            return deny(POLICY_ERROR, tool_name, required, "policy evaluation failed")

        return allow(tool_name, required, "role carries the permission and every named project is in scope")

    # ── seams ────────────────────────────────────────────────────────────

    async def _role_of(self, session: Any, user_id: uuid.UUID) -> str | None:
        """The caller's current role, or None when the user is unknown."""
        from app.modules.users.repository import UserRepository

        user = await UserRepository(session).get_by_id(user_id)
        role = getattr(user, "role", None) if user is not None else None
        return str(role) if role else None

    async def _project_access(self, session: Any, project_id: uuid.UUID, user_id: uuid.UUID) -> bool:
        """Owner-or-member, fail closed. The same rule the tools already applied."""
        from app.modules.ai_agents.agents._access import assert_user_can_access_project

        return await assert_user_can_access_project(session, project_id, user_id)

    def _session_scope(self) -> Any:
        """Reuse the run's session when one was given, else open one."""
        if self._session is not None:
            return _reuse(self._session)

        from app.database import async_session_factory

        return async_session_factory()


class _reuse:
    """Adapt an existing session to the async-context-manager shape."""

    def __init__(self, session: Any) -> None:
        self._session = session

    async def __aenter__(self) -> Any:
        return self._session

    async def __aexit__(self, *exc: Any) -> bool:
        # Not ours to close: the run opened it and the run closes it.
        return False
