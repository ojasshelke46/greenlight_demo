"""Registries features plug into. Features register from their package __init__; the core calls run_*/build_*."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RunInfo:
    id: str
    owner: str
    repo: str
    mode: str
    via_fork: bool


@dataclass(frozen=True)
class PendingActionInfo:
    tool_name: str
    arguments: Any


@dataclass(frozen=True)
class PriorApproval:
    approver: str
    decision: str
    decided_at: str
    # "accepted", "needs_more: <reason>" or "refused: <reason>"
    result: str


@dataclass(frozen=True)
class ApprovalContext:
    run: RunInfo
    pending_action: PendingActionInfo
    approver: str
    prior_approvals: list[PriorApproval] = field(default_factory=list)


@dataclass(frozen=True)
class Allow:
    pass


@dataclass(frozen=True)
class Deny:
    reason: str


@dataclass(frozen=True)
class NeedMore:
    reason: str


CheckResult = Allow | Deny | NeedMore
ApprovalCheck = Callable[[ApprovalContext], Awaitable[CheckResult]]
RunMessagePart = Callable[[str, str], Awaitable[str | None]]

_approval_checks: list[ApprovalCheck] = []
_run_message_parts: list[RunMessagePart] = []


def register_approval_check(fn: ApprovalCheck) -> ApprovalCheck:
    if fn not in _approval_checks:
        _approval_checks.append(fn)
    return fn


def register_run_message_part(fn: RunMessagePart) -> RunMessagePart:
    if fn not in _run_message_parts:
        _run_message_parts.append(fn)
    return fn


async def run_approval_checks(ctx: ApprovalContext) -> CheckResult:
    """First Deny, else first NeedMore, else Allow, in registration order.

    A check that raises or returns something else counts as a Deny: this gates an irreversible merge.
    """
    results = await asyncio.gather(*(check(ctx) for check in _approval_checks), return_exceptions=True)

    verdicts: list[CheckResult] = []
    for check, result in zip(_approval_checks, results):
        name = getattr(check, "__qualname__", repr(check))
        if isinstance(result, BaseException):
            logger.error("Approval check %s failed", name, exc_info=result)
            verdicts.append(Deny(f"Approval check {name} failed"))
        elif isinstance(result, (Allow, Deny, NeedMore)):
            verdicts.append(result)
        else:
            logger.error("Approval check %s returned %r", name, result)
            verdicts.append(Deny(f"Approval check {name} returned no verdict"))

    for kind in (Deny, NeedMore):
        for verdict in verdicts:
            if isinstance(verdict, kind):
                return verdict
    return Allow()


async def build_run_message_extras(owner: str, repo: str) -> str:
    """Non null parts in registration order, one per line. A part that raises is logged and left out."""
    results = await asyncio.gather(*(part(owner, repo) for part in _run_message_parts), return_exceptions=True)

    lines = []
    for part, result in zip(_run_message_parts, results):
        if isinstance(result, BaseException):
            logger.error("Run message part %s failed", getattr(part, "__qualname__", repr(part)), exc_info=result)
        elif result is not None:
            lines.append(result)
    return "\n".join(lines)
