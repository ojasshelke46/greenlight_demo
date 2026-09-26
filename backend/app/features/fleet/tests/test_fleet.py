import asyncio
import json
from contextlib import asynccontextmanager

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from app.features.fleet import router as fleet_router
from app.features.fleet.lockfile import LockfileError, parse_lockfile
from app.main import create_app

AUTH = {"Authorization": "Bearer test-key"}


def v1(dependencies):
    return json.dumps({"name": "app", "lockfileVersion": 1, "dependencies": dependencies})


def v2(packages, dependencies=None):
    return json.dumps({"name": "app", "lockfileVersion": 2, "packages": packages, "dependencies": dependencies or {"stale": {"version": "0.0.1"}}})


def v3(packages):
    return json.dumps({"name": "app", "lockfileVersion": 3, "packages": packages})


def detail(vuln_id, severity=None, summary=None):
    body = {"id": vuln_id, "summary": summary or f"{vuln_id} summary"}
    if severity is not None:
        body["database_specific"] = {"severity": severity}
    return body


class World:
    """Mock GitHub and OSV, recording every request and how many ran at once."""

    def __init__(self):
        self.repos: dict[str, dict] = {}
        self.orgs: dict[str, list[dict]] = {}
        self.vulns: dict[tuple[str, str], list[str]] = {}
        self.details: dict[str, dict] = {}
        self.batches: list[list[tuple[str, str]]] = []
        self.detail_calls: dict[str, int] = {}
        self.calls: list[str] = []
        self.delay = 0.0
        self.in_flight = {"lockfile": 0, "detail": 0}
        self.peak = {"lockfile": 0, "detail": 0}

    def repo(self, full_name, lockfile=None, status=200, branch="main"):
        self.repos[full_name] = {"lockfile": lockfile, "status": status, "branch": branch}

    async def _timed(self, kind, make):
        self.in_flight[kind] += 1
        self.peak[kind] = max(self.peak[kind], self.in_flight[kind])
        try:
            await asyncio.sleep(self.delay)
            return make()
        finally:
            self.in_flight[kind] -= 1

    async def handler(self, request: httpx.Request) -> httpx.Response:
        host, path = request.url.host, request.url.path
        self.calls.append(f"{request.method} {host}{path}")
        if host == "api.osv.dev":
            if path == "/v1/querybatch":
                queries = json.loads(request.content)["queries"]
                pairs = [(q["package"]["name"], q["version"]) for q in queries]
                assert all(q["package"]["ecosystem"] == "npm" for q in queries)
                self.batches.append(pairs)
                return httpx.Response(200, json={"results": [{"vulns": [{"id": i} for i in self.vulns[p]]} if self.vulns.get(p) else {} for p in pairs]})
            vuln_id = path.rsplit("/", 1)[1]
            self.detail_calls[vuln_id] = self.detail_calls.get(vuln_id, 0) + 1
            return await self._timed("detail", lambda: httpx.Response(200, json=self.details[vuln_id]) if vuln_id in self.details else httpx.Response(404))

        assert request.headers["authorization"] == "Bearer test-token"
        parts = path.strip("/").split("/")
        if parts[0] in ("orgs", "users") and parts[2] == "repos":
            listed = self.orgs.get(parts[1])
            if listed is None:
                return httpx.Response(404, json={"message": "Not Found"})
            page, per_page = int(request.url.params["page"]), int(request.url.params["per_page"])
            return httpx.Response(200, json=listed[(page - 1) * per_page : page * per_page])
        full_name = f"{parts[1]}/{parts[2]}"
        repo = self.repos.get(full_name)
        if len(parts) == 3:
            return httpx.Response(200, json={"default_branch": repo["branch"]}) if repo else httpx.Response(404)
        assert parts[3:] == ["contents", "package-lock.json"]
        assert request.headers["accept"] == "application/vnd.github.raw+json"
        assert request.url.params.get("ref") == repo["branch"]

        def respond():
            if repo["status"] != 200:
                return httpx.Response(repo["status"], json={"message": "boom"})
            if repo["lockfile"] is None:
                return httpx.Response(404, json={"message": "Not Found"})
            return httpx.Response(200, text=repo["lockfile"])

        return await self._timed("lockfile", respond)


class ReadOnly:
    """Stands in for TrueForge: any use at all fails the test."""

    def __getattr__(self, name):
        raise AssertionError(f"the fleet scan touched TrueForge ({name})")


@pytest.fixture(autouse=True)
def clear_results():
    fleet_router._results.clear()
    yield
    fleet_router._results.clear()


@asynccontextmanager
async def api(world: World):
    app = create_app()
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.MockTransport(world.handler)) as mocked:
            app.state.http_client = mocked
            app.state.trueforge_client = ReadOnly()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=AUTH) as client:
                yield client


def parse_sse(text):
    events, current = [], {}
    for line in text.splitlines() + [""]:
        if not line:
            if "data" in current:
                events.append((current.get("event", "message"), json.loads(current["data"])))
            current = {}
        elif not line.startswith(":"):
            field, _, value = line.partition(":")
            current[field] = value.removeprefix(" ")
    return events


async def scan(client, target):
    response = await client.get("/features/fleet/scan", params={"target": target})
    assert response.status_code == 200, response.text
    events = parse_sse(response.text)
    assert [name for name, _ in events[:-1]] == ["repo"] * (len(events) - 1) and events[-1][0] == "summary"
    return {data["repo"]: data for _, data in events[:-1]}, events[-1][1]


def urls(*names):
    return ",".join(f"https://github.com/{n}" for n in names)


# Lockfiles


def test_parses_all_three_lockfile_versions():
    assert parse_lockfile(v1({"lodash": {"version": "4.17.20"}, "express": {"version": "4.17.1", "dependencies": {"qs": {"version": "6.7.0"}}}, "local": {"version": "file:../local"}})) == {
        ("lodash", "4.17.20"),
        ("express", "4.17.1"),
        ("qs", "6.7.0"),
    }
    packages = {
        "": {"name": "app", "version": "1.0.0"},
        "node_modules/lodash": {"version": "4.17.20"},
        "node_modules/@scope/pkg": {"version": "2.0.0"},
        "node_modules/express/node_modules/qs": {"version": "6.7.0"},
        "node_modules/alias": {"name": "real-name", "version": "1.2.3"},
        "node_modules/linked": {"resolved": "packages/linked", "link": True},
        "node_modules/from-git": {"version": "git+ssh://git@github.com/a/b.git"},
        "packages/linked": {"name": "linked", "version": "0.1.0"},
    }
    expected = {("lodash", "4.17.20"), ("@scope/pkg", "2.0.0"), ("qs", "6.7.0"), ("real-name", "1.2.3")}
    assert parse_lockfile(v3(packages)) == expected
    # Version 2 also carries "dependencies" for old npm; the complete "packages" section wins.
    assert parse_lockfile(v2(packages)) == expected
    assert parse_lockfile(json.dumps({"lockfileVersion": 3})) == set()


@pytest.mark.parametrize("text", ["{not json", "[1, 2]", '{"name": "no sections"}'])
def test_unreadable_lockfiles_raise(text):
    with pytest.raises(LockfileError):
        parse_lockfile(text)


async def test_scans_repos_with_each_lockfile_version():
    world = World()
    world.repo("acme/old", v1({"lodash": {"version": "4.17.20"}}))
    world.repo("acme/mid", v2({"node_modules/minimist": {"version": "1.2.5"}}), branch="develop")
    world.repo("acme/new", v3({"node_modules/@scope/pkg": {"version": "2.0.0"}}))
    world.vulns = {("lodash", "4.17.20"): ["GHSA-lodash"], ("minimist", "1.2.5"): ["GHSA-minimist"]}
    world.details = {"GHSA-lodash": detail("GHSA-lodash", "HIGH"), "GHSA-minimist": detail("GHSA-minimist", "CRITICAL")}

    async with api(world) as client:
        repos, summary = await scan(client, urls("acme/old", "acme/mid", "acme/new"))

    assert {r: (d["status"], d["packages"], d["risk"]) for r, d in repos.items()} == {"acme/old": ("ok", 1, 5), "acme/mid": ("ok", 1, 10), "acme/new": ("ok", 1, 0)}
    assert repos["acme/mid"]["default_branch"] == "develop"
    assert repos["acme/mid"]["top"] == [{"id": "GHSA-minimist", "package": "minimist", "version": "1.2.5", "severity": "critical", "summary": "GHSA-minimist summary"}]
    assert sorted(p for batch in world.batches for p in batch) == [("@scope/pkg", "2.0.0"), ("lodash", "4.17.20"), ("minimist", "1.2.5")]
    assert [r["repo"] for r in summary["repos"]] == ["acme/mid", "acme/old", "acme/new"]


# Repos that cannot be scanned


async def test_no_lockfile_and_errors_are_reported_without_failing_the_scan():
    world = World()
    world.repo("acme/ok", v3({"node_modules/lodash": {"version": "4.17.20"}}))
    world.repo("acme/nolock", None)
    world.repo("acme/broken", "{not json")
    world.repo("acme/down", v3({}), status=500)
    world.vulns = {("lodash", "4.17.20"): ["GHSA-lodash"]}
    world.details = {"GHSA-lodash": detail("GHSA-lodash", "LOW")}

    async with api(world) as client:
        repos, summary = await scan(client, urls("acme/ok", "acme/nolock", "acme/broken", "acme/down", "acme/ghost"))

    assert {r: d["status"] for r, d in repos.items()} == {"acme/ok": "ok", "acme/nolock": "no_lockfile", "acme/broken": "error", "acme/down": "error", "acme/ghost": "error"}
    assert "not valid JSON" in repos["acme/broken"]["error"]
    assert repos["acme/down"]["error"] == "GitHub returned 500 for package-lock.json"
    assert repos["acme/ghost"]["error"] == "Repository not found or not visible to the bot"
    assert repos["acme/nolock"]["error"] is None and repos["acme/nolock"]["risk"] == 0
    assert summary["totals"] | {"counts": None} == {"repos": 5, "ok": 1, "no_lockfile": 1, "error": 3, "counts": None, "risk": 1}


# Dedup and severities


async def test_packages_and_advisories_are_looked_up_once_across_repos():
    world = World()
    shared = {"node_modules/lodash": {"version": "4.17.20"}, "node_modules/minimist": {"version": "1.2.5"}}
    world.repo("acme/a", v3(shared))
    world.repo("acme/b", v3({**shared, "node_modules/qs": {"version": "6.7.0"}}))
    world.repo("acme/c", v1({"lodash": {"version": "4.17.20"}}))
    # One advisory covers two packages; lodash has two advisories.
    world.vulns = {("lodash", "4.17.20"): ["GHSA-a", "GHSA-b"], ("minimist", "1.2.5"): ["GHSA-a"], ("qs", "6.7.0"): ["GHSA-c"]}
    world.details = {i: detail(i, "HIGH") for i in ("GHSA-a", "GHSA-b", "GHSA-c")}
    world.delay = 0.01

    async with api(world) as client:
        repos, summary = await scan(client, urls("acme/a", "acme/b", "acme/c"))

    queried = [p for batch in world.batches for p in batch]
    assert sorted(queried) == [("lodash", "4.17.20"), ("minimist", "1.2.5"), ("qs", "6.7.0")]
    assert world.detail_calls == {"GHSA-a": 1, "GHSA-b": 1, "GHSA-c": 1}
    # Counts are per repo, one per advisory however many of its packages it hits.
    assert repos["acme/a"]["counts"]["high"] == 2 and repos["acme/b"]["counts"]["high"] == 3 and repos["acme/c"]["counts"]["high"] == 2
    assert summary["totals"]["counts"]["high"] == 7


async def test_severity_is_normalised_and_falls_back_to_unknown():
    world = World()
    pairs = {f"node_modules/p{i}": {"version": "1.0.0"} for i in range(6)}
    world.repo("acme/app", v3(pairs))
    world.vulns = {(f"p{i}", "1.0.0"): [f"GHSA-{i}"] for i in range(6)}
    world.details = {
        "GHSA-0": detail("GHSA-0", "CRITICAL"),
        "GHSA-1": detail("GHSA-1", "MODERATE"),
        "GHSA-2": detail("GHSA-2", "Medium"),
        "GHSA-3": detail("GHSA-3"),  # no database_specific at all
        "GHSA-4": {"id": "GHSA-4", "database_specific": {"severity": "spicy"}},
        # GHSA-5 is missing from OSV: 404
    }

    async with api(world) as client:
        repos, _ = await scan(client, urls("acme/app"))

    app = repos["acme/app"]
    assert app["counts"] == {"critical": 1, "high": 0, "moderate": 2, "low": 0, "unknown": 3}
    assert app["risk"] == 10 + 2 + 2
    assert [(a["id"], a["severity"]) for a in app["top"]] == [("GHSA-0", "critical"), ("GHSA-1", "moderate"), ("GHSA-2", "moderate")]


async def test_risk_top_three_and_summary_order():
    world = World()
    world.repo("acme/calm", v3({"node_modules/x": {"version": "1.0.0"}}))
    world.repo("acme/risky", v3({f"node_modules/r{i}": {"version": "1.0.0"} for i in range(5)}))
    world.vulns = {("x", "1.0.0"): ["GHSA-low"], **{(f"r{i}", "1.0.0"): [f"GHSA-r{i}"] for i in range(5)}}
    world.details = {"GHSA-low": detail("GHSA-low", "LOW"), "GHSA-r0": detail("GHSA-r0", "LOW"), "GHSA-r1": detail("GHSA-r1", "HIGH"), "GHSA-r2": detail("GHSA-r2", "CRITICAL"), "GHSA-r3": detail("GHSA-r3", "MODERATE"), "GHSA-r4": detail("GHSA-r4", "HIGH")}

    async with api(world) as client:
        repos, summary = await scan(client, urls("acme/calm", "acme/risky"))

    assert repos["acme/risky"]["risk"] == 1 + 5 + 10 + 2 + 5
    assert [a["id"] for a in repos["acme/risky"]["top"]] == ["GHSA-r2", "GHSA-r1", "GHSA-r4"]
    assert [(r["repo"], r["risk"]) for r in summary["repos"]] == [("acme/risky", 23), ("acme/calm", 1)]


# Limits


def org_listing(owner, count, skipped_every=0):
    listed = []
    for i in range(count):
        entry = {"name": f"r{i:03}", "owner": {"login": owner}, "default_branch": "main", "archived": False, "fork": False}
        if skipped_every and i % skipped_every == 0:
            entry["archived" if i % (2 * skipped_every) == 0 else "fork"] = True
        listed.append(entry)
    return listed


async def test_org_scan_skips_archived_and_forks_and_stops_at_the_cap(monkeypatch):
    world = World()
    world.orgs["acme"] = org_listing("acme", 130, skipped_every=5)
    for entry in world.orgs["acme"]:
        world.repo(f"acme/{entry['name']}", None)

    async with api(world) as client:
        repos, summary = await scan(client, "org:acme")
    assert len(repos) == 25 and summary["totals"]["repos"] == 25
    kept = [e["name"] for e in world.orgs["acme"] if not (e["archived"] or e["fork"])]
    assert sorted(r.split("/")[1] for r in repos) == kept[:25]
    assert sum("/orgs/acme/repos" in c for c in world.calls) == 1

    monkeypatch.setenv("FLEET_MAX_REPOS", "90")
    from app.config import get_settings

    get_settings.cache_clear()
    fleet_router._results.clear()  # a new cap only takes effect on restart, which also empties the cache
    world.calls.clear()
    async with api(world) as client:
        repos, _ = await scan(client, "org:acme")
    assert len(repos) == 90 and not any(r.split("/")[1] in {e["name"] for e in world.orgs["acme"] if e["archived"] or e["fork"]} for r in repos)
    assert sum("/orgs/acme/repos" in c for c in world.calls) == 2  # the second page was needed


async def test_user_targets_and_the_cap_on_repo_lists(monkeypatch):
    monkeypatch.setenv("FLEET_MAX_REPOS", "2")
    world = World()
    world.orgs["octocat"] = org_listing("octocat", 3)
    for name in ("octocat/r000", "octocat/r001", "a/one", "a/two", "a/three"):
        world.repo(name, None)
    async with api(world) as client:
        user_repos, _ = await scan(client, "user:octocat")
        listed_repos, _ = await scan(client, urls("a/one", "a/two", "a/three"))
    assert sorted(user_repos) == ["octocat/r000", "octocat/r001"]
    assert any("/users/octocat/repos" in c for c in world.calls)
    assert sorted(listed_repos) == ["a/one", "a/two"]


async def test_osv_queries_are_chunked_at_1000():
    world = World()
    world.repo("acme/big", v3({f"node_modules/pkg{i}": {"version": "1.0.0"} for i in range(1500)}))
    async with api(world) as client:
        repos, _ = await scan(client, urls("acme/big"))
    assert repos["acme/big"]["packages"] == 1500
    assert [len(batch) for batch in world.batches] == [1000, 500]


async def test_at_most_five_repos_and_ten_advisory_reads_at_once():
    world = World()
    world.delay = 0.02
    for i in range(12):
        world.repo(f"acme/r{i}", v3({f"node_modules/p{i}_{j}": {"version": "1.0.0"} for j in range(4)}))
    world.vulns = {(f"p{i}_{j}", "1.0.0"): [f"GHSA-{i}-{j}"] for i in range(12) for j in range(4)}
    world.details = {f"GHSA-{i}-{j}": detail(f"GHSA-{i}-{j}", "LOW") for i in range(12) for j in range(4)}
    async with api(world) as client:
        repos, _ = await scan(client, urls(*(f"acme/r{i}" for i in range(12))))
    assert len(repos) == 12 and all(r["status"] == "ok" for r in repos.values())
    assert 1 < world.peak["lockfile"] <= 5
    assert 1 < world.peak["detail"] <= 10


# Cache and requests


async def test_results_are_cached_per_target_for_sixty_seconds(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(fleet_router, "monotonic", lambda: clock[0])
    world = World()
    world.repo("acme/a", v3({"node_modules/lodash": {"version": "4.17.20"}}))
    world.vulns = {("lodash", "4.17.20"): ["GHSA-lodash"]}
    world.details = {"GHSA-lodash": detail("GHSA-lodash", "HIGH")}

    async with api(world) as client:
        first = await scan(client, urls("acme/a"))
        calls = len(world.calls)
        clock[0] += 59
        assert await scan(client, "https://github.com/ACME/a") == first
        assert len(world.calls) == calls
        clock[0] += 2
        await scan(client, urls("acme/a"))
        assert len(world.calls) > calls


async def test_bad_targets_unknown_orgs_and_missing_keys():
    async with api(World()) as client:
        bad = await client.get("/features/fleet/scan", params={"target": "team:acme"})
        unknown = await client.get("/features/fleet/scan", params={"target": "org:nobody"})
        keyless = await client.get("/features/fleet/scan", params={"target": "org:acme"}, headers={"Authorization": "Bearer nope"})
    assert bad.status_code == 400 and "org:<name>" in bad.json()["detail"]
    assert unknown.status_code == 404 and "not found" in unknown.json()["detail"]
    assert keyless.status_code == 401
