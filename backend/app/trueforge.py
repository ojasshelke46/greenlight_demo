"""Async client for the TrueForge HTTP API (/api/v1).

Endpoints are taken from docs/trueforge_openapi.json (fetched from
TRUEFORGE_BASE_URL/api/v1/openapi.json).
"""

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import httpx

API_PREFIX = "/api/v1"


class TrueForgeError(RuntimeError):
    pass


class TurnStreamGone(TrueForgeError):
    """The turn's live event buffer no longer exists (HTTP 412), e.g. the turn finished long ago."""


@dataclass(frozen=True)
class TurnHandle:
    session_id: str
    turn_id: str
    status: str


@dataclass(frozen=True)
class TurnEvent:
    # SSE `id:` field. Pass the last one seen as after_sequence to resume a dropped stream.
    sequence: int | None
    type: str
    data: dict[str, Any]


@dataclass(frozen=True)
class PendingAction:
    """Tool calls paused on tool.approval_required, plus the turn they paused."""

    turn_id: str
    thread_id: str
    tool_call_ids: list[str] = field(default_factory=list)

    @classmethod
    def from_event(cls, turn_id: str, event: dict[str, Any]) -> "PendingAction":
        if event.get("type") != "tool.approval_required":
            raise ValueError(f"expected tool.approval_required, got {event.get('type')!r}")
        return cls(
            turn_id=turn_id,
            thread_id=event["thread_id"],
            tool_call_ids=[call["id"] for call in event["tool_calls"]],
        )


class TrueForgeClient:
    def __init__(self, http_client: httpx.AsyncClient, base_url: str, agent_name: str) -> None:
        self._http = http_client
        self._base_url = base_url.rstrip("/")
        self._agent_name = agent_name

    def _url(self, path: str) -> str:
        return f"{self._base_url}{API_PREFIX}{path}"

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        response = await self._http.request(method, self._url(path), **kwargs)
        if response.is_error:
            raise TrueForgeError(f"{method} {path} -> {response.status_code}: {response.text[:500]}")
        return response.json()

    async def health(self) -> bool:
        try:
            response = await self._http.get(self._url("/capabilities"), timeout=2.0)
            return response.status_code == 200
        except httpx.HTTPError:
            return False

    async def get_agent_id(self, name: str | None = None) -> str:
        name = name or self._agent_name
        # agent_name is a case insensitive substring filter, so match the exact name here.
        body = await self._request("GET", "/agents", params={"agent_name": name, "limit": 100})
        for agent in body["data"]:
            if agent["name"] == name:
                return agent["id"]
        raise TrueForgeError(f"agent {name!r} not found")

    async def create_session(self, agent_id: str) -> str:
        # Sessions bind to an agent by name, not id, so resolve the name first.
        agent = await self._request("GET", f"/agents/{agent_id}")
        body = await self._request("POST", "/sessions", json={"agent": {"name": agent["data"]["name"]}})
        return body["data"]["id"]

    async def _create_turn(
        self,
        session_id: str,
        input_items: list[dict[str, Any]],
        previous_turn_id: str = "auto",
    ) -> TurnHandle:
        body = await self._request(
            "POST",
            f"/sessions/{session_id}/turns",
            json={"input": input_items, "previous_turn_id": previous_turn_id, "stream": False},
        )
        turn = body["data"]
        return TurnHandle(session_id=session_id, turn_id=turn["id"], status=turn["state"]["status"])

    async def start_turn(self, session_id: str, message: str) -> TurnHandle:
        return await self._create_turn(session_id, [{"type": "user.message", "content": message}])

    async def stream_turn(
        self,
        session_id: str,
        turn_id: str,
        after_sequence: int | None = None,
    ) -> AsyncIterator[TurnEvent]:
        # TrueForge closes a subscribe stream after TURN_SUBSCRIBE_TIMEOUT_MS (default 10 min),
        # possibly before turn.done is sent, so reconnect from the last sequence until it arrives.
        last_sequence = after_sequence
        while True:
            try:
                async for event in self._subscribe(session_id, turn_id, last_sequence):
                    yield event
                    if event.sequence is not None:
                        last_sequence = event.sequence
                    if event.type == "turn.done":
                        return
            except TurnStreamGone:
                pass

            turn = await self._request("GET", f"/sessions/{session_id}/turns/{turn_id}")
            if turn["data"]["state"]["status"] != "running":
                yield await self._stored_turn_done(session_id, turn_id)
                return

    async def _subscribe(
        self,
        session_id: str,
        turn_id: str,
        after_sequence: int | None,
    ) -> AsyncIterator[TurnEvent]:
        params = {} if after_sequence is None else {"after_sequence_number": after_sequence}
        url = self._url(f"/sessions/{session_id}/turns/{turn_id}/subscribe")
        timeout = httpx.Timeout(10.0, read=None)

        async with self._http.stream("GET", url, params=params, timeout=timeout) as response:
            if response.status_code == 412:
                raise TurnStreamGone(f"live stream for turn {turn_id} is gone")
            if response.is_error:
                await response.aread()
                raise TrueForgeError(f"subscribe {turn_id} -> {response.status_code}: {response.text[:500]}")

            async for event in _parse_sse(response):
                yield event

    async def get_tool_call(
        self, session_id: str, turn_id: str, tool_call_id: str, source_event_id: str
    ) -> dict[str, Any] | None:
        """The tool call as TrueForge persisted it, i.e. exactly what it will execute on approval."""
        params: dict[str, Any] = {"limit": 100}
        while True:
            body = await self._request("GET", f"/sessions/{session_id}/turns/{turn_id}/events", params=params)
            for event in body["data"]:
                if event["id"] == source_event_id and event["type"] == "model.message":
                    for call in event.get("tool_calls") or []:
                        if call.get("id") == tool_call_id:
                            return call
                    return None
            next_token = body["pagination"].get("next_page_token")
            if not next_token:
                return None
            params["page_token"] = next_token

    async def _stored_turn_done(self, session_id: str, turn_id: str) -> TurnEvent:
        # Persisted events carry no sequence number.
        body = await self._request(
            "GET",
            f"/sessions/{session_id}/turns/{turn_id}/events",
            params={"order": "desc", "limit": 10},
        )
        for event in body["data"]:
            if event["type"] == "turn.done":
                return TurnEvent(sequence=None, type="turn.done", data=event)
        raise TrueForgeError(f"turn {turn_id} finished but has no stored turn.done event")

    async def resume_with_approval(
        self,
        session_id: str,
        pending_action: PendingAction,
        approved: bool,
        reason: str | None = None,
    ) -> TurnHandle:
        decision: dict[str, Any] = {"status": "allow" if approved else "deny"}
        if not approved and reason:
            decision["reason"] = reason
        items = [
            {
                "type": "user.tool_approval",
                "thread_id": pending_action.thread_id,
                "tool_call_id": tool_call_id,
                "approval": decision,
            }
            for tool_call_id in pending_action.tool_call_ids
        ]
        return await self._create_turn(session_id, items, previous_turn_id=pending_action.turn_id)


async def _parse_sse(response: httpx.Response) -> AsyncIterator[TurnEvent]:
    event_id: str | None = None
    event_name: str | None = None
    data_lines: list[str] = []

    async for line in response.aiter_lines():
        if line == "":
            if data_lines:
                data = json.loads("\n".join(data_lines))
                yield TurnEvent(
                    sequence=int(event_id) if event_id and event_id.isdigit() else None,
                    type=data.get("type") or event_name or "message",
                    data=data,
                )
            event_id, event_name, data_lines = None, None, []
            continue
        if line.startswith(":"):
            continue
        key, _, value = line.partition(":")
        value = value.removeprefix(" ")
        if key == "id":
            event_id = value
        elif key == "event":
            event_name = value
        elif key == "data":
            data_lines.append(value)
