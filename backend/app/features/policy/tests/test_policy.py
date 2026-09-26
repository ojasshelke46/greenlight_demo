from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from app import hooks
from app.config import get_settings
from app.features.policy import checks, loader
from app.features.policy.checks import majors_message_part, policy_check
from app.github import GitHubClient
from app.hooks import Allow, ApprovalContext, Deny, NeedMore, PendingActionInfo, PriorApproval, RunInfo
from app.main import create_app
from app.runs import RunManager
from tests.test_approval import AUTH, github_state, paused_run
from tests.test_seams import RecordingTrueForge

IST = ZoneInfo("Asia/Kolkata")
CONTENTS = "/repos/acme/widgets/contents/.greenlight.yml"
FRIDAY = datetime(2026, 9, 25, tzinfo=IST)  # a Friday


class GitHub:
    """Mock GitHub: the policy file (None means 404), plus what the approval checks read."""

    def __init__(self, policy: str | None = None, status: int = 200, content_type: str = "application/vnd.github.raw+json"):
        self.policy, self.status, self.content_type = policy, status, content_type
        self.gh = github_state()
        self.contents_calls: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.startswith("/repos/acme/widgets/contents/"):
            self.contents_calls.append(request)
            if self.policy is None:
                return httpx.Response(404, json={"message": "Not Found"})
            if self.status != 200:
                return httpx.Response(self.status, json={"message": "boom"})
            return httpx.Response(200, text=self.policy, headers={"content-type": self.content_type})
        if path == "/repos/acme/widgets":
            return httpx.Response(200, json={"private": False, "default_branch": "main", "permissions": {"push": self.gh["push"]}})
        if path == "/repos/acme/widgets/pulls/7":
            return httpx.Response(200, json=self.gh["pr"])
        return httpx.Response(404, json={"message": "Not Found"})


@pytest.fixture
def github():
    """Binds the loader to a stand in app whose shared GitHub client is the mock."""
    mock = GitHub()
    client = GitHubClient(httpx.AsyncClient(transport=httpx.MockTransport(mock.handler)), get_settings())
    loader.bind(SimpleNamespace(state=SimpleNamespace(github_client=client)))
    yield mock
    loader.bind(None)


def at(when: datetime):
    return lambda: when


def ctx(approver="ojasshelke46", prior=()):
    return ApprovalContext(
        run=RunInfo(id="run_1", owner="acme", repo="widgets", mode="ship", via_fork=False),
        pending_action=PendingActionInfo(tool_name="merge_pull_request", arguments={"pullNumber": 7}),
        approver=approver,
        prior_approvals=list(prior),
    )


def prior(approver, result="needs_more: 1 of 2 approvals", decision="approve"):
    return PriorApproval(approver=approver, decision=decision, decided_at="t", result=result)


FREEZE_OVERNIGHT = """
freeze:
  timezone: Asia/Kolkata
  windows:
    - {start: "Fri 23:00", end: "Sat 01:00"}
"""

FREEZE_WEEKEND = """
freeze:
  timezone: Asia/Kolkata
  windows:
    - {start: "Fri 17:00", end: "Mon 09:00"}
"""


# Missing and invalid files


async def test_missing_file_gives_the_defaults(github):
    loaded = await loader.load_policy("acme", "widgets")
    assert (loaded.exists, loaded.error) == (False, None)
    assert loaded.policy.model_dump() == {
        "approvers": [],
        "required_approvals": 1,
        "allow_major_upgrades": True,
        "freeze": {"timezone": "UTC", "windows": []},
    }
    assert await policy_check(ctx(approver="anyone at all")) == Allow()
    assert await majors_message_part("acme", "widgets") is None


async def test_the_file_is_read_from_the_default_branch_with_the_bot_token(github, monkeypatch):
    github.policy = "required_approvals: 1\n"
    monkeypatch.setenv("POLICY_FILE_PATH", "config/greenlight policy.yml")
    get_settings.cache_clear()
    await loader.load_policy("acme", "widgets")
    [request] = github.contents_calls
    assert request.url.path == "/repos/acme/widgets/contents/config/greenlight policy.yml"
    assert "ref" not in request.url.params
    assert request.headers["authorization"] == "Bearer test-token"
    assert request.headers["accept"] == "application/vnd.github.raw+json"


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("required_approvals: [1\n", "is not valid YAML"),
        ("required_approvals: 5\n", "required_approvals: Input should be less than or equal to 3"),
        ("required_approvals: 0\n", "required_approvals: Input should be greater than or equal to 1"),
        ('required_approvals: "2"\n', "required_approvals: Input should be a valid integer"),
        ("allow_major_upgrades: maybe\n", "allow_major_upgrades: Input should be a valid boolean"),
        ("approvers: ojasshelke46\n", "approvers: Input should be a valid list"),
        ("required_approval: 2\n", "required_approval: Extra inputs are not permitted"),
        ('freeze: {windows: [{start: "Friday 5pm", end: "Mon 09:00"}]}\n', "is not a weekly time like 'Fri 17:00'"),
        ('freeze: {windows: [{start: "Fri 17:00", end: "fri 17:00"}]}\n', "needs different start and end times"),
        ("freeze: {timezone: Mars/Olympus}\n", "'Mars/Olympus' is not a known timezone"),
        ("- ojasshelke46\n", "must be a mapping of settings"),
    ],
)
async def test_an_invalid_file_blocks_merging_with_the_validation_message(github, text, message):
    github.policy = text
    loaded = await loader.load_policy("acme", "widgets")
    assert loaded.exists and loaded.policy is None and message in loaded.error

    result = await policy_check(ctx())
    assert isinstance(result, Deny) and message in result.reason and ".greenlight.yml" in result.reason
    assert await majors_message_part("acme", "widgets") is None


async def test_an_empty_file_gives_the_defaults(github):
    github.policy = "# nothing set yet\n"
    loaded = await loader.load_policy("acme", "widgets")
    assert loaded.exists and loaded.error is None and loaded.policy.required_approvals == 1


async def test_a_directory_at_the_policy_path_is_invalid(github):
    github.policy, github.content_type = "[]", "application/json; charset=utf-8"
    assert "is a directory" in (await loader.load_policy("acme", "widgets")).error


async def test_github_failures_block_merging_and_are_not_cached(github):
    github.policy, github.status = "required_approvals: 1\n", 502
    assert "Could not read .greenlight.yml from GitHub (502)" in (await loader.load_policy("acme", "widgets")).error
    assert isinstance(await policy_check(ctx()), Deny)
    github.status = 200
    assert (await loader.load_policy("acme", "widgets")).error is None
    assert len(github.contents_calls) == 3


async def test_policies_are_cached_for_ten_seconds(github, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(loader, "monotonic", lambda: clock[0])
    github.policy = "required_approvals: 2\n"
    await loader.load_policy("acme", "widgets")
    github.policy = "required_approvals: 3\n"
    clock[0] += 9.9
    assert (await loader.load_policy("ACME", "Widgets")).policy.required_approvals == 2
    clock[0] += 0.2
    assert (await loader.load_policy("acme", "widgets")).policy.required_approvals == 3
    assert len(github.contents_calls) == 2


# Approvers


async def test_an_unknown_approver_is_denied_naming_the_allowed_ones(github):
    github.policy = "approvers: [ojasshelke46, DeepanshuDadhich]\n"
    result = await policy_check(ctx(approver="demo"))
    assert result == Deny("demo is not an allowed approver. Allowed approvers: ojasshelke46, DeepanshuDadhich")
    assert await policy_check(ctx(approver="deepanshudadhich")) == Allow()


# Freeze windows


@pytest.mark.parametrize(
    ("now", "frozen"),
    [
        (FRIDAY.replace(hour=22, minute=59), False),
        (FRIDAY.replace(hour=23), True),
        (FRIDAY.replace(hour=23, minute=30), True),
        (FRIDAY.replace(hour=23, minute=30).astimezone(timezone.utc), True),  # Fri 18:00 UTC, judged in IST
        ((FRIDAY + timedelta(days=1)).replace(hour=0, minute=59), True),
        ((FRIDAY + timedelta(days=1)).replace(hour=1), False),
        (datetime(2026, 9, 25, 23, 30, tzinfo=timezone.utc), False),  # Sat 05:00 IST
    ],
)
async def test_a_freeze_across_midnight(github, monkeypatch, now, frozen):
    github.policy = FREEZE_OVERNIGHT
    monkeypatch.setattr(checks, "now", at(now))
    result = await policy_check(ctx())
    assert result == (Deny("Merges frozen until Sat 01:00 Asia/Kolkata") if frozen else Allow())


@pytest.mark.parametrize(
    ("now", "frozen"),
    [
        (FRIDAY - timedelta(days=1), False),  # Thu 00:00
        (FRIDAY.replace(hour=16, minute=59), False),
        (FRIDAY.replace(hour=17), True),
        (FRIDAY + timedelta(days=1, hours=12), True),  # Sat 12:00
        (FRIDAY + timedelta(days=2, hours=23, minutes=59), True),  # Sun 23:59
        (FRIDAY + timedelta(days=3, hours=8, minutes=59), True),  # Mon 08:59
        (FRIDAY + timedelta(days=3, hours=9), False),  # Mon 09:00
    ],
)
async def test_a_freeze_across_the_weekend(github, monkeypatch, now, frozen):
    github.policy = FREEZE_WEEKEND
    monkeypatch.setattr(checks, "now", at(now))
    result = await policy_check(ctx())
    assert result == (Deny("Merges frozen until Mon 09:00 Asia/Kolkata") if frozen else Allow())


async def test_a_freeze_that_wraps_past_sunday_into_monday(github, monkeypatch):
    github.policy = 'freeze: {timezone: Asia/Kolkata, windows: [{start: "Sun 22:00", end: "Mon 06:00"}]}\n'
    sunday = FRIDAY + timedelta(days=2)
    for when, frozen in [(sunday.replace(hour=21, minute=59), False), (sunday.replace(hour=23), True), (sunday + timedelta(hours=29, minutes=59), True), (sunday + timedelta(hours=30), False)]:
        monkeypatch.setattr(checks, "now", at(when))
        assert isinstance(await policy_check(ctx()), Deny) is frozen, when


# Required approvals


async def test_two_approvals_count_distinct_approvers_only(github):
    github.policy = "required_approvals: 2\n"
    assert await policy_check(ctx(approver="ojasshelke46")) == NeedMore("1 of 2 approvals")

    # The same approver again, in any case, is still one approval.
    assert await policy_check(ctx(approver="OJASSHELKE46", prior=[prior("ojasshelke46")])) == NeedMore("1 of 2 approvals")
    assert await policy_check(ctx(approver="DeepanshuDadhich", prior=[prior("ojasshelke46")])) == Allow()

    # Refused attempts and rejections never count towards the total.
    ignored = [prior("shreyashp25", result="refused: Merges frozen until Mon 09:00"), prior("shreyashp25", decision="reject", result="accepted")]
    assert await policy_check(ctx(approver="DeepanshuDadhich", prior=ignored)) == NeedMore("1 of 2 approvals")


async def test_approvals_from_people_no_longer_allowed_do_not_count(github):
    github.policy = "approvers: [ojasshelke46, DeepanshuDadhich]\nrequired_approvals: 2\n"
    assert await policy_check(ctx(approver="ojasshelke46", prior=[prior("removed_teammate")])) == NeedMore("1 of 2 approvals")


async def test_rules_apply_in_order(github, monkeypatch):
    github.policy = FREEZE_WEEKEND + "approvers: [ojasshelke46]\nrequired_approvals: 3\n"
    monkeypatch.setattr(checks, "now", at(FRIDAY + timedelta(days=1)))
    # The approver rule comes before the freeze, and the freeze before the approval count.
    assert await policy_check(ctx(approver="demo")) == Deny("demo is not an allowed approver. Allowed approvers: ojasshelke46")
    assert await policy_check(ctx(approver="ojasshelke46")) == Deny("Merges frozen until Mon 09:00 Asia/Kolkata")
    monkeypatch.setattr(checks, "now", at(FRIDAY - timedelta(days=1)))
    assert await policy_check(ctx(approver="ojasshelke46")) == NeedMore("1 of 3 approvals")


# The run message part


async def test_majors_message_part(github):
    github.policy = "allow_major_upgrades: false\n"
    assert await majors_message_part("acme", "widgets") == "POLICY: allow_major_upgrades=false"
    github.policy = "allow_major_upgrades: true\n"
    loader._cache.clear()
    assert await majors_message_part("acme", "widgets") is None


def test_hooks_are_registered_at_startup():
    assert policy_check in hooks._approval_checks
    assert majors_message_part in hooks._run_message_parts


# Through the API


@asynccontextmanager
async def api(github: GitHub, fake: RecordingTrueForge):
    app = create_app()
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.MockTransport(github.handler)) as github_http:
            app.state.github_client = GitHubClient(github_http, get_settings())
            app.state.trueforge_client = fake
            app.state.run_manager = RunManager(app.state.ledger, fake)
            fake.ledger = app.state.ledger
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=AUTH) as client:
                yield client, app


def approve(client, run_id, approver):
    return client.post(f"/runs/{run_id}/approval", json={"decision": "approve", "approver": approver})


async def test_run_message_carries_the_majors_policy():
    fake = RecordingTrueForge()
    async with api(GitHub("allow_major_upgrades: false\n"), fake) as (client, app):
        await paused_run(client, app)
    assert fake.messages[0].endswith("\n\nPOLICY: allow_major_upgrades=false")
    assert "MODE: ship\n\n" in fake.messages[0]


async def test_two_approvers_through_the_approval_endpoint():
    fake = RecordingTrueForge()
    async with api(GitHub("approvers: [ojasshelke46, DeepanshuDadhich]\nrequired_approvals: 2\n"), fake) as (client, app):
        run_id = await paused_run(client, app)
        stranger = await approve(client, run_id, "demo")
        first = await approve(client, run_id, "ojasshelke46")
        again = await approve(client, run_id, "ojasshelke46")
        assert fake.resumes == []
        second = await approve(client, run_id, "DeepanshuDadhich")

    assert stranger.status_code == 409 and "Allowed approvers: ojasshelke46, DeepanshuDadhich" in stranger.json()["detail"]
    assert first.status_code == 202 and first.json()["reason"] == "1 of 2 approvals"
    assert again.status_code == 202 and again.json()["replayed"] is True
    assert second.status_code == 200, second.text
    assert len(fake.resumes) == 1


async def test_policy_endpoint_reports_the_policy_and_an_active_freeze(monkeypatch):
    monkeypatch.setattr(checks, "now", at(FRIDAY + timedelta(days=1, hours=12)))
    async with api(GitHub(FREEZE_WEEKEND + "allow_major_upgrades: false\n"), RecordingTrueForge()) as (client, _):
        response = await client.get("/features/policy", params={"repo": "https://github.com/acme/widgets"})

    assert response.status_code == 200
    body = response.json()
    assert (body["repo"], body["path"], body["exists"], body["error"]) == ("acme/widgets", ".greenlight.yml", True, None)
    assert body["policy"]["allow_major_upgrades"] is False
    assert body["policy"]["freeze"]["windows"] == [{"start": "Fri 17:00", "end": "Mon 09:00"}]
    assert body["freeze"]["active"] is True and body["freeze"]["until"] == "Mon 09:00 Asia/Kolkata"
    assert datetime.fromisoformat(body["freeze"]["ends_at"]) == datetime(2026, 9, 28, 9, 0, tzinfo=IST)


async def test_policy_endpoint_reports_missing_and_invalid_files():
    async with api(GitHub(None), RecordingTrueForge()) as (client, _):
        missing = (await client.get("/features/policy", params={"repo": "https://github.com/acme/widgets"})).json()
        bad_url = await client.get("/features/policy", params={"repo": "https://gitlab.com/acme/widgets"})
        unauthorized = await client.get("/features/policy", params={"repo": "https://github.com/acme/widgets"}, headers={"Authorization": "Bearer nope"})
    async with api(GitHub("required_approvals: 9\n"), RecordingTrueForge()) as (client, _):
        invalid = (await client.get("/features/policy", params={"repo": "https://github.com/acme/widgets"})).json()

    assert missing["exists"] is False and missing["error"] is None and missing["policy"]["required_approvals"] == 1
    assert missing["freeze"] == {"active": False, "ends_at": None, "until": None}
    assert bad_url.status_code == 400 and unauthorized.status_code == 401
    assert invalid["exists"] is True and invalid["policy"] is None and "less than or equal to 3" in invalid["error"]
