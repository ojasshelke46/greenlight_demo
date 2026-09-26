"""The TrueForge agents Greenlight drives, one per role, resolved by exact name and cached."""

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from app.config import AGENTS, CHAT_ROLES

logger = logging.getLogger(__name__)

RESOLVE_TIMEOUT_SECONDS = 5.0


class UnknownAgent(RuntimeError):
    pass


@dataclass
class AgentInfo:
    role: str
    name: str
    id: str | None = None
    model: str | None = None

    @property
    def found(self) -> bool:
        return self.id is not None

    def as_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "name": self.name,
            "model": self.model,
            "found": self.found,
            "chat": self.role in CHAT_ROLES,
        }


class AgentRegistry:
    def __init__(self, agents: dict[str, str] = AGENTS) -> None:
        self._agents = {role: AgentInfo(role=role, name=name) for role, name in agents.items()}
        self._lock = asyncio.Lock()

    def roles(self) -> list[str]:
        return list(self._agents)

    def info(self, role: str) -> AgentInfo:
        if role not in self._agents:
            raise UnknownAgent(f"Unknown agent role {role!r}")
        return self._agents[role]

    def all(self) -> list[AgentInfo]:
        return list(self._agents.values())

    async def resolve(self, trueforge: Any) -> None:
        """Look up every agent not found yet. Never raises: an agent TrueForge does not have stays not found."""
        async with self._lock:
            missing = [info for info in self._agents.values() if not info.found]
            if not missing:
                return
            try:
                async with asyncio.timeout(RESOLVE_TIMEOUT_SECONDS):
                    listed = {agent["name"]: agent for agent in await trueforge.list_agents()}
                    for info in missing:
                        agent = listed.get(info.name)
                        if agent is None:
                            continue
                        detail = await trueforge.get_agent(agent["id"])
                        info.model = ((detail.get("manifest") or {}).get("model") or {}).get("name")
                        info.id = agent["id"]
            except Exception as exc:
                logger.warning("Could not resolve TrueForge agents: %s", type(exc).__name__)

    async def agent_id(self, role: str, trueforge: Any) -> str:
        """The cached id, or a fresh exact name lookup when startup could not find it."""
        info = self.info(role)
        if info.id is None:
            info.id = await trueforge.get_agent_id(info.name)
        return info.id
