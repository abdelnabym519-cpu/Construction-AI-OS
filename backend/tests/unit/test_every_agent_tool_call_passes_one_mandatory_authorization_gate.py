# DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
# Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
"""Every agent tool call passes one mandatory authorization gate.

Before the gate, ``AgentRunner.run`` forwarded a trusted context to the tool and
each tool that cared re-verified the caller. That discipline was real -
``estimate_reviewer._tool_read_boq`` resolves a BOQ to its project and refuses a
foreign user - but it was a convention, and a tool that did not implement it
still ran with whatever arguments the model chose. Authorization now happens in
the runner, once, before ``tool.run`` is reached, so a protected call cannot be
made without it.

These tests answer the questions that decide whether that claim is true:

* an authorized call still reaches the tool, unchanged;
* an unauthorized one is refused **before** the tool runs, so it has no side
  effect to undo;
* the refusal is a structured observation the model can reason about, not an
  exception that kills the run;
* identity and scope come from the trusted context only - arguments that try to
  name a principal or point at another project are refused, not silently
  corrected;
* a missing principal, an unmapped permission or a policy evaluation that blows
  up all deny, because an unevaluated policy is not a granted one;
* denials are recorded where an operator will see them, without the arguments;
* and the two invariants that keep this from rotting: every registered tool
  names the permission it requires, and every production call site that runs an
  agent supplies an authorizer.

The database questions (what is this user's role, may they see this project) are
answered by a stub, because the *answers* are the policy and a cluster is not
needed to pin them. The permission check itself is never stubbed: it runs through
the real :data:`app.core.permissions.permission_registry`, so a test that says
"this role may call this tool" is asserting the platform's actual policy.
"""

from __future__ import annotations

import ast
import logging
import uuid
from pathlib import Path
from typing import Any

import pytest

from app.core.permissions import Role, permission_registry
from app.modules.ai_agents.base import Agent, AgentRunner, ToolRegistry
from app.modules.ai_agents.llm import ScriptedLLM
from app.modules.ai_agents.tool_authorization import (
    DEFAULT_TOOL_PERMISSION,
    IDENTITY_ARGUMENT_KEYS,
    IDENTITY_OVERRIDE,
    MISSING_PRINCIPAL,
    NO_AUTHORIZER,
    PERMISSION_DENIED,
    POLICY_ERROR,
    PROJECT_DENIED,
    SessionToolAuthorizer,
    decide_without_authorizer,
    identity_override_in,
    principal_from,
    project_scopes,
)
from app.modules.ai_agents.triggers import TOOL_PERMISSIONS

# ── Fixtures and doubles ────────────────────────────────────────────────────

#: Any value that is not ``DEFAULT_LLM_STEP_TIMEOUT`` (45.0). ``effective_step_
#: timeout`` returns an agent's own cap untouched and only reaches
#: ``app.modules.ai.ai_client`` - a module that needs Python 3.12 syntax - for
#: the default. Setting a cap here is a supported agent configuration, and it
#: keeps this suite runnable on every supported interpreter rather than only the
#: newest one. It is not what these tests are about.
_STEP_TIMEOUT = 30.0

#: A value that must never reach a log line or a model-visible payload.
_SECRET = "paste-of-a-tenant-price-file-8f31c2"

_USER = uuid.uuid4()
_OTHER_USER = uuid.uuid4()
_OWN_PROJECT = uuid.uuid4()
_FOREIGN_PROJECT = uuid.uuid4()


@pytest.fixture(autouse=True)
def _registered_permissions() -> None:
    """Register the permissions these tests reason about.

    Idempotent, in the shape ``test_agent_automation`` already uses: an unknown
    permission denies by default, so a test that forgot to register one would
    read as a policy result instead of a missing fixture.
    """
    for permission, minimum in (
        ("costs.read", Role.VIEWER),
        ("boq.read", Role.VIEWER),
        ("ai_agents.run", Role.EDITOR),
        ("ai_estimator.run", Role.EDITOR),
    ):
        if not permission_registry.has(permission):
            permission_registry.register(permission, minimum)


class _RecordingTool:
    """A :class:`Tool` that records calls and touches no database."""

    def __init__(self, name: str, *, raises: Exception | None = None) -> None:
        self.name = name
        self.description = f"records calls to {name}"
        self.input_schema: dict[str, Any] = {"type": "object", "properties": {}}
        self.calls: list[dict[str, Any]] = []
        self._raises = raises

    async def run(self, args: dict[str, Any]) -> Any:
        self.calls.append(args)
        if self._raises is not None:
            raise self._raises
        return {"ok": True, "tool": self.name}


class _StubAuthorizer(SessionToolAuthorizer):
    """The production authorizer with its two database questions answered here.

    ``_role_of`` and ``_project_access`` are the seams the class exposes for
    exactly this; everything above them - identity override detection, principal
    resolution, scope collection, the permission check, the fail-closed wrapping
    - is the real code.
    """

    def __init__(
        self,
        *,
        role: str | None = "editor",
        allowed_projects: tuple[uuid.UUID, ...] = (),
        role_lookup_raises: bool = False,
    ) -> None:
        super().__init__(session=object())  # never used: both seams are overridden
        self._role = role
        self._allowed_projects = {str(p) for p in allowed_projects}
        self._role_lookup_raises = role_lookup_raises
        self.project_checks: list[tuple[str, str]] = []

    async def _role_of(self, session: Any, user_id: uuid.UUID) -> str | None:
        if self._role_lookup_raises:
            msg = "role lookup exploded"
            raise RuntimeError(msg)
        return self._role

    async def _project_access(self, session: Any, project_id: uuid.UUID, user_id: uuid.UUID) -> bool:
        self.project_checks.append((str(project_id), str(user_id)))
        return str(project_id) in self._allowed_projects


def _agent(tools: list[_RecordingTool], *, name: str = "gate_test_agent") -> Agent:
    return Agent(
        name=name,
        system_prompt="Test agent for the authorization gate.",
        allowed_tools=[t.name for t in tools],
        max_iterations=4,
        llm_step_timeout=_STEP_TIMEOUT,
    )


def _registry(tools: list[_RecordingTool]) -> ToolRegistry:
    registry = ToolRegistry()
    for tool in tools:
        registry.register(tool)
    return registry


def _tool_call(name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"type": "tool_call", "name": name, "args": args or {}, "thought": f"Calling {name}."}


def _final(text: str) -> dict[str, Any]:
    return {"type": "final", "text": text}


def _context(**extra: str) -> dict[str, str]:
    """The trusted context a call site builds: the authenticated user, plus scope."""
    context = {"user_id": str(_USER), **extra}
    return context


async def _run(
    tools: list[_RecordingTool],
    script: list[dict[str, Any]],
    *,
    context: dict[str, Any] | None,
    authorizer: Any = None,
) -> Any:
    runner = AgentRunner(ScriptedLLM(script=script, tokens_per_call=5))
    return await runner.run(
        _agent(tools),
        "do the thing",
        context=context,
        tool_registry=_registry(tools),
        authorizer=authorizer,
    )


def _observations(result: Any) -> list[dict[str, Any]]:
    return [step.content for step in result.steps if step.role == "observation"]


# ── Authorized calls keep working ───────────────────────────────────────────


async def test_an_authorized_call_reaches_the_tool_and_returns_its_observation() -> None:
    """The gate is not a speed bump on the happy path."""
    tool = _RecordingTool("search_costs")
    authorizer = _StubAuthorizer(role="editor", allowed_projects=(_OWN_PROJECT,))

    result = await _run(
        [tool],
        [_tool_call("search_costs", {"query": "in-situ concrete"}), _final("Here are the rates.")],
        context=_context(project_id=str(_OWN_PROJECT)),
        authorizer=authorizer,
    )

    assert result.status == "completed"
    assert result.final_output == "Here are the rates."
    assert len(tool.calls) == 1
    # The tool still receives the trusted context, so its own resource-level
    # checks (resolving a boq_id to its project, say) keep working unchanged.
    assert tool.calls[0]["__agent_context__"] == {
        "user_id": str(_USER),
        "project_id": str(_OWN_PROJECT),
    }
    assert _observations(result)[-1] == {"ok": True, "tool": "search_costs"}


async def test_a_viewer_level_tool_runs_for_a_viewer() -> None:
    """``costs.read`` is VIEWER, so a viewer keeps the cost tools they always had."""
    tool = _RecordingTool("search_costs")

    result = await _run(
        [tool],
        [_tool_call("search_costs"), _final("done")],
        context=_context(),
        authorizer=_StubAuthorizer(role="viewer"),
    )

    assert result.status == "completed"
    assert len(tool.calls) == 1


async def test_a_multi_tool_run_is_unaffected_by_the_gate() -> None:
    """An ordinary two-tool ReAct loop behaves exactly as it did before."""
    first = _RecordingTool("search_costs")
    second = _RecordingTool("read_boq")
    authorizer = _StubAuthorizer(role="manager", allowed_projects=(_OWN_PROJECT,))

    result = await _run(
        [first, second],
        [
            _tool_call("search_costs", {"query": "blockwork"}),
            _tool_call("read_boq", {"boq_id": str(uuid.uuid4())}),
            _final("Both reads succeeded."),
        ],
        context=_context(project_id=str(_OWN_PROJECT)),
        authorizer=authorizer,
    )

    assert result.status == "completed"
    assert result.iterations == 3
    assert len(first.calls) == 1
    assert len(second.calls) == 1
    assert len(_observations(result)) == 2


# ── Unauthorized calls are refused centrally, before any side effect ────────


async def test_a_role_that_lacks_the_tools_permission_is_denied_before_the_tool_runs() -> None:
    """``estimator_flag_for_human`` needs ``ai_estimator.run`` (EDITOR); a viewer has not got it."""
    tool = _RecordingTool("estimator_flag_for_human")

    result = await _run(
        [tool],
        [_tool_call("estimator_flag_for_human"), _final("I could not flag that.")],
        context=_context(),
        authorizer=_StubAuthorizer(role="viewer"),
    )

    assert tool.calls == []  # denied before the call, so nothing to undo
    observation = _observations(result)[-1]
    assert observation["error"] == PERMISSION_DENIED
    assert observation["required_permission"] == "ai_estimator.run"
    # The run itself survives: the model is told, and answers.
    assert result.status == "completed"
    assert result.final_output == "I could not flag that."


async def test_a_role_below_the_hierarchy_floor_is_denied_a_viewer_level_tool() -> None:
    """Field roles rank under VIEWER, so even a read-only tool is out of reach."""
    tool = _RecordingTool("search_costs")

    result = await _run(
        [tool],
        [_tool_call("search_costs"), _final("No access.")],
        context=_context(),
        authorizer=_StubAuthorizer(role="field_worker"),
    )

    assert tool.calls == []
    assert _observations(result)[-1]["error"] == PERMISSION_DENIED


async def test_a_run_with_no_trusted_user_is_denied() -> None:
    """No principal means no decision can be made, which means no tool call."""
    tool = _RecordingTool("search_costs")

    result = await _run(
        [tool],
        [_tool_call("search_costs"), _final("I do not know who is asking.")],
        context={},
        authorizer=_StubAuthorizer(role="admin"),
    )

    assert tool.calls == []
    assert _observations(result)[-1]["error"] == MISSING_PRINCIPAL


async def test_an_invoking_user_that_no_longer_exists_is_denied() -> None:
    """A deleted or unresolvable user has no role, and no role means no grant."""
    tool = _RecordingTool("search_costs")

    result = await _run(
        [tool],
        [_tool_call("search_costs"), _final("No access.")],
        context=_context(),
        authorizer=_StubAuthorizer(role=None),
    )

    assert tool.calls == []
    assert _observations(result)[-1]["error"] == MISSING_PRINCIPAL


async def test_a_project_the_caller_cannot_see_is_denied_even_though_the_run_has_one() -> None:
    """The arguments name where the tool will look, so that project is checked too."""
    tool = _RecordingTool("search_documents")
    authorizer = _StubAuthorizer(role="admin", allowed_projects=(_OWN_PROJECT,))

    result = await _run(
        [tool],
        [_tool_call("search_documents", {"project_id": str(_FOREIGN_PROJECT)}), _final("No access.")],
        context=_context(project_id=str(_OWN_PROJECT)),
        authorizer=authorizer,
    )

    assert tool.calls == []
    assert _observations(result)[-1]["error"] == PROJECT_DENIED
    # Both projects were asked about, and the foreign one is what refused.
    assert (str(_OWN_PROJECT), str(_USER)) in authorizer.project_checks
    assert (str(_FOREIGN_PROJECT), str(_USER)) in authorizer.project_checks


async def test_a_project_the_caller_cannot_see_in_the_run_context_is_denied() -> None:
    """Scope comes from the run as well: a scheduled run cannot act outside it."""
    tool = _RecordingTool("project_cost_summary")

    result = await _run(
        [tool],
        [_tool_call("project_cost_summary"), _final("No access.")],
        context=_context(project_id=str(_FOREIGN_PROJECT)),
        authorizer=_StubAuthorizer(role="admin", allowed_projects=(_OWN_PROJECT,)),
    )

    assert tool.calls == []
    assert _observations(result)[-1]["error"] == PROJECT_DENIED


async def test_a_malformed_project_identifier_fails_closed_instead_of_being_skipped() -> None:
    """An unparseable id is not a reason to drop the check."""
    tool = _RecordingTool("search_documents")

    result = await _run(
        [tool],
        [_tool_call("search_documents", {"project_id": "not-a-uuid"}), _final("No access.")],
        context=_context(),
        authorizer=_StubAuthorizer(role="admin", allowed_projects=(_OWN_PROJECT,)),
    )

    assert tool.calls == []
    assert _observations(result)[-1]["error"] == PROJECT_DENIED


async def test_a_policy_evaluation_that_raises_is_denied_not_allowed() -> None:
    """An unevaluated policy is not a granted one."""
    tool = _RecordingTool("search_costs")

    result = await _run(
        [tool],
        [_tool_call("search_costs"), _final("No access.")],
        context=_context(),
        authorizer=_StubAuthorizer(role_lookup_raises=True),
    )

    assert tool.calls == []
    assert _observations(result)[-1]["error"] == POLICY_ERROR


# ── Caller-supplied arguments cannot retarget the call ──────────────────────


async def test_a_forged_agent_context_is_stripped_and_the_trusted_one_is_used() -> None:
    """The model may not install its own identity by writing the context key."""
    tool = _RecordingTool("search_costs")
    authorizer = _StubAuthorizer(role="editor")

    result = await _run(
        [tool],
        [
            _tool_call(
                "search_costs",
                {"query": "concrete", "__agent_context__": {"user_id": str(_OTHER_USER)}},
            ),
            _final("done"),
        ],
        context=_context(),
        authorizer=authorizer,
    )

    assert len(tool.calls) == 1
    assert tool.calls[0]["__agent_context__"] == {"user_id": str(_USER)}
    assert str(_OTHER_USER) not in str(tool.calls[0])
    # And the gate decided about the real caller, not the forged one.
    assert authorizer.project_checks == []


@pytest.mark.parametrize("key", sorted(IDENTITY_ARGUMENT_KEYS))
async def test_identity_keys_in_the_arguments_are_denied_rather_than_ignored(key: str) -> None:
    """No registered tool takes these, so their presence is an override attempt.

    Refusing beats correcting: silently dropping the key would leave the run
    record looking like an ordinary call, and the attempt is the interesting part.
    """
    tool = _RecordingTool("search_costs")

    result = await _run(
        [tool],
        [_tool_call("search_costs", {key: str(_OTHER_USER)}), _final("No access.")],
        context=_context(),
        authorizer=_StubAuthorizer(role="admin"),
    )

    assert tool.calls == []
    assert _observations(result)[-1]["error"] == IDENTITY_OVERRIDE


async def test_a_forged_tenant_identifier_does_not_widen_the_call() -> None:
    """``tenant_id`` is an identity key here, and the runner never takes one from a model."""
    tool = _RecordingTool("search_costs")

    result = await _run(
        [tool],
        [_tool_call("search_costs", {"tenant_id": str(uuid.uuid4())}), _final("No access.")],
        context=_context(),
        authorizer=_StubAuthorizer(role="admin"),
    )

    assert tool.calls == []
    observation = _observations(result)[-1]
    assert observation["error"] == IDENTITY_OVERRIDE
    assert observation["required_permission"] == "costs.read"


# ── What a denial looks like, and what it must not leak ─────────────────────


async def test_a_denial_carries_the_code_and_the_permission_and_nothing_else() -> None:
    """The payload the model sees must not describe what the caller almost reached."""
    tool = _RecordingTool("estimator_flag_for_human")

    result = await _run(
        [tool],
        [_tool_call("estimator_flag_for_human", {"note": _SECRET}), _final("No access.")],
        context=_context(),
        authorizer=_StubAuthorizer(role="viewer"),
    )

    observation = _observations(result)[-1]
    assert set(observation) == {"name", "error", "required_permission"}
    assert observation["name"] == "estimator_flag_for_human"
    assert _SECRET not in str(observation)


async def test_a_denial_is_logged_at_warning_without_the_tool_arguments(caplog: pytest.LogCaptureFixture) -> None:
    """WARNING, unlike ``RequirePermission``'s DEBUG: a denied tool call is not routine."""
    tool = _RecordingTool("estimator_flag_for_human")

    with caplog.at_level(logging.WARNING, logger="app.modules.ai_agents.tool_authorization"):
        await _run(
            [tool],
            [_tool_call("estimator_flag_for_human", {"note": _SECRET}), _final("No access.")],
            context=_context(),
            authorizer=_StubAuthorizer(role="viewer"),
        )

    denials = [r for r in caplog.records if "ai_agent.tool_denied" in r.getMessage()]
    assert len(denials) == 1
    message = denials[0].getMessage()
    assert denials[0].levelno == logging.WARNING
    assert "tool=estimator_flag_for_human" in message
    assert f"code={PERMISSION_DENIED}" in message
    assert "permission=ai_estimator.run" in message
    assert f"user={_USER}" in message
    # Enough to triage, and no model-authored content: arguments routinely carry
    # pasted document text that belongs to the project.
    assert _SECRET not in message
    assert _SECRET not in caplog.text


async def test_the_denied_attempt_is_still_recorded_in_the_run_timeline() -> None:
    """A denial is an outcome, not a disappearance: the tool_call step stays."""
    tool = _RecordingTool("estimator_flag_for_human")

    result = await _run(
        [tool],
        [_tool_call("estimator_flag_for_human"), _final("No access.")],
        context=_context(),
        authorizer=_StubAuthorizer(role="viewer"),
    )

    roles = [step.role for step in result.steps]
    assert roles == ["thought", "tool_call", "observation", "answer"]


async def test_an_authorization_denial_is_distinguishable_from_the_other_two_failures() -> None:
    """Refused, unknown and broken are three different facts and stay three payloads."""
    denied = _RecordingTool("estimator_flag_for_human")
    denied_result = await _run(
        [denied],
        [_tool_call("estimator_flag_for_human"), _final("No access.")],
        context=_context(),
        authorizer=_StubAuthorizer(role="viewer"),
    )

    unknown = _RecordingTool("search_costs")
    unknown_result = await _run(
        [unknown],
        [_tool_call("no_such_tool"), _final("No such tool.")],
        context=_context(),
        authorizer=_StubAuthorizer(role="admin"),
    )

    broken = _RecordingTool("search_costs", raises=RuntimeError("boom"))
    broken_result = await _run(
        [broken],
        [_tool_call("search_costs"), _final("It failed.")],
        context=_context(),
        authorizer=_StubAuthorizer(role="admin"),
    )

    authorization = _observations(denied_result)[-1]
    not_found = _observations(unknown_result)[-1]
    execution = _observations(broken_result)[-1]

    assert authorization["error"] == PERMISSION_DENIED
    assert not_found["error"] == "unknown_tool"
    assert "boom" in str(execution["error"])
    assert len({authorization["error"], not_found["error"], execution["error"]}) == 3
    # Only a refusal can name the permission that was missing.
    assert "required_permission" in authorization
    assert "required_permission" not in not_found
    assert "required_permission" not in execution
    # And only a refusal leaves the tool untouched.
    assert denied.calls == []
    assert len(broken.calls) == 1


# ── Without an authorizer the gate still decides ────────────────────────────


async def test_without_an_authorizer_a_tool_above_the_run_floor_is_denied() -> None:
    """Omitting the authorizer is not a way to switch the gate off."""
    tool = _RecordingTool("search_costs")

    result = await _run([tool], [_tool_call("search_costs"), _final("No access.")], context=_context())

    assert tool.calls == []
    assert _observations(result)[-1]["error"] == NO_AUTHORIZER


async def test_without_an_authorizer_a_tool_at_the_run_floor_still_runs() -> None:
    """The floor permission was established at the request edge, so it needs no re-check.

    This is what keeps a prompt-only or test-double agent working on a call site
    that predates the gate - and it is why an unmapped *privileged* tool would be
    a bug, which the completeness test below pins.
    """
    tool = _RecordingTool("echo")

    result = await _run([tool], [_tool_call("echo", {"message": "hi"}), _final("hi back")], context=_context())

    assert len(tool.calls) == 1
    assert result.status == "completed"


# ── The invariants that keep the gate from rotting ──────────────────────────


async def test_every_registered_tool_names_the_permission_it_requires() -> None:
    """No tool may fall through to the default by being forgotten.

    ``TOOL_PERMISSIONS`` is the contract the gate reads at execution time, the
    tool picker renders, and ``set_tools`` enforces. A tool missing from it would
    run at ``ai_agents.run`` for anyone who can start an agent at all, so a new
    tool has to arrive with its permission named - or this test says so.
    """
    import app.modules.ai_agents as ai_agents_module
    import app.modules.ai_estimator as ai_estimator_module
    from app.modules.ai_agents.base import global_tool_registry

    await ai_agents_module.on_startup()
    await ai_estimator_module.on_startup()

    registered = {tool.name for tool in global_tool_registry.all()}
    assert registered, "the global registry should hold the built-in tools"
    assert sorted(registered - set(TOOL_PERMISSIONS)) == []
    # The other direction matters too: a mapping for a tool that no longer
    # exists is a stale contract the frontend would still render.
    assert sorted(set(TOOL_PERMISSIONS) - registered) == []


def test_every_production_run_call_site_supplies_an_authorizer() -> None:
    """A new entry point cannot quietly arrive without the gate.

    ``AgentRunner.run`` takes ``context`` keyword-only, so a ``.run(...)`` call
    passing ``context=`` is an agent execution site. Each one must pass an
    authorizer: the three that exist today are the HTTP background task, the
    service path the scheduler and the event triggers use, and the precise-match
    agent in the estimator module.

    Only files that could contain such a call are parsed. That is not a way to
    look the other way: a candidate that fails to parse fails this test, so
    nothing can hide behind a syntax the running interpreter does not accept.
    """
    app_root = Path(__file__).resolve().parents[2] / "app"
    offenders: list[str] = []
    unparsable: list[str] = []
    sites = 0

    for path in sorted(app_root.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        # A runner is either constructed here or handed in; either way the file
        # mentions the class, or it calls ``.run(...)`` with a context.
        if "AgentRunner" not in source and not (".run(" in source and "context=" in source):
            continue
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError as exc:  # a candidate we cannot read is not a pass
            unparsable.append(f"{path.relative_to(app_root.parent)}:{exc.lineno}")
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Attribute) or node.func.attr != "run":
                continue
            keywords = {kw.arg for kw in node.keywords}
            if "context" not in keywords:
                continue
            sites += 1
            if "authorizer" not in keywords:
                offenders.append(f"{path.relative_to(app_root.parent)}:{node.lineno}")

    assert unparsable == [], f"could not inspect candidate file(s): {unparsable}"
    assert sites >= 3, f"expected the known agent run sites, found {sites}"
    assert offenders == [], f"agent run site(s) without an authorizer: {offenders}"


# ── Pure decision helpers, pinned without a runner ──────────────────────────


def test_decide_without_authorizer_applies_the_documented_floor_rule() -> None:
    assert decide_without_authorizer("search_costs").allowed is False
    assert decide_without_authorizer("search_costs").code == NO_AUTHORIZER

    floor = decide_without_authorizer("echo")
    assert floor.allowed is True
    assert floor.permission == DEFAULT_TOOL_PERMISSION


def test_decide_without_authorizer_denies_every_tool_that_names_a_real_permission() -> None:
    """All fifteen registered tools are above the floor, so none runs unverified."""
    for tool_name, permission in TOOL_PERMISSIONS.items():
        decision = decide_without_authorizer(tool_name)
        assert decision.allowed is False, tool_name
        assert decision.permission == permission, tool_name


def test_principal_from_reads_only_the_trusted_context() -> None:
    assert principal_from({"user_id": str(_USER)}) == _USER
    assert principal_from(None) is None
    assert principal_from({}) is None
    assert principal_from({"user_id": "not-a-uuid"}) is None
    assert principal_from({"user_id": ""}) is None


def test_project_scopes_checks_the_context_project_and_the_argument_project() -> None:
    scopes = project_scopes({"project_id": str(_FOREIGN_PROJECT)}, {"project_id": str(_OWN_PROJECT)})
    assert scopes == [("context.project_id", _OWN_PROJECT), ("args.project_id", _FOREIGN_PROJECT)]


def test_project_scopes_deduplicates_one_project_named_twice() -> None:
    assert project_scopes({"project_id": str(_OWN_PROJECT)}, {"project_id": str(_OWN_PROJECT)}) == [
        ("context.project_id", _OWN_PROJECT),
    ]


def test_project_scopes_keeps_an_unparseable_identifier_so_the_caller_denies_on_it() -> None:
    assert project_scopes({"project_id": "seventeen"}, None) == [("args.project_id", None)]


def test_project_scopes_ignores_keys_that_are_not_project_scope() -> None:
    assert project_scopes({"region": "EG", "query": "concrete", "boq_id": str(uuid.uuid4())}, {}) == []


def test_identity_override_in_flags_the_keys_a_model_could_use_to_name_a_principal() -> None:
    assert identity_override_in({}) == []
    assert identity_override_in({"project_id": str(_OWN_PROJECT)}) == []
    assert identity_override_in({"user_id": str(_OTHER_USER)}) == ["user_id"]
    assert identity_override_in({"tenant_id": "t", "actor_id": "a"}) == ["actor_id", "tenant_id"]


def test_the_denial_reason_never_becomes_part_of_the_decision_payload() -> None:
    """``reason`` is for logs and tests; the observation carries code and permission."""
    decision = decide_without_authorizer("search_costs")
    assert decision.reason
    assert decision.code == NO_AUTHORIZER
    assert decision.tool == "search_costs"
