"""Response models for fleet rows: the shapes scan.py streams, also used for the scout agent's board so the
frontend renders both with the same fleet rows."""

import re
from typing import Any, Literal

from pydantic import BaseModel

from app.features.fleet.scan import SEVERITIES, WEIGHT

Severity = Literal["critical", "high", "moderate", "low", "unknown"]


class FleetAdvisory(BaseModel):
    id: str
    package: str
    version: str
    severity: Severity
    summary: str


class FleetRepoResult(BaseModel):
    repo: str
    default_branch: str | None
    status: Literal["ok", "no_lockfile", "error"]
    error: str | None
    packages: int
    counts: dict[Severity, int]
    top: list[FleetAdvisory]
    risk: int


class FleetTotals(BaseModel):
    repos: int
    ok: int
    no_lockfile: int
    error: int
    counts: dict[Severity, int]
    risk: int


class FleetSummary(BaseModel):
    target: str
    totals: FleetTotals
    repos: list[FleetRepoResult]


# Only a reason that says the lockfile is missing counts as no_lockfile; "scan failed, package-lock.json present"
# is an error, never a clean row.
_NO_LOCKFILE = re.compile(r"^\s*(no|missing|without)\b[^.;]*(lock ?file|package-lock)", re.IGNORECASE)


def _scout_row(entry: dict[str, Any]) -> FleetRepoResult:
    """One scout repo as a fleet row. Scout reports only the highest severity and a total, so one advisory is
    counted at that severity and the rest as unknown, rather than guessing how they split."""
    counts = dict.fromkeys(SEVERITIES, 0)
    skipped = entry.get("skipped_reason")
    total = entry.get("advisory_count") if isinstance(entry.get("advisory_count"), int) else 0
    severity = str(entry.get("max_severity") or "").lower()
    severity = "moderate" if severity == "medium" else severity
    if not skipped and total > 0:
        counts[severity if severity in WEIGHT else "unknown"] += 1
        counts["unknown"] += total - 1

    top = []
    for advisory in entry.get("top_advisories") or []:
        if not isinstance(advisory, dict):
            continue
        fixed = advisory.get("fixed")
        top.append(
            FleetAdvisory(
                id=str(advisory.get("id") or ""),
                package=str(advisory.get("package") or ""),
                version=str(advisory.get("current") or ""),
                severity="unknown",
                summary=f"Fixed in {fixed}" if fixed else "",
            )
        )

    if skipped:
        status = "no_lockfile" if _NO_LOCKFILE.search(str(skipped)) else "error"
    else:
        status = "ok"
    return FleetRepoResult(
        repo=str(entry.get("repo") or ""),
        default_branch=None,
        status=status,
        error=str(skipped) if skipped and status == "error" else None,
        packages=0,
        counts=counts,
        top=top[:3],
        risk=sum(WEIGHT[s] * n for s, n in counts.items()),
    )


def scout_board(target: str, result: dict[str, Any]) -> FleetSummary:
    """The scout agent's parsed result ({"repos": [...]}) as a fleet summary, riskiest first."""
    rows = [_scout_row(entry) for entry in result.get("repos") or [] if isinstance(entry, dict)]
    counts = dict.fromkeys(SEVERITIES, 0)
    for row in rows:
        for severity, count in row.counts.items():
            counts[severity] += count
    return FleetSummary(
        target=target,
        totals=FleetTotals(
            repos=len(rows),
            ok=sum(r.status == "ok" for r in rows),
            no_lockfile=sum(r.status == "no_lockfile" for r in rows),
            error=sum(r.status == "error" for r in rows),
            counts=counts,
            risk=sum(r.risk for r in rows),
        ),
        repos=sorted(rows, key=lambda r: (-r.risk, r.repo.lower())),
    )
