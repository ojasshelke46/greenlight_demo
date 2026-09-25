# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

Next.js frontend using the TrueForge TypeScript SDK (`@truefoundry/trueforge-sdk`), next to the existing Python FastAPI backend in `backend/`.

Binding split, confirmed by the user:

- Next.js may use the SDK server side to read sessions and stream turn events.
- Every approve or reject decision goes through the FastAPI backend, `POST /runs/{id}/approval`. The Next.js app never resumes a TrueForge turn with an approval itself, because the server side merge checks and the ledger live only in the backend.
- API keys and tokens stay in server side env vars (`.env.local`, gitignored). Nothing secret in client code.

Backend endpoints the UI builds on: `GET /access?repo=`, `POST /runs`, `GET /runs/{id}`, `GET /runs/{id}/events` (SSE, resumable with `Last-Event-ID`), `GET /runs/{id}/ledger`, `POST /runs/{id}/approval`, `GET /runs/{id}/release`.

## Users

Developers and hackathon judges watching a live demo on a projector from 3 to 10 metres away. They must understand what the agent is doing within 2 seconds of looking. The person driving the demo is a developer who pastes a repo link, watches the run, and makes the merge decision.

## Product Purpose

Greenlight is a live control room for an AI agent that takes any GitHub repo link, fixes vulnerable dependencies in a sandbox until tests pass, and opens a PR. On repos where it is a collaborator, it waits for human approval before merging.

The UI exists to:

- make every agent step visible as it happens;
- make the access level obvious before a run starts (ship, or PR only via a fork);
- make the approval moment feel deliberate and weighty.

Success: a viewer across the room can follow the run from repo link to vulnerability to fix to PR to approval to release, and trusts that the merge was safe.

## Positioning

The agent really acts, and the UI shows only what it really did. Every state on screen is a real TrueForge event, and the one irreversible step (merge to `main`, which publishes to npm) is gated by server side checks a human can see before turning the key.

## Operating Context

- Built at the TrueForge x Polaris "Agents That Act" hackathon, 7 hour build.
- The agent runs on TrueForge (open source agent harness, running locally), with GitHub through GitHub's remote MCP server and a Daytona sandbox.
- Demo target: this repo's tiny npm library, with `node-fetch` pinned to 2.6.0 (GHSA-r683-j2x4-v87g). Upgrading to 3.x breaks one test, which the agent must fix.
- Merging to `main` runs `.github/workflows/release.yml`, which tests and runs `npm publish`. That merge is the one irreversible step.
- `reset_demo.sh` restores the vulnerable state between rehearsals.
- Run modes: `ship` (bot can push; may merge after approval) and `pr_only` (bot cannot push; works through a fork and never merges).

## Capabilities and Constraints

- Live event stream per run: `turn.created`, `model.message` (filled in by `model.message.delta`), `tool.response`, `tool.approval_required`, `sandbox.created`, `mcp.initialize`, `turn.done`, and others. Sequence numbers are monotonic per run, including across the resume after approval.
- Server side approval checks, each refusal returned with a readable reason: exactly one pending action; the tool is GitHub `merge_pull_request`; run is ship mode and not via a fork; PR is in the run's repo, open, targets the default branch, has a `greenlight/` head branch from the repo owner; the bot can still push; one decision per run.
- Rejecting is always allowed on a paused run, including `pr_only` runs, so an agent that asks to merge when it should not can be denied.
- Append only ledger of every event and every approval attempt, including refused ones.
- Release status after merge: GitHub Actions run for the merge commit, with status, conclusion and link.
- TrueForge cancels turns after `SERVER_EXECUTION_TIMEOUT_SECONDS` (default 600). A full run needs this raised; the UI must show a cancelled turn honestly.
- No hyphens in any user facing copy.

The UI will show all four of: every live agent event, why the merge is safe (the checks beside the approve control), the audit ledger, and the release after merge. The user delegated this choice.

## Brand Commitments

User stated, binding:

- Name: Greenlight.
- Dark, desktop web, projector friendly.
- Colour carries meaning and nothing else: red is vulnerable or blocked, amber is the agent working, green is tests passing or approved. These colours are never used for anything else.

## Evidence on Hand

- Real agent runs against `ojasshelke46/greenlight_demo` through local TrueForge, with real event streams and GitHub side effects (branches such as `greenlight/node-fetch` and `greenlight/upgrade-node-fetch` on the `greenlight-agent` fork).
- The advisory GHSA-r683-j2x4-v87g and the real failing test after the upgrade.
- No testimonials, users, metrics, or press exist. Do not invent them.

## Product Principles

1. Every state on screen comes from a real agent event. Nothing is animated ahead of the agent or faked.
2. Colour means something. Red, amber and green are reserved for their states.
3. One focal point at a time.
4. The human approval is the climax. It should feel like turning a launch key.
5. Show why it is safe, not just that it is: the gate's checks and refusals are visible, and the ledger backs them.
