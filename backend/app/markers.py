"""The one parser for facts the agent reports as lines of the form: GREENLIGHT_FACT {"kind": "...", ...}"""

import json
from typing import Any

MARKER = "GREENLIGHT_FACT "


def extract_facts(text: str | None) -> list[dict[str, Any]]:
    """Every well formed fact in text, in order. Malformed lines are skipped, never raised."""
    if not text or MARKER not in text:
        return []

    facts = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith(MARKER):
            continue
        try:
            fact = json.loads(line[len(MARKER) :])
        except json.JSONDecodeError:
            continue
        if isinstance(fact, dict) and isinstance(fact.get("kind"), str) and fact["kind"]:
            facts.append(fact)
    return facts


class FactStream:
    """Facts from a live TrueForge event stream, each returned once, as soon as its line is complete.

    An agent message arrives either as one complete model.message, or as an empty model.message
    followed by model.message.delta fragments, so a fact line can be split across events.
    """

    def __init__(self) -> None:
        # message id -> [text so far, lines already parsed]
        self._messages: dict[str, list[Any]] = {}
        self._open: set[str] = set()

    def feed(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        kind, message_id = event.get("type"), event.get("id")
        if kind not in ("model.message", "model.message.delta") or not isinstance(message_id, str):
            return self._flush_open()

        found = self._flush_open(keep=message_id)
        buffer = self._messages.setdefault(message_id, ["", 0])
        if kind == "model.message":
            text = message_text(event.get("content"))
            if not text:
                self._open.add(message_id)
                return found
            buffer[0] = text
            return found + self._drain(message_id, final=True)

        if isinstance(event.get("content"), str):
            buffer[0] += event["content"]
        self._open.add(message_id)
        return found + self._drain(message_id, final=bool(event.get("finish_reason")))

    def _flush_open(self, keep: str | None = None) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        for message_id in list(self._open - {keep}):
            found += self._drain(message_id, final=True)
        return found

    def _drain(self, message_id: str, final: bool) -> list[dict[str, Any]]:
        buffer = self._messages[message_id]
        lines = buffer[0].split("\n")
        upto = len(lines) if final else len(lines) - 1
        found = [fact for line in lines[buffer[1] : upto] for fact in extract_facts(line)]
        buffer[1] = max(buffer[1], upto)
        if final:
            self._open.discard(message_id)
        return found


def message_text(content: Any) -> str:
    """Text of a model.message content field: a string, a list of content parts, or null."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            part["text"] for part in content if isinstance(part, dict) and isinstance(part.get("text"), str)
        )
    return ""
