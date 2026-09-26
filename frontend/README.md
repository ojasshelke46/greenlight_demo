# Greenlight frontend

The Next.js chat UI for Greenlight. See the [project README](../README.md) for what Greenlight does and how the pieces fit together.

```sh
cp .env.example .env.local   # BACKEND_URL, GREENLIGHT_API_KEY, NEXT_PUBLIC_USER_NAME, NEXT_PUBLIC_DEMO_REPO
npm install
npm run dev                  # http://localhost:3000
```

The browser only ever calls this app's own `/api` routes. They add the Greenlight API key server side and stream event routes through without buffering, so no secret reaches the client.

| Path | What |
| --- | --- |
| `app/api/` | Server side proxies to the backend |
| `components/chat/` | The conversation: agent messages, terminal, diffs, PR card, approval, handoffs |
| `components/campaign/` | Campaign panel, fleet and policy campaign views |
| `components/composer/` | Agent chip, model chip, mode chip and the message box |
| `lib/run-model.ts` | Projects the raw TrueForge event stream onto what the chat shows |

Design rules live in [`DESIGN.md`](../DESIGN.md).
