# Campaign page performance (2026-10-01)

## Problem

The campaign page froze when a campaign accumulated a lot of history. On the
running "Common Codes" campaign the supervisor turn was one assistant row with
~1,500 parts and 3.4 MB of `parts` JSON (plus 2.1 MB of `tool_calls`). The page
rendered every part of that message, fully syntax-highlighted with Shiki, even
though the tool cards were collapsed.

Measured on the real campaign 1 data (local copy of `/app/data/l4rpness.db`),
before the fix:

- 240,775 DOM nodes, 961 mounted tool blocks
- 30.0 s of long-task blocking in the first 9 s, worst single task 24.7 s
- initial load barely interactive

Supporting costs: every SSE token/chunk re-rendered the transcript, every
generator event refetched the generator and hypothesis lists, the generator
detail pane rendered every message of a worker, and partially loaded message
pages re-fetched the whole gigantic row on every catch-up.

## Fixes

Frontend:

- `AssistantMessage` renders a 60-part tail window with a "Show earlier steps"
  button (120 more per click); keys stay on absolute part index so expanding
  does not remount.
- `ToolBlock` shows the first 8,000 characters with a "Show more" expander up
  to 60,000; copy still carries the full output.
- `agent-code.tsx` skips Shiki above 40,000 characters and bounds its token
  cache to 200 entries.
- SSE thinking/content tokens and tool-output chunks are coalesced into one
  state update per animation frame; non-batched events flush first to keep
  ordering.
- Generator messages are windowed (40 per page, "Load earlier output"),
  fetched tail-first from the server; stale async loads are discarded.
- Generator and hypothesis list refreshes are throttled to 1.5 s and skip
  no-op re-renders via equality checks; generator events only append to the
  worker pane while it is open.
- Hypotheses render 60 at a time and each card is memoized per id/status/
  result/updated_at.
- Message parts cache bounded to 240 entries.

Backend:

- `GET /generators/{id}/messages` supports `before` plus `X-Has-More` and
  returns the newest `limit` rows instead of always the whole transcript.
- `GET /campaigns/{id}/messages?after=` is capped at 500 rows when no explicit
  limit is given, so a client that was away can never pull an unbounded tail
  in one response.

## Verification

Playwright against a local server with a copy of the live campaign 1 database:

| metric | before | after |
| --- | --- | --- |
| DOM nodes | 240,775 | 26,901 |
| mounted tool blocks | 961 | 126 |
| long-task total (9 s window) | 30,092 ms | 3,070 ms |
| worst blocking task | 24,661 ms | 720 ms |

With 200 extra synthetic messages: initial render worst task 51 ms, 60 rows,
"Load earlier" 51 ms per page, no console errors. Generator tab: 0 long tasks.
Hypotheses tab: 0 long tasks. Scroll of the supervisor transcript: 57 fps.
Expanding 120 more parts of the giant message blocks ~1.4 s once, by user
request, then settles.

`pytest`: 112 passed. `npm run build`: passes.
