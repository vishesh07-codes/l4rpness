# Campaign workspace — design

Date: 2026-10-03
Status: approved (layout + decisions); implementation phased.

## Goal

Turn the Campaign page into a two-pane console: the **Supervisor transcript on
the left**, and a **resizable right panel** tabbing between **Generators**,
**Hypotheses**, and a new **Agent Workspace** (files the agent produced). The
agent should be able to reference files both *to the user* (clickable) and *to
itself* (hand a file into its context).

## Decisions (locked)

- Right pane is **resizable** (draggable divider, width persisted).
- Agent Workspace viewer is **read-only**.
- File references: **clickable chips in messages** that open the file in the
  Workspace, **plus `@`-insert** from the composer to hand a file to the agent.
- Build order: **layout → workspace → references**.

## What exists

- Agent work dir: `data/work/<campaign_id>` (`app/config.py:100`), created
  lazily by `app/tools.py:_work_dir`; agent tools (`read_file`/`write_file`/
  `bash`) are sandboxed by `tools._resolve` / `_label`.
- No HTTP route lists/reads/serves the work dir.
- No file part in the stream; paths only live inside `part.args`.
- Campaign page is one column with a top `SegmentedControl`.

## Phase 1 — layout

- Campaign container widens to `~1400px`; on `lg+` a flex row: left transcript
  (`flex-1 min-w-0`), a drag handle, right panel (`width` from state).
- Right panel header: `SegmentedControl` `[Generators · Hypotheses · Workspace]`;
  body renders the active panel (Generators/Hypotheses reuse existing markup;
  Workspace is a placeholder until Phase 2).
- On `< lg`: stacked — transcript above, panel below.
- Resize: pointer-drag on the handle, clamped `320–640px`, persisted in
  `localStorage` (`campaignPane`). Accessible: the handle is a focusable
  `role="separator"` with `aria-orientation="vertical"`, `aria-valuenow/min/max`,
  and ArrowLeft/Right nudge (±16px).
- The campaign header (target/actions) stays full width above the row.

## Phase 2 — Agent Workspace

Backend (sandboxed to `WORK_DIR/<id>`, reusing `tools._resolve`/`_work_dir`):

- `GET /api/campaigns/{id}/files` → recursive tree of the work dir
  `{ path, name, type: "file"|"dir", size }` (dirs last, hidden files shown).
- `GET /api/campaigns/{id}/file?path=` → `{ path, content, truncated }` for
  text files (cap ~200 KB), `415`/binary-safe otherwise.
- `GET /api/campaigns/{id}/file/raw?path=` → `FileResponse` for download/preview.
- Path traversal is rejected (`..`, absolute) — reuse `_resolve([work])`.

Frontend:

- `FilesPane`: tree (reuse `filetype.ts` icons) + read-only viewer (`AgentCode`
  for highlighting, mono for plain text), file size, empty state, loading, and
  a refresh control.

## Phase 3 — file references

- **To the user:** paths the agent mentions in assistant content and in tool
  parts (`read_file`/`write_file`/`bash`) render as file chips; clicking opens
  the Workspace pane on that file.
- **To the agent (itself):** typing `@` in the composer opens a fuzzy file
  picker; selecting inserts a token the backend expands into the prompt (path +
  the agent reads it via `read_file`). Keep the expansion server-side so the
  file is only read when sent.

## Edge cases

- Work dir may not exist yet → empty state, no error.
- Binary/large files → not shown as text; offer raw download.
- Deleted campaign → pane handles 404.
- Mobile: pane stacks; resize handle hidden.
- Reduced motion: pane width changes are not animated.

## Testing

- Backend: `tests/test_workspace.py` — list, read, traversal rejection, missing
  dir, truncation. Reuse the temp-root fixture pattern from `tests/test_skills.py`.
- Frontend: `tsc` + `vite build`; manual state walk (hover/focus/active/empty).
