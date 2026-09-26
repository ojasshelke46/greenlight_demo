# Greenlight

Greenlight is an agent that takes a vulnerable dependency all the way to a safe, human approved release. It scans a GitHub repo, then fixes one vulnerable package at a time: it upgrades the package in a sandbox, runs the tests, repairs any code the upgrade breaks, and opens one pull request per package. Merging is the one irreversible step (merging to `main` publishes the package), so Greenlight always stops and waits for a person to approve the merge.

Built at the TrueFoundry x Polaris "Agents That Act" hackathon.

Every state on screen comes from a real agent event. Nothing is simulated, and every event and approval is written to a tamper evident, hash chained ledger.

## How it works

```
Browser (Next.js, frontend/)
   |  /api/* routes add the API key server side
   v
Greenlight backend (FastAPI, backend/)
   |  runs, campaigns, approvals, ledger, receipts
   v
TrueForge (open source agent harness, running locally)
   |  six agents, GitHub remote MCP server, Daytona sandbox
   v
GitHub  +  OSV  +  npm
```

1. **Scan.** You paste a repo link. Greenlight starts a campaign whose first run is `TASK: scan`: clone, install, baseline tests, `npm audit`, OSV. It changes nothing and reports every vulnerable package as a fact.
2. **Queue.** The backend orders the packages by severity (critical, high, moderate, low), then by how many advisories each has.
3. **Fix one.** "Fix next" starts a `TASK: fix <package>` run with its own session and sandbox. It upgrades only that package, repairs code, runs the full test suite, and publishes on a branch named `greenlight/<package>-<short advisory id>`. If the bot cannot push to the repo, it forks and opens the PR from the fork.
4. **Prove.** When the PR opens, an independent prover agent reproduces each advisory before the fix and checks it is gone after.
5. **Approve.** In ship mode the agent asks to merge, and the chat shows a hold to approve card. Before anything merges, the backend checks the PR, the repo's `.greenlight.yml` rules and the prover's verdict.
6. **Next.** A card offers the next package. "Fix all, one PR at a time" moves on automatically after each PR, with a 5 second window to stop, and it stops at the first failure.

## The agents

Six TrueForge agents, resolved by name at startup (`GET /agents`, `/health`):

| Role | Agent | Started by | What it does |
| --- | --- | --- | --- |
| Fix | `greenlight` | you | Scan mode and fix one package mode, publishing and merge (merge needs approval) |
| Scout | `greenlight-scout` | you | Ranks many repos by risk, read only |
| Policy | `greenlight-policy` | you | Drafts a `.greenlight.yml` for a repo and opens one PR with it |
| Prover | `greenlight-prover` | Greenlight | Independently proves each fix closes its advisory |
| Receipt | `greenlight-receipt` | Greenlight | Writes the cost breakdown under each run's receipt |
| Auditor | `greenlight-auditor` | Greenlight | Turns a run's ledger export into an incident style report; never writes |

Agent instructions and tool settings live in TrueForge. Backups of each change are in `backend/docs/agent_backups/`.

## Safety

- **Merge needs a person.** `merge_pull_request` is the only tool that requires approval. The approval endpoint revalidates the PR on GitHub, and a PR only or fork run can never merge.
- **Repo rules.** A `.greenlight.yml` in the target repo sets approvers, how many approvals a merge needs, whether major upgrades are allowed, and freeze windows.
- **Independent proof.** A prover that still reproduces the advisory after the fix blocks the merge.
- **Flight recorder.** Every run, event, approval and note is hash chained in SQLite and can be signed with an HMAC key. `GET /runs/{id}/audit/verify` rechecks the chain, and the export can be verified offline with `backend/scripts/verify_export.py`.
- **No secrets in the browser.** The frontend only calls its own `/api` routes; the API key, GitHub token and gateway key stay in server side env files.

## Repository layout

| Path | What |
| --- | --- |
| `frontend/` | Next.js chat UI (composer, conversation, campaigns, fleet, policy, audit drawer) |
| `backend/` | FastAPI service: TrueForge client, run pump, campaigns, approvals, ledger, receipts |
| `backend/tests/` | pytest suite |
| `backend/scripts/` | `e2e.py`, `latency_check.py`, `smoke_trueforge.py`, `tamper_demo.py`, `verify_export.py` |
| `src/`, `test/` | The demo target library (see below) |
| `DESIGN.md`, `PRODUCT.md` | Design system and product notes |

## Running it locally

You need Python 3.12 with [uv](https://docs.astral.sh/uv/), Node 20 or newer, and a local TrueForge with the six agents above, the GitHub remote MCP server connected, and a Daytona sandbox provider.

### 1. TrueForge

Run TrueForge locally (default `http://localhost:8790`) and create the six agents by name. `GET http://localhost:8000/agents` shows which ones the backend found.

### 2. Backend

```sh
cd backend
cp .env.example .env     # then fill it in
uv sync
uv run dev               # http://localhost:8000, reloads on change
```

| Variable | Purpose |
| --- | --- |
| `TRUEFORGE_BASE_URL` | TrueForge API, e.g. `http://localhost:8790` |
| `TRUEFORGE_AGENT_NAME` | The fix agent's name, `greenlight` |
| `GREENLIGHT_API_KEY` | Shared secret the frontend sends to the backend |
| `FRONTEND_ORIGIN` | The frontend's origin, for CORS |
| `GITHUB_BOT_TOKEN`, `GITHUB_BOT_LOGIN` | The bot account Greenlight checks access, branches and forks with |
| `LEDGER_PATH` | SQLite file for the ledger, e.g. `greenlight.db` |
| `LEDGER_HMAC_KEY` | Optional; signs the head of every run's chain |
| `TFY_GATEWAY_BASE_URL`, `TFY_GATEWAY_API_KEY` | Optional; TrueFoundry control plane, for exact token counts and cost in receipts |
| `FLEET_MAX_REPOS`, `POLICY_FILE_PATH`, `INR_PER_USD`, `TFY_TRACE_BASE_URL` | Optional tuning |

Without the gateway settings, receipts count model calls from the ledger and show cost as unavailable. A cost is never estimated.

### 3. Frontend

```sh
cd frontend
cp .env.example .env.local   # BACKEND_URL, GREENLIGHT_API_KEY, NEXT_PUBLIC_USER_NAME, NEXT_PUBLIC_DEMO_REPO
npm install
npm run dev                  # http://localhost:3000
```

Pick an agent in the composer (Fix, Scout, Policy), paste a repo link (or `org:name`, `user:name` for Scout), and send. With a run open, plain text such as "continue" goes to that run's agent, and Try again picks up where a stopped run left off.

## Backend API, in short

All routes need `Authorization: Bearer $GREENLIGHT_API_KEY`.

| Route | Purpose |
| --- | --- |
| `POST /campaigns` | Start a repo campaign `{repo, mode, auto}`: a scan, then one fix at a time |
| `GET /campaigns/{id}` | Queue with each package's status, run, PR and what is next |
| `POST /campaigns/{id}/next`, `.../items/{package}/fix`, `.../items/{package}/skip` | Fix the next or a chosen package, or skip one (one fix at a time; 409 while one runs) |
| `POST /campaigns/{id}/auto`, `/stop` | Turn "fix all" on or off, or stop after the current fix |
| `POST /campaigns/fleet`, `GET /campaigns/fleet/{id}/events` | Scout repos one at a time (streamed), then `/next` starts the next repo's campaign |
| `POST /campaigns/policy` | Draft `.greenlight.yml` for each repo, one PR at a time |
| `POST /runs`, `GET /runs`, `GET /runs/{id}`, `GET /runs/{id}/events` | Single runs and their live event stream (SSE, resumable with `Last-Event-ID`) |
| `POST /runs/{id}/message`, `/resume`, `/pause` | Talk to a run's agent, continue it in the same session, or pause it |
| `POST /runs/{id}/approval` | Approve or reject the pending merge |
| `GET /runs/{id}/children` | Helper agent runs (provers, receipt, auditor) |
| `GET /runs/{id}/receipt`, `/audit/verify`, `/audit/export`, `POST /audit/report` | Receipt, chain verification, export, auditor report |
| `GET /agents`, `GET /health` | Agents and their models, TrueForge reachability |

## Tests

```sh
cd backend && uv run pytest        # unit and integration tests, TrueForge faked
cd frontend && npm run build && npx eslint .
```

Live checks against a real TrueForge (they open real PRs, never merge):

```sh
cd backend
uv run python scripts/e2e.py                       # a pr_only fix run on this repo
uv run python scripts/latency_check.py --run-id <id>
```

## The demo target

This repository is also the target Greenlight fixes in the demo. It is not a real library; do not depend on it.

A tiny CommonJS library with three functions:

| Function | File | Dependency |
| --- | --- | --- |
| `slugify(text)` | `src/slugify.js` | none |
| `formatBytes(bytes)` | `src/formatBytes.js` | none |
| `checkUrl(url)` | `src/checkUrl.js` | `node-fetch` |

Tests use the built in `node:test` runner. The `checkUrl` test starts a local HTTP server and never touches the internet.

```sh
npm ci
npm test
```

**The planted vulnerability.** `node-fetch` is pinned to `2.6.0`, which is affected by [GHSA-r683-j2x4-v87g](https://osv.dev/vulnerability/GHSA-r683-j2x4-v87g): secure headers such as `authorization` and `cookie` are forwarded when a request redirects to an untrusted site. The clean upgrade is `node-fetch` 3.x, which ships as an ES module only, so `require('node-fetch')` stops working and exactly one test fails. The expected fix loads it with a dynamic `import()` inside `checkUrl`.

**Releases.** `.github/workflows/release.yml` runs on every push to `main`: install, test, then `npm publish --access public` with the `NPM_TOKEN` secret. That is what makes merging the one irreversible step. npm refuses to publish a version twice, so a PR has to bump `version` in `package.json` for its merge to publish.

**Resetting between rehearsals.**

```sh
./reset_demo.sh        # asks for confirmation
./reset_demo.sh --yes  # no prompt
```

It restores the code from the `vulnerable_snapshot` tag, sets `version` to the latest version on npm, commits with `[skip ci]`, force pushes `main`, deletes remote `greenlight/` branches and closes open PRs. It needs `git`, `gh` (logged in), `npm` and `node`, and refuses to run with uncommitted changes.

## Known limits

- The Daytona sandbox image ships without Node.js; the fix agent installs it at the start of each run.
- Receipts show cost only when the TrueFoundry gateway settings are configured.
- The release workflow needs an npm token that can publish without two factor prompts.
