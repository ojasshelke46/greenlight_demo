"""The .greenlight.yml policy: what it may contain, and whether a freeze window is active."""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, field_validator, model_validator

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
MINUTES_PER_WEEK = 7 * 24 * 60
_WEEK_TIME = re.compile(
    r"^(mon(?:day)?|tue(?:sday)?|wed(?:nesday)?|thu(?:rsday)?|fri(?:day)?|sat(?:urday)?|sun(?:day)?)\s+([01]?\d|2[0-3]):([0-5]\d)$",
    re.IGNORECASE,
)


def week_minute(value: str) -> int:
    """'Fri 17:00' as minutes since Monday 00:00."""
    match = _WEEK_TIME.match(value.strip())
    if match is None:
        raise ValueError(f"{value!r} is not a weekly time like 'Fri 17:00'")
    return DAYS.index(match.group(1)[:3].lower()) * 1440 + int(match.group(2)) * 60 + int(match.group(3))


class FreezeWindow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: StrictStr
    end: StrictStr

    @field_validator("start", "end")
    @classmethod
    def _weekly_time(cls, value: str) -> str:
        week_minute(value)
        return value

    @model_validator(mode="after")
    def _not_empty(self) -> "FreezeWindow":
        if week_minute(self.start) == week_minute(self.end):
            raise ValueError("a freeze window needs different start and end times")
        return self

    def contains(self, minute: int) -> bool:
        start, end = week_minute(self.start), week_minute(self.end)
        # A window whose end comes before its start wraps past Sunday into the next week.
        return start <= minute < end if start < end else minute >= start or minute < end


class Freeze(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timezone: StrictStr = "UTC"
    windows: list[FreezeWindow] = Field(default_factory=list)

    @field_validator("timezone")
    @classmethod
    def _known_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"{value!r} is not a known timezone") from exc
        return value


class Policy(BaseModel):
    # Unknown keys are errors: a misspelt rule silently ignored would make the merge gate weaker than written.
    model_config = ConfigDict(extra="forbid")

    approvers: list[StrictStr] = Field(default_factory=list)
    required_approvals: StrictInt = Field(default=1, ge=1, le=3)
    allow_major_upgrades: StrictBool = True
    freeze: Freeze = Field(default_factory=Freeze)

    def allows(self, approver: str) -> bool:
        return not self.approvers or approver.casefold() in {a.casefold() for a in self.approvers}


@dataclass(frozen=True)
class ActiveFreeze:
    ends_at: datetime
    # The end as the policy wrote it, with its timezone, e.g. "Mon 09:00 Asia/Kolkata".
    until: str


def active_freeze(freeze: Freeze, now: datetime) -> ActiveFreeze | None:
    local = now.astimezone(ZoneInfo(freeze.timezone))
    minute = local.weekday() * 1440 + local.hour * 60 + local.minute
    active = [w for w in freeze.windows if w.contains(minute)]
    if not active:
        return None

    def minutes_left(window: FreezeWindow) -> int:
        return (week_minute(window.end) - minute) % MINUTES_PER_WEEK

    # Overlapping windows: the merge waits for the one that ends last.
    last = max(active, key=minutes_left)
    ends_at = local.replace(second=0, microsecond=0) + timedelta(minutes=minutes_left(last))
    return ActiveFreeze(ends_at=ends_at, until=f"{last.end} {freeze.timezone}")
