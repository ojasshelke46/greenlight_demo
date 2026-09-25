---
name: Greenlight
description: A chat first control room for an agent that fixes vulnerable dependencies and merges only with your approval.
colors:
  accent: "#c8ff3d"
  canvas: "#050505"
  panel: "#0e0e0e"
  card: "#161616"
  raised: "#1d1d1d"
  line: "rgb(255 255 255 / 0.06)"
  line-strong: "rgb(255 255 255 / 0.12)"
  text: "#f4f2ec"
  text-muted: "#a9a7a1"
  text-subtle: "#807e79"
  status-fail: "#ff4d4d"
  status-progress: "#ffb020"
typography:
  display:
    fontFamily: "Sora, ui-sans-serif, system-ui, sans-serif"
    fontSize: "3.25rem"
    fontWeight: 600
    lineHeight: 1.1
    letterSpacing: "-0.03em"
  headline:
    fontFamily: "Sora, ui-sans-serif, system-ui, sans-serif"
    fontSize: "1.5rem"
    fontWeight: 600
    lineHeight: 1.2
  title:
    fontFamily: "Sora, ui-sans-serif, system-ui, sans-serif"
    fontSize: "0.95rem"
    fontWeight: 600
  body:
    fontFamily: "Inter, ui-sans-serif, system-ui, sans-serif"
    fontSize: "0.95rem"
    fontWeight: 400
    lineHeight: 1.6
  label:
    fontFamily: "Inter, ui-sans-serif, system-ui, sans-serif"
    fontSize: "0.82rem"
    fontWeight: 500
  code:
    fontFamily: "JetBrains Mono, ui-monospace, monospace"
    fontSize: "0.8rem"
    lineHeight: 1.6
rounded:
  card: "16px"
  panel: "24px"
  composer: "28px"
  pill: "999px"
spacing:
  gutter: "20px"
  stack: "16px"
  section: "28px"
components:
  button-send:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.canvas}"
    rounded: "{rounded.pill}"
    size: "44px"
  button-hold-to-approve:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.accent}"
    rounded: "{rounded.pill}"
    height: "56px"
  chip:
    backgroundColor: "{colors.card}"
    textColor: "{colors.text-muted}"
    rounded: "{rounded.pill}"
    height: "32px"
  composer:
    backgroundColor: "{colors.canvas}"
    rounded: "{rounded.composer}"
    padding: "12px"
  card:
    backgroundColor: "{colors.card}"
    rounded: "{rounded.card}"
  panel:
    backgroundColor: "{colors.panel}"
    rounded: "{rounded.panel}"
  sidebar:
    backgroundColor: "{colors.panel}"
    width: "300px"
---

# Design System: Greenlight

## Overview

**Creative North Star: "Light leaking into a dark room"**

Greenlight is a near black room with one source of light: an electric lime accent that bleeds in from the right edge, lives inside the orb, and lights the few controls that matter. Everything else stays quiet so the agent's real work can be read. The surface is a conversation, not a dashboard: the user pastes a repo, the agent answers with what it actually did, and the one irreversible step arrives as a single focused card.

Density is calm. Agent output is dense where it has to be (terminal, diff) and framed by generous space everywhere else. Nothing on screen is decorative state: every block, step and status comes from a real event.

Rejected: traffic lights, lamps, and any signal metaphor. Dashboard grids of equal cards. Colour used for decoration inside agent output.

**Key Characteristics:**
- One accent, defined once as `--accent` in `frontend/app/globals.css`; swapping that line recolours the product.
- Status colours appear only inside agent output.
- Monospace only for what the agent executes or produces.
- The orb is the agent: 140px in the empty state, 28px as its avatar in the chat.

## Colors

A monochrome dark room with a single electric accent.

- **Accent (`#c8ff3d`)**: glows, the orb, gradient headlines, active states, the send button, the approval card, and "passing or done" inside agent output. Never used for body text blocks.
- **Canvas / panel / card / raised (`#050505`, `#0e0e0e`, `#161616`, `#1d1d1d`)**: layered surfaces, each one step lighter. Borders are 1px white at 6% (`line`) or 12% (`line-strong`).
- **Text (`#f4f2ec`, `#a9a7a1`, `#807e79`)**: warm off white, muted, subtle. Subtle text holds at least 4.5:1 on the canvas.
- **Status, agent output only**: `status-fail` (`#ff4d4d`) for vulnerable or failing, `status-progress` (`#ffb020`) for in progress, and the accent for passing or done.

The canvas carries a faint 48px grid (1px lines at 4% white) and one large radial accent glow from the right edge, both on a fixed layer.

## Typography

Sora for display (the greeting, card titles, headings), Inter for all UI, JetBrains Mono for commands, terminal output, diffs, package names, advisories, branches and SHAs. Display headlines use a gradient fill from the accent to warm off white; this is the one place gradient text is allowed. Emphasis in UI comes from weight, never from mixing families.

## Layout

A 300px sidebar (run history, search, the policy card) and a main column. The empty state centres the orb, greeting, composer and three suggestion cards. Once a run starts, the conversation fills a centred 768px column and the composer docks to the bottom. Below 1024px the sidebar becomes an overlay that starts closed; below 640px the model chip collapses to its icon and cards stack. No horizontal scroll at 390px.

## Elevation & Depth

Tonal layering first, light second. Surfaces step up in lightness; depth comes from soft offset shadows under floating pieces (composer, sheets, menus). Accent glows are reserved for the orb, the send button, the policy card and the approval card, because those are where light is supposed to be in the room.

## Shapes

Large and soft: cards 16px, panels 24px, composer 28px, pills 999px for chips, buttons, the history items and the hold control. Circles only for icon badges, avatars, status dots and the orb.

## Components

- **Orb**: layered radial gradients plus two counter rotating conic swirls and a bloom. Idle: 6s breathe, 14s swirl. Working: 1.6s breathe, 4s swirl. One 700ms flare when an approval completes. Static under reduced motion.
- **Composer**: black card, 28px radius, inner accent glow on focus. Access and visibility chips resolve above the textarea after the debounced access check. Mode chip toggles Ship it and PR only; Ship it is disabled with a tooltip reason without push access. Round accent send button. Enter sends, Shift+Enter adds a line.
- **Agent message**: orb avatar, status line, a collapsible step rail (dots: amber active, accent done, red failed, with elapsed time), then blocks in event order: text, terminal, action rows, vulnerability cards, syntax highlighted diff, PR card.
- **Approval card**: appears only for merge_pull_request on a ship run with a collaborator bot. The rest of the chat dims to 35%. Hold to approve for 2 seconds; the accent fill sweeps linearly and rewinds over 350ms on early release. Reject is a quiet text button. Backend refusals show inside the card in red.
- **Sheets**: policy and audit ledger slide in from the right on the drawer curve, 360ms.

Motion tokens: `--ease-out: cubic-bezier(0.23, 1, 0.32, 1)` for entrances and chips (200 to 240ms), `--ease-in-out: cubic-bezier(0.77, 0, 0.175, 1)` for on screen movement (composer glide, orb travel, 350ms), `--ease-drawer: cubic-bezier(0.32, 0.72, 0, 1)` for sheets. Terminal lines only fade in over 120ms, with no movement.

## Do's and Don'ts

- Do render every block from a real event; nothing appears before its event arrives.
- Do keep status colours inside agent output.
- Do route approve and reject through the backend approval endpoint only.
- Don't bring back traffic lights, lamps or signal metaphors.
- Don't use hyphens or em dashes in UI copy.
- Don't animate what the user triggers a hundred times a day, and don't exceed 400ms outside the orb loops and the hold fill.
