We are building Greenlight at the TrueFoundry x Polaris "Agents That Act" hackathon (7 hour build).

Greenlight is an agent that finds a vulnerable dependency in a GitHub repo, upgrades it inside a sandbox, runs the tests, fixes code if the upgrade breaks something, opens a PR, and then pauses for a human to approve the merge. Merging to main triggers a GitHub Action that publishes the package, so the merge is the one irreversible step.

The agent runs on TrueForge (open source agent harness, running locally). It is configured inside TrueForge: model is OpenAI, GitHub is connected through GitHub's remote MCP server, the sandbox is Daytona. merge_pull_request requires human approval. Delete and workflow tools are disabled.

This repo is the Greenlight web app: a custom UI that drives the TrueForge agent through the TrueForge TypeScript SDK and visualises every event live. The UI never fakes agent behaviour. Every state on screen must come from a real TrueForge event.

Rules:
1. Never guess SDK method names. Read the types in node_modules/@truefoundry/trueforge-sdk and the snippet from the agent's "Use in Code" tab before writing any SDK call.
2. Never put API keys or tokens in client code, the repo, or logs. Server side env vars only, .env.local is gitignored.
3. Keep scope tight. Working beats pretty. Finish each step before starting the next.
4. No hyphens in any user facing copy.

## Parallel work ownership
Paths are relative to backend/ for Python files and frontend/ for Next.js files.
Branch feat/receipt owns: app/gateway.py, app/receipt.py, app/routes/receipt.py, tests/test_receipt*.py, components/run/Receipt*.tsx, app/api/receipt/**.
Branch feat/flight-recorder owns: app/chain.py, app/routes/audit.py, the internals of app/ledger.py, tests/test_chain*.py, tests/test_audit*.py, scripts/tamper_demo.py, scripts/verify_export.py, components/run/Audit*.tsx, app/api/audit/**.
Frozen for both branches: app/main.py, app/config.py, .env.example, app/event_types.py, the public function signatures in app/ledger.py, the reducer, page layouts.
If you need a change in a frozen file or a file owned by the other branch, do not edit it. Write the exact change you need in HANDOFF.md on your branch and continue.
