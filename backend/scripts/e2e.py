"""End to end: one real fixer run on greenlight_demo in MODE pr_only, through every helper agent it triggers.

Stages, one summary line each with PASS or FAIL:
  agents        every TrueForge agent is found by name
  start         POST /runs starts the fixer
  pr            the fixer opens a PR (a create_pull_request response with a PR URL on the event stream)
  provers       one prover child per advisory, each finished with a result
  final         the fixer run ends with status done
  receipt       GET /runs/{id}/receipt settles (the backend stores it itself; this only reads it)
  receipt_agent the receipt child ran and finished

Never approves or merges anything: the run is pr_only and this script never calls the approval endpoint.

Usage (from backend/, with TrueForge and `uv run serve` running):
    uv run python scripts/e2e.py [--repo URL] [--backend URL] [--timeout SECONDS]
"""

import argparse
import asyncio
import json
import re
import sys
import time
from typing import Any

import httpx

from app.config import get_settings

DEMO_REPO = "https://github.com/ojasshelke46/greenlight_demo"
PR_URL = re.compile(r"https://github\.com/[\w.-]+/[\w.-]+/pull/\d+")
FINAL = {"done", "error", "cancelled", "awaiting_input", "awaiting_approval", "resume_failed"}
POLL_SECONDS = 5.0


class Stage:
    def __init__(self) -> None:
        self.lines: list[tuple[str, bool, str]] = []

    def report(self, name: str, ok: bool, detail: str) -> bool:
        self.lines.append((name, ok, detail))
        print(f"{name:<14} {'PASS' if ok else 'FAIL'}  {detail}", flush=True)
        return ok


async def watch_events(client: httpx.AsyncClient, run_id: str, seen: dict[str, Any]) -> None:
    """Tail the run's SSE stream; note the PR URL from the create_pull_request response."""
    last_id: str | None = None
    while seen.get("status") not in FINAL:
        headers = {"Last-Event-ID": last_id} if last_id else {}
        try:
            async with client.stream("GET", f"/runs/{run_id}/events", headers=headers, timeout=httpx.Timeout(10.0, read=None)) as response:
                event_id, data = None, []
                async for line in response.aiter_lines():
                    if line.startswith("id:"):
                        event_id = line[3:].strip()
                    elif line.startswith("data:"):
                        data.append(line[5:].strip())
                    elif line == "" and data:
                        last_id = event_id or last_id
                        event = json.loads("\n".join(data))
                        data = []
                        seen["events"] = seen.get("events", 0) + 1
                        if event.get("type") == "tool.response" and "pr_url" not in seen:
                            content = event.get("content")
                            match = PR_URL.search(content if isinstance(content, str) else json.dumps(content))
                            if match:
                                seen["pr_url"] = match.group(0)
        except httpx.HTTPError:
            pass
        await asyncio.sleep(1.0)


async def get_json(client: httpx.AsyncClient, path: str) -> tuple[int, Any]:
    response = await client.get(path)
    try:
        return response.status_code, response.json()
    except ValueError:
        return response.status_code, response.text


async def wait(condition, timeout: float) -> Any:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = await condition()
        if value:
            return value
        await asyncio.sleep(POLL_SECONDS)
    return None


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", default=DEMO_REPO)
    parser.add_argument("--backend", default="http://localhost:8000")
    parser.add_argument("--timeout", type=float, default=2400, help="seconds to wait for each long stage")
    args = parser.parse_args()

    stage = Stage()
    headers = {"Authorization": f"Bearer {get_settings().greenlight_api_key}"}
    async with httpx.AsyncClient(base_url=args.backend, headers=headers, timeout=30.0) as client:
        code, agents = await get_json(client, "/agents")
        missing = [a["name"] for a in agents if not a["found"]] if code == 200 else ["(GET /agents failed)"]
        stage.report("agents", not missing, f"{len(agents) - len(missing)} of {len(agents)} found" if code == 200 else str(agents))

        response = await client.post("/runs", json={"repo": args.repo, "mode": "pr_only"})
        if not stage.report("start", response.status_code == 201, f"HTTP {response.status_code} {response.text[:200]}"):
            return 1
        run_id = response.json()["run_id"]
        print(f"               run {run_id}", flush=True)

        seen: dict[str, Any] = {}
        watcher = asyncio.create_task(watch_events(client, run_id, seen))

        async def status() -> str | None:
            code, run = await get_json(client, f"/runs/{run_id}")
            seen["status"] = run.get("status") if code == 200 else None
            return seen["status"] if seen["status"] in FINAL else None

        async def pr_or_final() -> bool:
            return "pr_url" in seen or bool(await status())

        await wait(pr_or_final, args.timeout)
        stage.report("pr", "pr_url" in seen, seen.get("pr_url") or f"no PR URL seen (status {seen.get('status')})")

        final = await wait(status, args.timeout)

        async def provers_settled() -> list[dict[str, Any]] | None:
            code, children = await get_json(client, f"/runs/{run_id}/children")
            provers = [c for c in children if c["role"] == "prover"] if code == 200 else []
            if provers and all(p["result"] is not None and p["status"] in FINAL for p in provers):
                # The next prover starts only after the previous one ends; give it a moment to appear.
                await asyncio.sleep(POLL_SECONDS)
                code, again = await get_json(client, f"/runs/{run_id}/children")
                if len([c for c in again if c["role"] == "prover"]) == len(provers):
                    return provers
            return None

        provers = await wait(provers_settled, args.timeout) if "pr_url" in seen else None
        _, proof = await get_json(client, f"/features/proof/{run_id}")
        verification = (proof or {}).get("verification", {}) if isinstance(proof, dict) else {}
        detail = ", ".join(f"{p['advisory']}={p['state']}" for p in verification.get("provers", [])) or "no provers"
        stage.report("provers", bool(provers), f"{detail}; verification {verification.get('status')}")

        stage.report("final", final == "done", f"status {final or seen.get('status')}")

        async def receipt() -> dict[str, Any] | None:
            code, body = await get_json(client, f"/runs/{run_id}/receipt")
            return body if code == 200 else None

        body = await wait(receipt, 180) if final == "done" else None
        stage.report(
            "receipt",
            body is not None,
            f"source {body['source']}, {body['model_calls']} model calls, cost_usd {body['cost_usd']}" if body else "no receipt",
        )

        async def receipt_agent() -> dict[str, Any] | None:
            code, children = await get_json(client, f"/runs/{run_id}/children")
            done = [c for c in children if c["role"] == "receipt" and c["result"] is not None] if code == 200 else []
            return done[0] if done else None

        child = await wait(receipt_agent, 900) if body else None
        stage.report("receipt_agent", child is not None, f"run {child['run_id']} status {child['status']}" if child else "no receipt child")

        watcher.cancel()
        await asyncio.gather(watcher, return_exceptions=True)

    failed = [name for name, ok, _ in stage.lines if not ok]
    print(f"\n{'PASS' if not failed else 'FAIL'}: {len(stage.lines) - len(failed)} of {len(stage.lines)} stages passed", flush=True)
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
