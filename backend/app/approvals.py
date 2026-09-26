"""Server side checks that gate the one irreversible step: merging a Greenlight PR."""

from typing import Any

import httpx

from app.github import GitHubClient
from app.hooks import ApprovalContext, PendingActionInfo, PriorApproval, RunInfo
from app.ledger import Ledger
from app.tools import ResolvedToolCall, resolve_tool_call
from app.trueforge import PendingAction, TrueForgeClient, TrueForgeError

MERGE_SERVER = "github"
MERGE_TOOL = "merge_pull_request"
HEAD_REF_PREFIX = "greenlight/"


NEEDS_MORE_PREFIX = "needs_more: "


class ApprovalRefused(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


async def approvals_for_run(ledger: Ledger, run_id: str) -> list[PriorApproval]:
    """Every recorded approval attempt for the run, oldest first."""
    cursor = await ledger.db.execute(
        "SELECT approver, decision, decided_at, result FROM approvals WHERE run_id = ? ORDER BY id", (run_id,)
    )
    return [
        PriorApproval(approver=row["approver"], decision=row["decision"], decided_at=row["decided_at"], result=row["result"])
        for row in await cursor.fetchall()
    ]


def approver_decision(prior: list[PriorApproval], approver: str) -> PriorApproval | None:
    """The approver's standing decision on this run: accepted wins over needs more. Refusals never stand."""
    mine = [p for p in prior if p.approver == approver]
    accepted = [p for p in mine if p.result == "accepted"]
    if accepted:
        return accepted[0]
    waiting = [p for p in mine if p.result.startswith(NEEDS_MORE_PREFIX)]
    return waiting[-1] if waiting else None


def approval_context(run: dict[str, Any], tool: ResolvedToolCall, approver: str, prior: list[PriorApproval]) -> ApprovalContext:
    owner, repo = run["repo"].split("/", 1)
    return ApprovalContext(
        run=RunInfo(id=run["id"], owner=owner, repo=repo, mode=run["mode"], via_fork=run["via_fork"]),
        pending_action=PendingActionInfo(tool_name=tool.name or "unknown", arguments=tool.arguments),
        approver=approver,
        prior_approvals=prior,
    )


async def load_pending(
    run: dict[str, Any], ledger: Ledger, trueforge: TrueForgeClient
) -> tuple[PendingAction, ResolvedToolCall]:
    """Check 1: exactly one pending action on a paused turn. Returns it as TrueForge stored it."""
    pending = run["pending_action"]
    if run["status"] != "awaiting_approval" or not pending:
        raise ApprovalRefused(f"Run has no pending action (status is {run['status']})")

    calls = pending.get("tool_calls") or []
    if len(calls) != 1:
        raise ApprovalRefused(f"Run has {len(calls)} pending actions; exactly one is required")

    base = await ledger.turn_base(run["id"], pending["turn_id"])
    if not await ledger.has_event(run["id"], "turn.done", after=base):
        raise ApprovalRefused("The agent has not finished pausing yet; try again in a moment")

    # Validate what TrueForge will actually run on approval, not our streamed copy of it.
    ref = calls[0]
    try:
        call = await trueforge.get_tool_call(
            run["session_id"], pending["turn_id"], ref["tool_call_id"], ref.get("source_event_id") or ""
        )
    except (TrueForgeError, httpx.HTTPError) as exc:
        raise ApprovalRefused(f"Could not read the pending tool call from TrueForge: {exc}") from exc
    if call is None:
        raise ApprovalRefused("TrueForge has no record of the pending tool call")

    action = PendingAction(turn_id=pending["turn_id"], thread_id=pending["thread_id"], tool_call_ids=[ref["tool_call_id"]])
    return action, resolve_tool_call(call)


async def check_merge(run: dict[str, Any], tool: ResolvedToolCall, github: GitHubClient) -> None:
    """Checks 2 to 5, all against live GitHub state."""
    if tool.server != MERGE_SERVER or tool.name != MERGE_TOOL:
        raise ApprovalRefused(f"Pending tool is {tool.server}/{tool.name}; only {MERGE_SERVER}/{MERGE_TOOL} can be approved")

    if run["mode"] != "ship":
        raise ApprovalRefused(f"Run mode is {run['mode']}; only ship runs may merge")
    if run["via_fork"]:
        raise ApprovalRefused("Run works through a fork; fork runs never merge")

    args = tool.arguments if isinstance(tool.arguments, dict) else {}
    owner, repo = run["repo"].split("/", 1)
    target = f"{args.get('owner')}/{args.get('repo')}"
    if target.lower() != run["repo"].lower():
        raise ApprovalRefused(f"Merge targets {target}, not the run's repo {run['repo']}")

    number = args.get("pullNumber")
    if not isinstance(number, int) or isinstance(number, bool) or number <= 0:
        raise ApprovalRefused(f"Merge has no valid pullNumber: {number!r}")

    try:
        access = await github.check_access(owner, repo, fresh=True)
        if not access["exists"]:
            raise ApprovalRefused(f"Repository {run['repo']} is no longer visible to the bot")
        pr = await github.get_pr(owner, repo, number, fresh=True)
    except httpx.HTTPStatusError as exc:
        raise ApprovalRefused(f"Could not read PR #{number} from GitHub ({exc.response.status_code})") from exc
    except httpx.HTTPError as exc:
        raise ApprovalRefused(f"Could not reach GitHub to verify PR #{number}") from exc

    if pr["state"] != "open":
        raise ApprovalRefused(f"PR #{number} is {pr['state']}, not open")
    if pr["base_ref"] != access["default_branch"]:
        raise ApprovalRefused(f"PR #{number} targets {pr['base_ref']}, not the default branch {access['default_branch']}")
    if not pr["head_ref"].startswith(HEAD_REF_PREFIX):
        raise ApprovalRefused(f"PR #{number} head branch {pr['head_ref']} does not start with {HEAD_REF_PREFIX}")
    if (pr["head_repo_owner"] or "").lower() != owner.lower():
        raise ApprovalRefused(f"PR #{number} comes from {pr['head_repo_owner']}, not the repo owner {owner}")

    if not access["bot_can_push"]:
        raise ApprovalRefused(f"The bot no longer has push access to {run['repo']}")
