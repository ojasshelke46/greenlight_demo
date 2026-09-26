# TrueFoundry AI Gateway: request logs, and correlating them with TrueForge runs

Discovery notes for `feat/receipt`. Every name below is quoted from a doc page, the TrueForge OpenAPI spec, the TrueForge source, or a real TrueForge response. Anything not confirmed is marked **unconfirmed**.

Checked on 2026-09-26 against TrueForge `0.2.1` (`backend/docs/trueforge_openapi.json`, and the `npx trueforge` build running locally).

## 1. Status of the live check

`TFY_GATEWAY_BASE_URL` and `TFY_GATEWAY_API_KEY` are not set in the shell, `backend/.env` or `frontend/.env.local`, so the logs API has **not** been called and there is no `gateway_sample.json` yet. The HTTP response field names in section 3 therefore come from docs, not from a real response.

The local TrueForge does not use the TrueFoundry gateway at all today. Its only model provider (`GET /api/v1/settings/model-providers`) is:

| name | type | base_url | models |
| --- | --- | --- | --- |
| `greenlight` | `custom` | `https://ark.ap-southeast.bytepluses.com/api/v3` | `deepseek-v-4-pro-ga-260813` |

Until TrueForge's model calls go through the gateway, the gateway has no Greenlight traffic to report.

## 2. Two different base URLs

| Purpose | URL | Source |
| --- | --- | --- |
| Inference (model calls) | `https://gateway.truefoundry.ai` (e.g. `https://gateway.truefoundry.ai/openai/chat/completions`) | [Request Logging](https://www.truefoundry.com/docs/ai-gateway/request-logging) |
| Logs / traces query | `https://{control_plane_url}/api/svc/v1/spans/query` | [API Access to Logs](https://www.truefoundry.com/docs/ai-gateway/fetch-request-logs) |
| Aggregated metrics | `https://{your_control_plane_url}/api/svc/v1/llm-gateway/metrics/query` | [Agent Metrics](https://www.truefoundry.com/docs/ai-gateway/fetch-agent-metrics) |

The logs API lives on the **control plane**, not the inference host. `TFY_GATEWAY_BASE_URL` must be the control plane URL for the spans query to work; if it is set to the inference host, a second setting is needed.

Auth for both: `Authorization: Bearer <PAT or VAT>`, `Content-Type: application/json`.

## 3. Query spans API

`POST https://{control_plane_url}/api/svc/v1/spans/query`

Docs: [overview](https://www.truefoundry.com/docs/ai-gateway/fetch-request-logs), [filtering](https://www.truefoundry.com/docs/ai-gateway/fetch-request-logs-filtering), [basic queries](https://www.truefoundry.com/docs/ai-gateway/fetch-request-logs-use-cases), [advanced queries](https://www.truefoundry.com/docs/ai-gateway/fetch-request-logs-use-cases-advanced), [trace inspection](https://www.truefoundry.com/docs/ai-gateway/fetch-request-logs-trace-inspection), [SDK `query_spans`](https://www.truefoundry.com/docs/truefoundry_sdk/traces), [SDK types](https://www.truefoundry.com/docs/truefoundry_sdk/types), [SDK enums](https://www.truefoundry.com/docs/truefoundry_sdk/enums).

### Request body (HTTP names, as used in the docs' HTTP examples)

| Field | Notes |
| --- | --- |
| `startTime` | Required. ISO 8601, e.g. `"2025-10-08T00:00:00.000Z"` |
| `endTime` | Optional. Defaults to now |
| `dataRoutingDestination` | e.g. `"default"`. One of this or `tracingProjectFqn` is required |
| `tracingProjectFqn` | e.g. `truefoundry:tracing-project:tfy-default`. Shown under "Fetch via API" on the Request Logs page |
| `traceIds` | Array of trace ids |
| `limit` | Per page, default 200 |
| `sortDirection` | `"asc"` or `"desc"` (default desc) |
| `pageToken` | From previous response `pagination.nextPageToken` |
| `filters` | Array, AND combined, see below |

SDK only parameters whose HTTP names do not appear in any doc HTTP example (**unconfirmed** camelCase): `span_ids`, `parent_span_ids`, `created_by_subject_types`, `created_by_subject_slugs`, `application_names`, `include_feedbacks`.

### Filters (HTTP shapes from the docs)

```json
{"spanFieldName": "createdBySubjectSlug", "operator": "EQUAL", "value": "user@example.com"}
{"spanAttributeKey": "tfy.model.name", "operator": "STRING_CONTAINS", "value": "openai"}
{"gatewayRequestMetadataKey": "foo", "operator": "IN", "value": ["bar1", "bar2"]}
```

`spanFieldName` values: `spanName`, `spanId`, `traceId`, `parentSpanId`, `applicationName`, `createdBySubjectSlug`, `createdBySubjectType`, `durationInNs`, `durationInMs`, `spanStatusCode`, `tag`, `scopeName`, `environment`, `feedbacks`.

Operators (same set for all three filter kinds): `EQUAL`, `GREATER_THAN`, `LESS_THAN`, `GREATER_THAN_EQUAL`, `LESS_THAN_EQUAL`, `BETWEEN`, `IN`, `NOT_IN`, `STRING_CONTAINS`, `STRING_STARTS_WITH`, `STRING_ENDS_WITH`, `ARRAY_HAS_ANY`, `ARRAY_HAS_NONE`, `PRESENT`, `IS_NULL`.

Model spans only: `{"spanAttributeKey": "tfy.span_type", "operator": "EQUAL", "value": "Model"}`.

### Response

Confirmed by HTTP examples: `data` (array of spans), `pagination.nextPageToken`, and on each span `spanName`, `spanAttributes`.

Span fields from the SDK `TraceSpan` type (snake_case SDK names; HTTP names are **unconfirmed**): `span_id`, `trace_id`, `parent_span_id`, `service_name`, `span_name`, `span_kind`, `scope_name`, `scope_version`, `timestamp`, `duration_ns`, `status_code`, `status_message`, `span_attributes`, `events`, `created_by_subject`, `feedbacks`.

Conflict to settle with a real response: the trace-ID HTTP example reads `span['duration']`, while the SDK type is `duration_ns`.

### Span attributes for a receipt

From [Span attributes](https://www.truefoundry.com/docs/ai-gateway/fetch-request-logs-span-attributes):

| Need | Attribute key |
| --- | --- |
| Span type (`Model` = actual inference) | `tfy.span_type` |
| Model requested | `tfy.request.model_name` |
| Model display name / fqn / id | `tfy.model.name`, `tfy.model.fqn`, `tfy.model.id` |
| Input tokens | `tfy.model.metric.input_tokens` |
| Output tokens | `tfy.model.metric.output_tokens` |
| Cache tokens | `tfy.model.metric.cache_read_input_tokens`, `tfy.model.metric.cache_creation_input_tokens` |
| Cost | `tfy.model.metric.cost_in_usd` |
| Latency | `tfy.model.metric.latency_in_ms` (also `tfy.model.metric.time_to_first_token_in_ms`) |
| Request metadata | `tfy.request.metadata` |
| Conversation id | `tfy.request.conversation_id` |
| Caller | `tfy.request.created_by_subject` |
| Raw request / response | `tfy.input`, `tfy.output` (redact before storing) |
| HTTP status | `http.response.status_code` |

The `ChatCompletion` span is the request root; the `Model: <model>` span beneath it is the inference.

## 4. Metadata header

From [Custom Metadata and Headers](https://www.truefoundry.com/docs/ai-gateway/request-headers): request header `X-TFY-METADATA`, a stringified JSON object whose keys and values are strings, each value at most 128 characters. It is stored with the request, appears as `tfy.request.metadata`, and is filterable with `gatewayRequestMetadataKey`.

Response headers the gateway returns: `x-tfy-resolved-model`, `x-tfy-applied-configurations`, `server-timing`. The docs list no response header carrying a trace or request id.

## 5. UI links

The docs give only navigation: `AI Gateway` > `Monitor` > `Requests` ([Request Logging](https://www.truefoundry.com/docs/ai-gateway/request-logging)). No documented URL format for one trace or for a filtered view. Get it by opening a trace in the UI and copying the address bar; do not build it from a guess.

## 6. What TrueForge records

From `trueforge_openapi.json`, and confirmed on a real local session (`GET /api/v1/sessions/{id}/events`):

- `model.message` (schema `ModelMessageEvent`) has `id` (TrueForge ULID), `thread_id`, `created_at`, `finish_reason`, `tool_calls`, and `usage`.
- `usage` (schema `ModelMessageUsage`): `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_write_tokens`, `input_tokens_breakdown`. Real example: `{"input_tokens": 2205, "output_tokens": 177, "cache_read_tokens": 0, "input_tokens_breakdown": {...}}`.
- **No model name, no cost, no gateway or provider request id** on `model.message`.
- `turn.done` `state.metrics` (schema `TurnMetrics`): `total_input_tokens`, `total_output_tokens`, `total_tokens`, `total_cache_read_tokens`, `total_cache_write_tokens`, `total_reasoning_tokens`, `total_cost_in_usd` (optional). The real sample had token totals but no cost.
- Session `metrics` (schema `SessionMetrics`): `total_cost_in_usd` (optional), `total_duration_ms`, `total_turns`.
- The model name is only on the agent spec (`agent.spec.model.name`, e.g. `greenlight/deepseek-v-4-pro-ga-260813`).

### TrueForge already tags gateway calls, in TrueFoundry mode only

TrueForge source (`src/runtime/sessionResources.ts` in the `@truefoundry/trueforge` build) sends `x-tfy-metadata` on every model call and every MCP call with:

| Key | Value |
| --- | --- |
| `tfg.session_id` | TrueForge session id |
| `tfg.turn_id` | TrueForge turn id |
| `tfg.agent_id` | agent id (named agents only) |
| `tfg.agent_name` | agent name (named agents only) |

It also merges in any `x-tfy-metadata` header the caller sends on `POST /api/v1/sessions/{id}/turns`, so Greenlight could add its own keys (e.g. a run id) per turn.

This only happens when `isTrueFoundryModeEnabled()` is true: not `STANDALONE`, and `TRUEFOUNDRY_SERVICEFOUNDRY_SERVER_URL` set. The local `npx trueforge` runs standalone (SQLite under `~/Library/Application Support/trueforge`), so today it sends no metadata. Provider configs cannot add headers either: model provider `auth` only has `api_key`, and the code builds provider `headers: {}`.

## 7. Correlation options

| Option | Works? | Risk |
| --- | --- | --- |
| a) shared request or trace id | **No.** TrueForge stores only its own ULIDs. The gateway keeps the provider's response id inside `tfy.output`, but TrueForge never records it | None to weigh; there is no shared key |
| b) metadata per session | **Only in TrueFoundry mode**, where it is built in: filter `gatewayRequestMetadataKey` `tfg.session_id` `EQUAL` the run's session id | Needs TrueForge hosted on TrueFoundry, or self hosted non standalone with `TRUEFOUNDRY_SERVICEFOUNDRY_SERVER_URL` (Postgres, TrueFoundry auth). Too heavy for the local demo; exact and cheap if it is available |
| c) time window + model | **Yes, once the model provider points at the gateway.** Filter `startTime`/`endTime` = run start/end, `tfy.span_type` = `Model`, `tfy.model.name` = the greenlight model | Other traffic on the same key and model in the window is counted too; logs may land after the run ends; paused runs stretch the window. Mitigate with a dedicated virtual account for TrueForge (filter `createdBySubjectSlug`), runs not overlapping, and a check that the matched spans' `tfy.model.metric.input_tokens` / `output_tokens` equal the run's `model.message` `usage` one for one (same count, same numbers, close timestamps) |

Recommended for the demo: c, with the token match as the proof step. Switch to b if TrueForge runs in TrueFoundry mode.

## 8. Open items

1. Set `TFY_GATEWAY_BASE_URL` (control plane URL) and `TFY_GATEWAY_API_KEY`, call the spans API for the last 30 minutes, save a redacted response as `gateway_sample.json`, and replace every **unconfirmed** name above with the real one.
2. Repoint the local `greenlight` model provider at the gateway: a `truefoundry` type provider whose `base_url` is the gateway, keyed with a dedicated virtual account token.
3. Copy one trace URL and one filtered Requests URL from the TrueFoundry UI to learn the link format.
