import asyncio
import json

from tests.test_approval import AUTH, api, github_state  # noqa: F401
from app.routes.runs import RESUME_PROMPT
from app.trueforge import TurnEvent, TurnHandle


def turn(turn_id: str, status: str, reason: str | None = None) -> list[dict]:
    state = {"status": status, **({"reason": reason} if reason else {})}
    return [
        {"type": "turn.created", "id": f"{turn_id}:c", "turn_id": turn_id, "created_at": "t"},
        {"type": "model.message", "id": f"{turn_id}:m", "thread_id": "th", "created_at": "t", "content": f"working in {turn_id}"},
        {"type": "turn.done", "id": f"{turn_id}:d", "thread_id": None, "created_at": "t", "state": state},
    ]


class PausableTrueForge:
    """turn_1 runs until cancelled; resumed turns finish normally."""

    def __init__(self) -> None:
        self.cancelled = asyncio.Event()
        self.cancel_calls: list[str] = []
        self.turns: list[tuple[str, str]] = []

    async def get_agent_id(self, name=None):
        return "agent_1"

    async def create_session(self, agent_id):
        return "sess_1"

    async def start_turn(self, session_id, message):
        self.turns.append((session_id, message))
        return TurnHandle(session_id=session_id, turn_id=f"turn_{len(self.turns)}", status="running")

    async def cancel(self, session_id):
        self.cancel_calls.append(session_id)
        self.cancelled.set()

    async def stream_turn(self, session_id, turn_id, after_sequence=None):
        events = turn(turn_id, "cancelled", "client-cancelled") if turn_id == "turn_1" else turn(turn_id, "done")
        for sequence, event in enumerate(events, start=1):
            if sequence <= (after_sequence or 0):
                continue
            if turn_id == "turn_1" and event["type"] == "turn.done":
                await self.cancelled.wait()
            yield TurnEvent(sequence=sequence, type=event["type"], data=event)


async def wait_status(app, run_id, status):
    async def poll():
        while (await app.state.ledger.get_run(run_id))["status"] != status:
            await app.state.ledger.flush()
            await asyncio.sleep(0.01)

    await asyncio.wait_for(poll(), timeout=5)


async def start(client):
    response = await client.post("/runs", json={"repo": "https://github.com/acme/widgets", "mode": "ship"})
    assert response.status_code == 201, response.text
    return response.json()["run_id"]


async def test_pause_cancels_the_running_turn():
    fake = PausableTrueForge()
    async with api(fake, github_state()) as (client, app):
        run_id = await start(client)
        response = await client.post(f"/runs/{run_id}/pause")
        await wait_status(app, run_id, "cancelled")

    assert response.status_code == 200 and response.json()["status"] == "pausing"
    assert fake.cancel_calls == ["sess_1"]


async def test_pause_refuses_a_run_that_is_not_running():
    fake = PausableTrueForge()
    async with api(fake, github_state()) as (client, app):
        run_id = await start(client)
        await client.post(f"/runs/{run_id}/pause")
        await wait_status(app, run_id, "cancelled")
        again = await client.post(f"/runs/{run_id}/pause")

    assert again.status_code == 409
    assert fake.cancel_calls == ["sess_1"]


async def test_resume_continues_in_the_same_session_and_stream():
    fake = PausableTrueForge()
    async with api(fake, github_state()) as (client, app):
        run_id = await start(client)
        await client.post(f"/runs/{run_id}/pause")
        await wait_status(app, run_id, "cancelled")
        response = await client.post(f"/runs/{run_id}/resume")
        await wait_status(app, run_id, "done")
        events = await client.get(f"/runs/{run_id}/events")

    assert response.status_code == 200 and response.json()["status"] == "running"
    assert fake.turns[1] == ("sess_1", RESUME_PROMPT)
    sequences = [int(line[4:]) for line in events.text.splitlines() if line.startswith("id: ")]
    assert sequences == [1, 2, 3, 4, 5, 6]
    types = [json.loads(line[6:])["type"] for line in events.text.splitlines() if line.startswith("data: ")]
    assert types == ["turn.created", "model.message", "turn.done", "turn.created", "model.message", "turn.done"]


async def test_resume_refuses_a_running_run():
    fake = PausableTrueForge()
    async with api(fake, github_state()) as (client, app):
        run_id = await start(client)
        response = await client.post(f"/runs/{run_id}/resume")
        fake.cancelled.set()

    assert response.status_code == 409
    assert len(fake.turns) == 1
