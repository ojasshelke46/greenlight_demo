"""Campaigns: one vulnerable package, one repo, or one policy at a time.

A repo campaign starts a TASK: scan run. Its id is that scan run's id, and everything about it is a chained note on
the scan run: "campaign" {repo, mode, auto}, "campaign_queue" {items} once the scan reports, "campaign_item"
{package, action, run_id} for each fix or skip, and "campaign_auto" / "campaign_stopped" for control. Each fix is a
TASK: fix <package> run of its own (own session, own sandbox) whose run_meta links back to the campaign. An item's
status is derived from its fix run, that run's "pr" note and its approvals, never stored twice.

Fleet (scout) and policy campaigns span many repos, so they are kept in their own small table, like run_facts.
"""

import asyncio
import json
import logging
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import FastAPI

from app import facts
from app.config import get_settings
from app.github import GITHUB_API_URL

logger = logging.getLogger(__name__)

NOTE_CAMPAIGN = "campaign"
NOTE_QUEUE = "campaign_queue"
NOTE_ITEM = "campaign_item"
NOTE_AUTO = "campaign_auto"
NOTE_STOPPED = "campaign_stopped"

SEVERITY_RANK = {"critical": 0, "high": 1, "moderate": 2, "medium": 2, "low": 3}
ACTIVE_ITEM = {"fixing", "awaiting_approval"}
DONE_ITEM = {"pr_opened", "merged"}
RUN_ACTIVE = {"running", "awaiting_input"}
# In auto mode the next item starts after this delay, so "Stop here" has a moment to land.
AUTO_ADVANCE_SECONDS = 5.0
BRANCH_ATTEMPTS = 20

_SCHEMA = """
CREATE TABLE IF NOT EXISTS campaigns (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    state_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


class CampaignError(Exception):
    def __init__(self, message: str, status_code: int = 409) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def severity_rank(value: Any) -> int:
    return SEVERITY_RANK.get(str(value or "").lower(), 4)


def build_queue(vulnerabilities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One item per package, most severe first, then the package with the most advisories.

    Accepts the scan fact ({package, from, to, advisories, severity}) and the older per advisory fact
    ({package, advisory, current, fixed, severity}).
    """
    items: dict[str, dict[str, Any]] = {}
    for fact in vulnerabilities:
        package = fact.get("package")
        if not isinstance(package, str) or not package.strip():
            continue
        package = package.strip()
        item = items.setdefault(package, {"package": package, "from": None, "to": None, "advisories": [], "severity": None})
        advisories = fact.get("advisories") if isinstance(fact.get("advisories"), list) else [fact.get("advisory")]
        for advisory in advisories:
            if isinstance(advisory, str) and advisory.strip() and advisory.strip() not in item["advisories"]:
                item["advisories"].append(advisory.strip())
        item["from"] = item["from"] or fact.get("from") or fact.get("current")
        item["to"] = item["to"] or fact.get("to") or fact.get("fixed")
        severity = fact.get("severity")
        if isinstance(severity, str) and (item["severity"] is None or severity_rank(severity) < severity_rank(item["severity"])):
            item["severity"] = severity.lower()
    return sorted(items.values(), key=lambda i: (severity_rank(i["severity"]), -len(i["advisories"]), i["package"].lower()))


def short_advisory(advisories: list[str]) -> str:
    """GHSA-r683-j2x4-v87g -> r683; CVE-2024-1234 -> 2024-1234; nothing -> fix."""
    if not advisories:
        return "fix"
    first = advisories[0]
    ghsa = re.match(r"GHSA-([a-z0-9]{4})", first, re.IGNORECASE)
    if ghsa:
        return ghsa.group(1).lower()
    return re.sub(r"[^a-z0-9-]+", "-", first.lower()).strip("-")[-12:] or "fix"


def branch_base(package: str, advisories: list[str]) -> str:
    name = re.sub(r"[^a-z0-9._-]+", "-", package.lower().lstrip("@")).strip("-")
    return f"greenlight/{name}-{short_advisory(advisories)}"


def scan_message(repo: str, mode: str, sandbox_note: str) -> str:
    return (
        f"TASK: scan\nRepository: https://github.com/{repo}\nMODE: {mode}\n\n"
        "Scan only. Change nothing, and never branch, fork or open a PR.\n\n"
        f"{sandbox_note}\n\n"
        "Work through every step in this one turn and end with the scan_done fact."
    )


def fix_message(repo: str, mode: str, item: dict[str, Any], branch: str, sandbox_note: str, keep_going: str) -> str:
    advisories = ", ".join(item["advisories"]) or "not reported"
    versions = f"from {item['from'] or 'the current version'} to {item['to'] or 'the first fixed version or later'}"
    return (
        f"TASK: fix {item['package']}\nRepository: https://github.com/{repo}\nMODE: {mode}\nBRANCH: {branch}\n"
        f"Advisories: {advisories}\nUpgrade {versions}.\n\n"
        f"Fix only {item['package']}. Leave every other package exactly as it is.\n"
        "The advisories above come from this campaign's scan: if OSV is unreachable from the sandbox, use them. "
        "If the repo has no test script, check the change with the repo's lint and type check (for example "
        "npm run lint and npx tsc --noEmit) instead: never run a production build, it does not finish in the sandbox.\n\n"
        f"{sandbox_note}\n\n{keep_going}"
    )


def policy_message(repo: str, sandbox_note: str) -> str:
    return (
        f"TASK: policy\nRepository: https://github.com/{repo}\n\n"
        "Draft a .greenlight.yml for this repo, then publish it as exactly one PR that adds only that file, "
        "following PUBLISHING, and print the pr fact.\n\n"
        f"{sandbox_note}"
    )


class Campaigns:
    def __init__(self, app: FastAPI) -> None:
        self._app = app
        self._locks: dict[str, asyncio.Lock] = {}
        # campaign id -> (monotonic time the next item starts, the task that starts it)
        self._pending: dict[str, tuple[float, asyncio.Task[Any]]] = {}
        self._fleet_tasks: dict[str, asyncio.Task[Any]] = {}
        self._fleet_updates: dict[str, asyncio.Event] = {}

    # Plumbing

    @property
    def _ledger(self) -> Any:
        return self._app.state.ledger

    @property
    def _orchestrator(self) -> Any:
        return self._app.state.orchestrator

    def _lock(self, key: str) -> asyncio.Lock:
        return self._locks.setdefault(key, asyncio.Lock())

    async def init(self) -> None:
        async with self._ledger.write_lock:
            await self._ledger.db.executescript(_SCHEMA)
            await self._ledger.db.commit()

    async def close(self) -> None:
        tasks = [task for _, task in self._pending.values()] + list(self._fleet_tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _notes(self, run_id: str, kind: str) -> list[dict[str, Any]]:
        return [n["payload"] for n in await self._ledger.get_notes(run_id, kind=kind)]

    # GitHub, as the bot

    async def _github(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        token = get_settings().github_bot_token
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        return await self._app.state.http_client.request(method, f"{GITHUB_API_URL}{path}", headers=headers, timeout=15.0, **kwargs)

    async def _branch_exists(self, owner: str, repo: str, branch: str) -> bool:
        try:
            response = await self._github("GET", f"/repos/{owner}/{repo}/branches/{branch}")
        except httpx.HTTPError:
            return False
        return response.status_code == 200

    async def unique_branch(self, repo: str, item: dict[str, Any]) -> str:
        """greenlight/<package>-<short advisory>, with -2, -3 when that name is taken on the repo or the bot's fork."""
        owner, name = repo.split("/", 1)
        bot = get_settings().github_bot_login
        base = branch_base(item["package"], item["advisories"])
        for attempt in range(1, BRANCH_ATTEMPTS + 1):
            candidate = base if attempt == 1 else f"{base}-{attempt}"
            if not await self._branch_exists(owner, name, candidate) and not await self._branch_exists(bot, name, candidate):
                return candidate
        return f"{base}-{uuid.uuid4().hex[:6]}"

    async def sync_fork(self, repo: str, default_branch: str | None) -> None:
        """Bring the bot's fork up to date with the repo first, so a fix branch never reverts upstream work."""
        bot = get_settings().github_bot_login
        name = repo.split("/", 1)[1]
        try:
            response = await self._github("GET", f"/repos/{bot}/{name}")
            if response.status_code != 200 or not response.json().get("fork"):
                return
            branch = default_branch or response.json().get("default_branch") or "main"
            synced = await self._github("POST", f"/repos/{bot}/{name}/merge-upstream", json={"branch": branch})
            if synced.status_code >= 400:
                logger.warning("Could not sync fork %s/%s (%s)", bot, name, synced.status_code)
        except httpx.HTTPError:
            logger.warning("Could not reach GitHub to sync fork %s/%s", bot, name)

    # Repo campaigns

    async def create(self, repo: str, mode: str, auto: bool, via_fork: bool, extras: str = "") -> str:
        from app.orchestrator import SANDBOX_NOTE

        message = scan_message(repo, mode, SANDBOX_NOTE)
        if extras:
            message = f"{message}\n\n{extras}"
        campaign_id = await self._orchestrator.start_run(
            "fixer", repo, mode, message, via_fork=via_fork, extra_meta={"task": "scan", "campaign": True}
        )
        await self._ledger.append_note(campaign_id, NOTE_CAMPAIGN, {"repo": repo, "mode": mode, "auto": auto})
        return campaign_id

    async def _campaign_note(self, campaign_id: str) -> dict[str, Any]:
        notes = await self._notes(campaign_id, NOTE_CAMPAIGN)
        if not notes:
            raise CampaignError(f"Campaign {campaign_id} not found", 404)
        return notes[0]

    async def auto_on(self, campaign_id: str) -> bool:
        toggles = await self._notes(campaign_id, NOTE_AUTO)
        return bool(toggles[-1]["auto"]) if toggles else bool((await self._campaign_note(campaign_id)).get("auto"))

    async def _item_status(self, action: dict[str, Any] | None, mode: str) -> dict[str, Any]:
        if action is None:
            return {"status": "queued", "run_id": None, "pr_url": None, "pr_number": None, "reason": None}
        if action["action"] == "skip":
            return {"status": "skipped", "run_id": None, "pr_url": None, "pr_number": None, "reason": action.get("reason")}
        run_id = action["run_id"]
        run = await self._ledger.get_run(run_id)
        pr = await self._orchestrator.pr(run_id)
        approval = await self._ledger.accepted_approval(run_id)
        status = run["status"] if run else "missing"
        out = {"run_id": run_id, "pr_url": pr["url"] if pr else None, "pr_number": pr.get("number") if pr else None, "reason": None}
        if status == "awaiting_approval":
            return {**out, "status": "awaiting_approval"}
        if approval and approval["decision"] == "approve" and approval["tool_name"] == "merge_pull_request" and status == "done":
            return {**out, "status": "merged"}
        if pr:
            return {**out, "status": "pr_opened"}
        if status in RUN_ACTIVE:
            return {**out, "status": "fixing"}
        reason = "The fix run ended without opening a PR" if status == "done" else f"The fix run stopped ({status})"
        return {**out, "status": "failed", "reason": reason}

    async def get(self, campaign_id: str) -> dict[str, Any]:
        await self._ledger.flush()
        campaign = await self._campaign_note(campaign_id)
        scan = await self._ledger.get_run(campaign_id)
        queue_notes = await self._notes(campaign_id, NOTE_QUEUE)
        actions: dict[str, dict[str, Any]] = {}
        for action in await self._notes(campaign_id, NOTE_ITEM):
            actions[action["package"]] = action
        items = []
        for item in queue_notes[-1]["items"] if queue_notes else []:
            items.append({**item, **await self._item_status(actions.get(item["package"]), campaign["mode"])})
        stopped = await self._notes(campaign_id, NOTE_STOPPED)
        pending = self._pending.get(campaign_id)
        scan_status = "done" if queue_notes else ("running" if scan and scan["status"] in RUN_ACTIVE else "failed")
        next_item = next((i for i in items if i["status"] == "queued"), None)
        return {
            "id": campaign_id,
            "repo": campaign["repo"],
            "mode": campaign["mode"],
            "auto": await self.auto_on(campaign_id),
            "scan_run_id": campaign_id,
            "scan_status": scan_status,
            "items": items,
            "next": next_item,
            "active": next((i for i in items if i["status"] in ACTIVE_ITEM), None),
            "done": sum(i["status"] in DONE_ITEM for i in items),
            "total": len(items),
            "stopped": stopped[-1] if stopped else None,
            "next_start_in": max(0.0, round(pending[0] - time.monotonic(), 1)) if pending else None,
        }

    async def start_fix(self, campaign_id: str, package: str | None) -> str:
        """Start the fix run for a package, or for the next queued item. One active fix per campaign."""
        from app.hooks import build_run_message_extras
        from app.orchestrator import KEEP_GOING, SANDBOX_NOTE

        async with self._lock(campaign_id):
            state = await self.get(campaign_id)
            if state["active"] is not None:
                raise CampaignError(f"{state['active']['package']} is still being fixed. One fix at a time")
            if state["scan_status"] != "done":
                raise CampaignError("The scan has not finished yet")
            if package is None:
                if state["next"] is None:
                    raise CampaignError("Nothing left in the queue")
                package = state["next"]["package"]
            item = next((i for i in state["items"] if i["package"] == package), None)
            if item is None:
                raise CampaignError(f"{package} is not in this campaign's queue", 404)
            if item["status"] in DONE_ITEM:
                raise CampaignError(f"{package} already has a PR: {item['pr_url']}")
            self._cancel_pending(campaign_id)

            scan = await self._ledger.get_run(campaign_id)
            repo, mode = state["repo"], state["mode"]
            if scan and scan["via_fork"]:
                await self.sync_fork(repo, None)
            branch = await self.unique_branch(repo, item)
            owner, name = repo.split("/", 1)
            message = fix_message(repo, mode, item, branch, SANDBOX_NOTE, KEEP_GOING)
            extras = await build_run_message_extras(owner, name)
            if extras:
                message = f"{message}\n\n{extras}"
            run_id = await self._orchestrator.start_run(
                "fixer",
                repo,
                mode,
                message,
                via_fork=bool(scan and scan["via_fork"]),
                extra_meta={"task": "fix", "campaign_id": campaign_id, "package": package, "branch": branch},
            )
            # The fix run's advisories come from the scan's facts, so its provers know what to verify.
            for advisory in item["advisories"]:
                await facts.add_fact(
                    run_id,
                    {"kind": "vulnerability", "advisory": advisory, "package": package, "current": item["from"],
                     "fixed": item["to"], "severity": item["severity"], "source": f"campaign scan {campaign_id}"},
                )
            await self._ledger.append_note(campaign_id, NOTE_ITEM, {"package": package, "action": "fix", "run_id": run_id, "branch": branch})
            return run_id

    async def skip(self, campaign_id: str, package: str) -> None:
        async with self._lock(campaign_id):
            state = await self.get(campaign_id)
            item = next((i for i in state["items"] if i["package"] == package), None)
            if item is None:
                raise CampaignError(f"{package} is not in this campaign's queue", 404)
            if item["status"] in ACTIVE_ITEM | DONE_ITEM:
                raise CampaignError(f"{package} is {item['status'].replace('_', ' ')} and cannot be skipped")
            await self._ledger.append_note(campaign_id, NOTE_ITEM, {"package": package, "action": "skip", "reason": "Skipped by you"})

    async def set_auto(self, campaign_id: str, auto: bool) -> None:
        await self._campaign_note(campaign_id)
        if not auto:
            self._cancel_pending(campaign_id)
        await self._ledger.append_note(campaign_id, NOTE_AUTO, {"auto": auto})

    async def stop(self, campaign_id: str) -> None:
        """Stop here: no automatic next item. A fix already running carries on."""
        await self.set_auto(campaign_id, False)

    def _cancel_pending(self, campaign_id: str) -> None:
        pending = self._pending.pop(campaign_id, None)
        if pending and not pending[1].done() and pending[1] is not asyncio.current_task():
            pending[1].cancel()

    def schedule_next(self, campaign_id: str) -> None:
        if campaign_id in self._pending:
            return

        async def advance() -> None:
            await asyncio.sleep(AUTO_ADVANCE_SECONDS)
            try:
                if not await self.auto_on(campaign_id):
                    return
                state = await self.get(campaign_id)
                if state["next"] is None or state["active"] is not None or state["stopped"]:
                    return
                await self.start_fix(campaign_id, None)
            except CampaignError as exc:
                logger.info("Campaign %s did not advance: %s", campaign_id, exc.message)
            finally:
                if self._pending.get(campaign_id, (0, None))[1] is asyncio.current_task():
                    self._pending.pop(campaign_id, None)

        task = asyncio.create_task(advance())
        self._pending[campaign_id] = (time.monotonic() + AUTO_ADVANCE_SECONDS, task)

    # Hooks, called by the orchestrator (always from spawned tasks)

    async def on_facts(self, run_id: str, found: list[dict[str, Any]]) -> None:
        if any(fact.get("kind") == "scan_done" for fact in found):
            await self._build_queue(run_id)

    async def _build_queue(self, campaign_id: str) -> bool:
        async with self._lock(f"queue:{campaign_id}"):
            if not await self._notes(campaign_id, NOTE_CAMPAIGN) or await self._notes(campaign_id, NOTE_QUEUE):
                return False
            await facts.flush()
            items = build_queue(await facts.get_facts(campaign_id, kind="vulnerability"))
            await self._ledger.append_note(campaign_id, NOTE_QUEUE, {"items": items})
            logger.info("Campaign %s queued %d packages", campaign_id, len(items))
            return True

    async def on_pr(self, run_id: str) -> None:
        """A fix run opened its PR. In pr_only mode or through a fork that is the end of the item."""
        info = await self._orchestrator.run_info(run_id)
        campaign_id = info.get("campaign_id")
        if campaign_id and info.get("task") == "fix":
            pr = await self._orchestrator.pr(run_id)
            campaign = await self._campaign_note(campaign_id)
            if (campaign["mode"] != "ship" or (pr and pr.get("via_fork"))) and await self.auto_on(campaign_id):
                self.schedule_next(campaign_id)
        await self._policy_on_pr(run_id)

    async def on_turn_done(self, run_id: str, status: str) -> None:
        info = await self._orchestrator.run_info(run_id)
        if info.get("task") == "scan" and info.get("campaign"):
            await self._ledger.flush()
            built = await self._build_queue(run_id)
            if built:
                logger.info("Campaign %s scan ended without scan_done; queued from its vulnerability facts", run_id)
            return
        campaign_id = info.get("campaign_id")
        if campaign_id and info.get("task") == "fix":
            await self._ledger.flush()
            state = await self.get(campaign_id)
            item = next((i for i in state["items"] if i["run_id"] == run_id), None)
            if item is None:
                return
            if item["status"] == "failed":
                if state["auto"]:
                    await self.set_auto(campaign_id, False)
                    await self._ledger.append_note(
                        campaign_id, NOTE_STOPPED, {"package": item["package"], "run_id": run_id, "reason": item["reason"]}
                    )
            elif item["status"] in DONE_ITEM and state["auto"]:
                # Ship mode resolves at the end of the turn that ran the approved (or rejected) merge.
                self.schedule_next(campaign_id)
        await self._policy_on_turn_done(run_id, status)

    # Fleet and policy campaigns (their own table)

    async def _save(self, campaign_id: str, kind: str, state: dict[str, Any]) -> None:
        now = utc_now()
        async with self._ledger.write_lock:
            await self._ledger.db.execute(
                "INSERT INTO campaigns (id, kind, state_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT(id) DO UPDATE SET state_json = excluded.state_json, updated_at = excluded.updated_at",
                (campaign_id, kind, json.dumps(state), now, now),
            )
            await self._ledger.db.commit()
        event = self._fleet_updates.get(campaign_id)
        if event:
            event.set()

    async def _load(self, campaign_id: str, kind: str) -> dict[str, Any]:
        cursor = await self._ledger.db.execute("SELECT state_json FROM campaigns WHERE id = ? AND kind = ?", (campaign_id, kind))
        row = await cursor.fetchone()
        if row is None:
            raise CampaignError(f"Campaign {campaign_id} not found", 404)
        return json.loads(row["state_json"])

    async def list_all(self) -> list[dict[str, Any]]:
        cursor = await self._ledger.db.execute("SELECT id, kind, state_json, created_at FROM campaigns ORDER BY created_at DESC LIMIT 100")
        rows = []
        for row in await cursor.fetchall():
            state = json.loads(row["state_json"])
            rows.append({"id": row["id"], "kind": row["kind"], "label": state.get("label"), "status": state.get("status"), "created_at": row["created_at"]})
        return rows

    # Fleet: scan repos one at a time, then fix them one repo at a time.

    async def create_fleet(self, target: str, mode: str) -> str:
        from app.features.fleet.scan import DEFAULT_MAX_REPOS, parse_target

        parsed = parse_target(target)
        campaign_id = str(uuid.uuid4())
        state = {"target": parsed.key, "label": parsed.key, "mode": mode, "status": "scanning", "repos": [], "queue": [], "error": None}
        await self._save(campaign_id, "fleet", state)
        self._fleet_updates[campaign_id] = asyncio.Event()
        settings = get_settings()
        self._fleet_tasks[campaign_id] = asyncio.create_task(
            self._scan_fleet(campaign_id, parsed, settings.fleet_max_repos or DEFAULT_MAX_REPOS)
        )
        return campaign_id

    async def _scan_fleet(self, campaign_id: str, parsed: Any, cap: int) -> None:
        from app.features.fleet.scan import Scan, TargetUnavailable, list_repos

        state = await self._load(campaign_id, "fleet")
        http = self._app.state.http_client
        token = get_settings().github_bot_token
        try:
            refs = await list_repos(http, token, parsed, cap)
            scan = Scan(http, token)
            try:
                # One repo at a time: each result is saved and streamed before the next repo starts.
                for ref in refs:
                    result = await scan.scan_repo(ref)
                    state["repos"].append(result)
                    await self._save(campaign_id, "fleet", state)
            finally:
                scan.close()
            exposed = [r for r in state["repos"] if r["status"] == "ok" and r["risk"] > 0]
            state["queue"] = [
                {"repo": r["repo"], "risk": r["risk"], "status": "queued", "campaign_id": None}
                for r in sorted(exposed, key=lambda r: (-r["risk"], r["repo"].lower()))
            ]
            state["status"] = "ready"
        except TargetUnavailable as exc:
            state["status"], state["error"] = "failed", str(exc)
        except Exception:
            logger.exception("Fleet campaign %s failed", campaign_id)
            state["status"], state["error"] = "failed", "The fleet scan stopped unexpectedly"
        await self._save(campaign_id, "fleet", state)

    async def get_fleet(self, campaign_id: str) -> dict[str, Any]:
        state = await self._load(campaign_id, "fleet")
        for item in state["queue"]:
            if item["campaign_id"]:
                repo_campaign = await self.get(item["campaign_id"])
                # A repo is finished when nothing in it is queued or being fixed, or its campaign was stopped.
                unfinished = (
                    repo_campaign["scan_status"] == "running"
                    or repo_campaign["active"] is not None
                    or repo_campaign["next_start_in"] is not None
                    or (repo_campaign["next"] is not None and repo_campaign["stopped"] is None)
                )
                item["status"] = "running" if unfinished else "done"
                item["done"], item["total"] = repo_campaign["done"], repo_campaign["total"]
        state["next"] = next((i for i in state["queue"] if i["status"] == "queued"), None)
        state["active"] = next((i for i in state["queue"] if i["status"] == "running"), None)
        return {"id": campaign_id, **state}

    async def fleet_updates(self, campaign_id: str) -> asyncio.Event:
        return self._fleet_updates.setdefault(campaign_id, asyncio.Event())

    async def fleet_next(self, campaign_id: str, start_repo_campaign: Any) -> str:
        async with self._lock(f"fleet:{campaign_id}"):
            state = await self.get_fleet(campaign_id)
            if state["status"] != "ready":
                raise CampaignError("The fleet scan has not finished yet")
            if state["active"] is not None:
                raise CampaignError(f"{state['active']['repo']} is still in progress. One repo at a time")
            if state["next"] is None:
                raise CampaignError("No exposed repos left in the queue")
            repo = state["next"]["repo"]
            repo_campaign_id = await start_repo_campaign(repo, state["mode"])
            stored = await self._load(campaign_id, "fleet")
            for item in stored["queue"]:
                if item["repo"] == repo:
                    item["campaign_id"], item["status"] = repo_campaign_id, "running"
            await self._save(campaign_id, "fleet", stored)
            return repo_campaign_id

    # Policy: draft .greenlight.yml one repo at a time, one PR each.

    async def create_policy(self, repos: list[str], auto: bool) -> str:
        campaign_id = str(uuid.uuid4())
        label = repos[0] if len(repos) == 1 else f"{repos[0]} and {len(repos) - 1} more"
        state = {"label": label, "auto": auto, "status": "running", "stopped": None,
                 "items": [{"repo": r, "status": "queued", "run_id": None, "pr_url": None, "reason": None} for r in repos]}
        await self._save(campaign_id, "policy", state)
        await self.policy_next(campaign_id)
        return campaign_id

    async def get_policy(self, campaign_id: str) -> dict[str, Any]:
        state = await self._load(campaign_id, "policy")
        for item in state["items"]:
            if item["run_id"] and item["status"] in ("drafting", "queued"):
                run = await self._ledger.get_run(item["run_id"])
                pr = await self._orchestrator.pr(item["run_id"])
                if pr:
                    item["status"], item["pr_url"] = "pr_opened", pr["url"]
                elif run and run["status"] not in RUN_ACTIVE:
                    item["status"], item["reason"] = "failed", "The policy run ended without opening a PR"
        state["next"] = next((i for i in state["items"] if i["status"] == "queued"), None)
        state["active"] = next((i for i in state["items"] if i["status"] == "drafting"), None)
        return {"id": campaign_id, **state}

    async def policy_next(self, campaign_id: str) -> str | None:
        from app.features.policy.loader import load_policy
        from app.orchestrator import SANDBOX_NOTE

        async with self._lock(f"policy:{campaign_id}"):
            state = await self.get_policy(campaign_id)
            if state["active"] is not None:
                raise CampaignError(f"{state['active']['repo']} is still being drafted. One repo at a time")
            stored = await self._load(campaign_id, "policy")
            for item in stored["items"]:
                if item["status"] != "queued":
                    continue
                owner, name = item["repo"].split("/", 1)
                loaded = await load_policy(owner, name)
                if loaded.exists:
                    item["status"], item["reason"] = "skipped", f"Already has {loaded.path}"
                    continue
                run_id = await self._orchestrator.start_run(
                    "policy", item["repo"], "pr_only", policy_message(item["repo"], SANDBOX_NOTE),
                    extra_meta={"task": "policy", "policy_campaign_id": campaign_id},
                )
                item["status"], item["run_id"] = "drafting", run_id
                await self._save(campaign_id, "policy", stored)
                return run_id
            stored["status"] = "done"
            await self._save(campaign_id, "policy", stored)
            return None

    async def policy_stop(self, campaign_id: str) -> None:
        stored = await self._load(campaign_id, "policy")
        stored["auto"] = False
        await self._save(campaign_id, "policy", stored)

    async def _policy_campaign_of(self, run_id: str) -> str | None:
        return (await self._orchestrator.run_info(run_id)).get("policy_campaign_id")

    async def _policy_on_pr(self, run_id: str) -> None:
        campaign_id = await self._policy_campaign_of(run_id)
        if not campaign_id:
            return
        stored = await self._load(campaign_id, "policy")
        pr = await self._orchestrator.pr(run_id)
        for item in stored["items"]:
            if item["run_id"] == run_id and pr:
                item["status"], item["pr_url"] = "pr_opened", pr["url"]
        await self._save(campaign_id, "policy", stored)
        if stored["auto"]:
            try:
                await self.policy_next(campaign_id)
            except CampaignError as exc:
                logger.info("Policy campaign %s did not advance: %s", campaign_id, exc.message)

    async def _policy_on_turn_done(self, run_id: str, status: str) -> None:
        campaign_id = await self._policy_campaign_of(run_id)
        if not campaign_id:
            return
        state = await self.get_policy(campaign_id)
        item = next((i for i in state["items"] if i["run_id"] == run_id), None)
        if item and item["status"] == "failed":
            stored = await self._load(campaign_id, "policy")
            for stored_item in stored["items"]:
                if stored_item["run_id"] == run_id:
                    stored_item["status"], stored_item["reason"] = "failed", item["reason"]
            stored["auto"], stored["stopped"] = False, {"repo": item["repo"], "reason": item["reason"]}
            await self._save(campaign_id, "policy", stored)
