# Handoff from feat/receipt

Changes this branch needs in files it does not own. Nothing below is blocking the receipt; each item makes it work against a real TrueFoundry account.

## backend/.env.example (frozen)

`TFY_GATEWAY_BASE_URL` must be the TrueFoundry **control plane** URL, not the inference host `https://gateway.truefoundry.ai`: the receipt calls `POST {TFY_GATEWAY_BASE_URL}/api/svc/v1/spans/query` (see `backend/docs/gateway_notes.md`, section 2). Suggested comment above the line:

```
# TrueFoundry control plane URL (the logs API lives there), e.g. https://<your-org>.truefoundry.cloud
TFY_GATEWAY_BASE_URL=
```

## backend/app/config.py (frozen), only if needed

The spans query sends `"dataRoutingDestination": "default"`. If the account stores traces in a named tracing project instead, the query needs `tracingProjectFqn` (shown under "Fetch via API" on the Request Logs page). That would need one optional setting:

```python
tfy_tracing_project_fqn: str | None = None
```

added to the `_empty_is_default` validator list, plus `TFY_TRACING_PROJECT_FQN=` in `.env.example`. `app/gateway.py` would then send `tracingProjectFqn` instead of `dataRoutingDestination` when it is set.

## Not a file change, but required for any gateway data

The local TrueForge `greenlight` model provider points at BytePlus directly, so the gateway logs nothing for Greenlight runs. Until it points at the TrueFoundry gateway, every receipt settles with `"source": "events"` and null tokens and cost.

`trace_url` stays null until a real trace or Requests URL is copied from the TrueFoundry UI and its format is added to `gateway_notes.md`. `TFY_TRACE_BASE_URL` is unused for that reason.
