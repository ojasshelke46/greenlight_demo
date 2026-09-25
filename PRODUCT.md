# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

Next.js frontend in `frontend/`, next to the Python FastAPI backend in `backend/`.

Binding, confirmed by the user:

- The frontend never calls TrueForge or GitHub. Its server side route handlers (`frontend/app/api/*`) proxy to the backend and attach `Authorization: Bearer ${GREENLIGHT_API_KEY}` from server env.
- Every approve or reject decision goes through the backend, `POST /runs/{id}/approval`, where the merge checks and the ledger live.
- API keys and tokens stay in server side env vars (`.env.local`, gitignored). Nothing secret in client code.

Backend endpoints the UI builds on: `GET /access?repo=`, `POST /runs`, `GET /runs`, `GET /runs/{id}`, `GET /runs/{id}/events` (SSE, resumable with `Last-Event-ID`), `GET /runs/{id}/ledger`, `POST /runs/{id}/approval`, `GET /runs/{id}/release`, `GET /health`.

## Users

Developers and hackathon judges watching a live demo. They must understand what the agent is doing within seconds of looking. The person driving the demo is a developer who pastes a repo link into a chat style composer, watches the agent answer with what it actually did, and makes the merge decision.

## Product Purpose

Greenlight is a chat first interface for an AI agent that takes any GitHub repo link, fixes vulnerable dependencies in a sandbox until tests pass, and opens a PR. On repos where it is a collaborator, it waits for human approval before merging.

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
- Chat first agent interface, dark, modelled on a premium AI agent app. No dashboard grid.
- One accent colour, electric lime, defined once as `--accent` so it can be swapped in one line.
- Status colours appear only inside agent output: red for vulnerable or failing, amber for in progress, the accent for passing or done.
- No traffic lights, lamps, or signal metaphors anywhere in code, copy, or assets.
- The visual system is recorded in DESIGN.md.

## Evidence on Hand

- Real agent runs against `ojasshelke46/greenlight_demo` through local TrueForge, with real event streams and GitHub side effects (branches such as `greenlight/node-fetch` and `greenlight/upgrade-node-fetch` on the `greenlight-agent` fork).
- The advisory GHSA-r683-j2x4-v87g and the real failing test after the upgrade.
- No testimonials, users, metrics, or press exist. Do not invent them.

## Product Principles

1. Every state on screen comes from a real agent event. Nothing appears before the event that proves it.
2. Status colour lives only inside agent output, and always means the same thing.
3. One focal point at a time: when approval is due, the rest of the conversation steps back.
4. The human approval is the climax. It takes a deliberate two second hold, never a stray click.
5. Show why it is safe, not just that it is: the policy, the approval card's facts, and the ledger back every merge.
