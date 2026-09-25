"""Measure the latency Greenlight adds between TrueForge and an SSE client during a real run.

The backend stamps every event with received_at the moment it reads it off the TrueForge
stream (stored in the ledger). This script stamps each event again when it arrives over
GET /runs/{id}/events and reports client arrival minus received_at. Both stamps come from
this machine's clock, so run it on the same host as the backend.

Only events the backend received after this client connected are counted; anything earlier
is a ledger replay, not live fan out.

Usage (from backend/, with TrueForge and `uv run serve` running):
    uv run python scripts/latency_check.py --repo https://github.com/owner/repo [--mode pr_only]
    uv run python scripts/latency_check.py --run-id <id>   # attach to a run that is streaming now
"""

import argparse
import asyncio
import statistics
import sys
import time
from datetime import datetime

import httpx

from app.config import get_settings

TARGET_MS = 50.0


async def create_run(client: httpx.AsyncClient, repo: str, mode: str) -> str:
    response = await client.post("/runs", json={"repo": repo, "mode": mode})
    if response.status_code != 201:
        sys.exit(f"POST /runs failed ({response.status_code}): {response.text}")
    return response.json()["run_id"]


class Stream:
    def __init__(self) -> None:
        self.connected_at: float | None = None
        self.arrivals: dict[int, float] = {}


async def collect(client: httpx.AsyncClient, run_id: str, stream: Stream) -> None:
    """Reads the SSE stream until the backend closes it, stamping each event on arrival."""
    arrivals = stream.arrivals
    async with client.stream("GET", f"/runs/{run_id}/events", timeout=httpx.Timeout(10.0, read=None)) as response:
        if response.status_code != 200:
            await response.aread()
            sys.exit(f"GET /runs/{run_id}/events failed ({response.status_code}): {response.text}")
        stream.connected_at = time.time()
        print(f"connected to run {run_id}, streaming...", flush=True)

        first_line_at: float | None = None
        event_id: int | None = None
        async for line in response.aiter_lines():
            now = time.time()
            if line == "":
                if event_id is not None and first_line_at is not None:
                    arrivals[event_id] = first_line_at
                    if len(arrivals) % 100 == 0:
                        print(f"  {len(arrivals)} events", flush=True)
                first_line_at, event_id = None, None
            elif not line.startswith(":"):
                first_line_at = first_line_at or now
                if line.startswith("id:"):
                    event_id = int(line[3:].strip())


async def ledger_stamps(client: httpx.AsyncClient, run_id: str, wanted: set[int]) -> dict[int, tuple[str, float]]:
    """received_at per sequence. Ledger writes are queued, so poll briefly for the tail to land."""
    stamps: dict[int, tuple[str, float]] = {}
    for _ in range(20):
        response = await client.get(f"/runs/{run_id}/ledger")
        response.raise_for_status()
        stamps = {
            event["sequence"]: (event["type"], datetime.fromisoformat(event["received_at"]).timestamp())
            for event in response.json()["events"]
        }
        if wanted <= stamps.keys():
            break
        await asyncio.sleep(0.1)
    return stamps


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(fraction * (len(ordered) - 1)))]


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--repo", help="GitHub repo URL to start a new run on")
    target.add_argument("--run-id", help="existing run to attach to")
    parser.add_argument("--mode", default="pr_only", choices=["pr_only", "ship"])
    parser.add_argument("--backend", default="http://localhost:8000")
    parser.add_argument("--timeout", type=float, default=1800, help="seconds to wait for the run to finish")
    args = parser.parse_args()

    headers = {"Authorization": f"Bearer {get_settings().greenlight_api_key}"}
    async with httpx.AsyncClient(base_url=args.backend, headers=headers, timeout=30.0) as client:
        run_id = args.run_id or await create_run(client, args.repo, args.mode)
        # Subscribe as early as possible: events the backend gets before this are replays.
        stream = Stream()
        try:
            await asyncio.wait_for(collect(client, run_id, stream), timeout=args.timeout)
        except TimeoutError:
            print(f"timed out after {args.timeout:.0f}s; reporting what arrived", flush=True)

        stamps = await ledger_stamps(client, run_id, set(stream.arrivals))

    live = []
    for sequence, arrived in stream.arrivals.items():
        if sequence not in stamps:
            continue
        event_type, received = stamps[sequence]
        if stream.connected_at is None or received < stream.connected_at:
            continue
        live.append((max(0.0, (arrived - received) * 1000), sequence, event_type))

    replayed = len(stream.arrivals) - len(live)
    if not live:
        sys.exit(f"No live events measured ({len(stream.arrivals)} arrived, {replayed} were replays).")

    added = [ms for ms, _, _ in live]
    median, p95, worst = statistics.median(added), percentile(added, 0.95), max(added)
    print(f"\nrun {run_id}: {len(live)} live events measured ({replayed} replayed events excluded)")
    print(f"added latency  median {median:.2f} ms   p95 {p95:.2f} ms   max {worst:.2f} ms")
    print("slowest:", ", ".join(f"#{seq} {kind} {ms:.1f} ms" for ms, seq, kind in sorted(live, reverse=True)[:5]))
    over = sum(ms >= TARGET_MS for ms in added)
    verdict = "PASS" if worst < TARGET_MS else "FAIL"
    print(f"{verdict}: target is under {TARGET_MS:.0f} ms added; {over} of {len(added)} events at or over it")
    sys.exit(0 if verdict == "PASS" else 1)


if __name__ == "__main__":
    asyncio.run(main())
