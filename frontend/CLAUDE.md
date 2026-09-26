@AGENTS.md

## Parallel work ownership
Paths are relative to backend/ for Python files and frontend/ for Next.js files.
Branch feat/receipt owns: app/gateway.py, app/receipt.py, app/routes/receipt.py, tests/test_receipt*.py, components/run/Receipt*.tsx, app/api/receipt/**.
Branch feat/flight-recorder owns: app/chain.py, app/routes/audit.py, the internals of app/ledger.py, tests/test_chain*.py, tests/test_audit*.py, scripts/tamper_demo.py, scripts/verify_export.py, components/run/Audit*.tsx, app/api/audit/**.
Frozen for both branches: app/main.py, app/config.py, .env.example, app/event_types.py, the public function signatures in app/ledger.py, the reducer, page layouts.
If you need a change in a frozen file or a file owned by the other branch, do not edit it. Write the exact change you need in HANDOFF.md on your branch and continue.
