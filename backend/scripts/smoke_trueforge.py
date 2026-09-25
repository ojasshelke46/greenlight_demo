"""Run one real Greenlight turn through TrueForgeClient and print every event.

Usage (from backend/):
    uv run python scripts/smoke_trueforge.py [message]

Never approves anything. If the agent pauses on tool.approval_required, the
pending calls are printed and the script exits.
"""

import asyncio
import json
import os
import sys

import httpx

from app.trueforge import PendingAction, TrueForgeClient

DEMO_REPO = "https://github.com/ojasshelke46/greenlight_demo.git"
DEFAULT_MESSAGE = f"MODE: pr_only\nRepository: {DEMO_REPO}"


def summarize(data: dict) -> str:
    kind = data.get("type")
    if kind == "model.message.delta":
        return ""
    if kind == "model.message":
        calls = [c.get("function", {}).get("name") for c in data.get("tool_calls") or []]
        text = (data.get("content") or "").strip().replace("\n", " ")
        return f"tools={calls}" if calls else text[:120]
    if kind == "tool.response":
        return f"tool_call_id={data.get('tool_call_id')} {str(data.get('content'))[:100]!r}"
    if kind in ("turn.created", "turn.update", "turn.done"):
        return f"status={data.get('state', {}).get('status')}"
    if kind in ("tool.approval_required", "tool.response_required"):
        return f"thread={data.get('thread_id')} calls={[c['id'] for c in data.get('tool_calls', [])]}"
    return ""


async def main() -> None:
    message = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_MESSAGE
    base_url = os.environ.get("TRUEFORGE_BASE_URL", "http://localhost:8790")
    agent_name = os.environ.get("TRUEFORGE_AGENT_NAME", "greenlight")

    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=None)) as http:
        client = TrueForgeClient(http, base_url, agent_name)

        agent_id = await client.get_agent_id(agent_name)
        session_id = await client.create_session(agent_id)
        turn = await client.start_turn(session_id, message)
        print(f"agent={agent_id} session={session_id} turn={turn.turn_id} status={turn.status}")
        print(f"message={message!r}\n")

        pending: PendingAction | None = None
        counts: dict[str, int] = {}
        async for event in client.stream_turn(session_id, turn.turn_id):
            counts[event.type] = counts.get(event.type, 0) + 1
            print(f"seq={event.sequence} {event.type} {summarize(event.data)}", flush=True)
            if event.type == "tool.approval_required":
                pending = PendingAction.from_event(turn.turn_id, event.data)
            if event.type == "turn.done":
                state = event.data["state"]
                if state["status"] == "error":
                    print(f"\nturn error: {state.get('message')}")
                elif state.get("output"):
                    print(f"\nfinal output:\n{state['output'].get('content')}")
                if state.get("required_actions"):
                    print(f"\nrequired_actions: {json.dumps(state['required_actions'])[:1000]}")

        print(f"\nevent counts: {counts}")
        if pending:
            print(f"paused for approval, NOT approving: {pending}")


if __name__ == "__main__":
    asyncio.run(main())
