# l4rpness — Design System

The visual language for the l4rpness web console. Every component in
`src/` follows this document. It is dark-first, quiet, and strictly
monochrome: large near-black fields, one task in focus, an ink ramp for
text, and polished **silver metal** reserved for the primary action. Color
is semantic only (status); it is never decoration.

The system is token-driven. Components almost never hard-code a color —
they read the semantic CSS variables defined in
`src/app/beautifui/foundation.css` and re-exported to Tailwind through
`@theme inline`. Retuning a token restyles the whole app. When adding a
component, reach for an existing token before inventing a value.

---

## 1. Principles

1. **Dark-first.** Dark mode is the default and the design target. Light
   mode is supported but secondary — verify dark first.
2. **Monochrome + silver.** Hierarchy comes from surface elevation, the
   ink ramp, and depth — not from hue. The single filled primary action is
   a **polished silver key** in dark mode (graphite, its inverse, in light);
   everything else is neutral. Saturated color appears only on status
   (green / amber / red). If a surface is tinted, it is wrong.
3. **Physical depth over flat hairlines.** The console should read as
   machined hardware: raised keys, recessed wells, beveled panels. Depth
   comes from layered `box-shadow` + a light-catching bevel, not from a
   solid border. Keep a hairline only where it marks structure (dividers)
   or state (focus, selected). See §4 *Materials*.
4. **Quiet motion.** Transitions are short (120–300ms) and eased with the
   shared curves. Motion confirms an action or reveals structure; it never
   performs.
5. **Prefer shadcn primitives.** Buttons, badges, inputs, labels,
   textareas, tooltips, separators, scroll-areas live in
   `src/components/ui/*` and are the canonical building blocks. Compose
   these before writing bespoke markup.

---

## 2. Color

All colors are authored in OKLCH so lightness is perceptually even across
the ramp. Semantic tokens live in `foundation.css`; never reference a raw
`oklch(...)` in a component.

### Surfaces (dark, the default)

| Token        | Role                                   | ~Hex      |
| ------------ | -------------------------------------- | --------- |
| `--canvas`   | Deepest field — sidebar, page gutter   | `#0a0a0a` |
| `--page`     | App background                         | `#0d0d0d` |
| `--inset`    | Recessed fields, code, table headers   | `#131313` |
| `--surface`  | Cards, panels, popovers                | `#1a1a1a` |
| `--hover`    | Hover fill on surfaces                 | `#232323` |
| `--hover-2`  | Pressed / stronger hover               | `#2b2b2b` |

Elevation order, dark → light: `canvas < page < inset < surface < hover`.
A raised element is one step lighter than what sits behind it.

### Ink ramp (text)

| Token      | Role                       | Measured on `--page` / `--surface` |
| ---------- | -------------------------- | ---------------------------------- |
| `--ink`    | Primary text, headings     | 18.7:1 / 17.2:1                    |
| `--ink-2`  | Body, secondary text       | 7.45:1 / 6.83:1                    |
| `--ink-3`  | Muted labels, placeholders | 6.28:1 / 5.76:1                    |

Three steps only. Do not introduce intermediate greys — pick the closest.

**All three clear 4.5:1 on every surface they render on**, so `--ink-3` is
safe for real text, not just decoration. It is set at `oklch(0.66)` in dark
and `oklch(0.52)` in light specifically to hold that floor — do not darken
it in dark mode (or lighten it in light mode) to make it "quieter"; that
was the bug this value fixes. Re-measure if you change any surface token.

### Lines

| Token            | Role                              |
| ---------------- | --------------------------------- |
| `--line`         | Default hairline border           |
| `--line-strong`  | Emphasized border, input outline  |
| `--line-soft`    | Barely-there internal divider     |
| `--field`        | Input / control fill              |

### Primary key — silver metal

The one filled action is a metal key, not a color. Its face and gloss are
monochrome; only lightness changes between themes.

| Token                    | Role                                      |
| ------------------------ | ----------------------------------------- |
| `--key-accent-bg`        | Key face — silver (dark) / graphite (light) |
| `--key-accent-bg-hover`  | Hover face                                |
| `--key-accent-bg-press`  | Pressed face                              |
| `--key-accent-fg`        | Text on the key (dark on silver, light on graphite) |
| `--key-accent-gloss`     | Bright top + shaded bottom metallic overlay |
| `--key-accent-shadow*`   | Ring + top highlight + contact + ambient  |

`index.css` repoints `--accent` at `--hover` (so shadcn's `bg-accent`
utilities stay neutral). The legacy `--accent-*` tokens remain defined but
are **not** used for UI chrome — status and the silver key have replaced the
old orange accent.

### Semantic (status only, used sparingly)

`--green` success/done · `--orange` running/in-progress · `--red`
error/destructive. Each has a `-tint` companion for soft fills. Status
pills derive fill and border from these via `color-mix`.

Status hues carry the only saturation in the interface and never touch
chrome. Keep running amber clear of error red (≥ 15° apart) so a live signal
can't read as a failure.

### Tailwind usage

Tokens surface as utilities: `bg-page`, `bg-surface`, `bg-inset`,
`text-ink`, `text-ink-2`, `text-ink-3`, `border-line`, `border-line-strong`,
`bg-hover`, `text-key-accent-fg`, `bg-green/-orange/-red`. Chrome is
monochrome; reach for the material classes (§4) before any color utility.

**Pages use the ink ramp.** Prefer `text-ink` / `text-ink-2` / `text-ink-3`
over the shadcn aliases (`text-muted-foreground`, `text-foreground`) so
text color reads consistently across files. The shadcn aliases resolve to
the same values and remain correct *inside* `components/ui/*`, which must
stay drop-in compatible with upstream shadcn.

---

## 3. Typography

Two families, loaded as variable fonts via `@fontsource-variable`:

- **Geist** (`--font-sans`, `'Geist Variable'`) — all UI text.
- **Geist Mono** (`--font-mono`, `'Geist Mono Variable'`) — targets,
  hostnames, skill names, file names, code, terminal output, tabular data.

Base body is `14px`, `line-height 1.5`, letter-spacing `-0.01em`, with
`cv11` + `ss01` OpenType features on.

### Scale

| Use                    | Size / weight                         |
| ---------------------- | ------------------------------------- |
| Page title (`h1`)      | `text-lg` (18px), `font-semibold`     |
| Section title (`h2`)   | `text-base` (16px), `font-medium`     |
| Body                   | `text-sm` (14px), `font-normal`       |
| Secondary / meta       | `text-[13px]`, `text-ink-2`           |
| Small / captions       | `text-xs` (12px), `text-ink-3`        |
| Eyebrow / machine label| `.eyebrow` — 10px mono, uppercase, tracked |

Headings use `text-wrap: balance`; paragraphs use `text-wrap: pretty`.
Numbers in tables use `tabular-nums`.

---

## 4. Shape, spacing, elevation

### Radius

`--radius` (`0.75rem` = 12px) in `index.css` is the single knob — every
named Tailwind radius derives from it (`sm` 8, `md` 10, `lg` 12, `xl` 16,
`2xl` ~22). Change that one value to make the whole interface rounder or
tighter; do not chase individual components.

| Token             | px  | Use                          |
| ----------------- | --- | ---------------------------- |
| `--radius-chip`   | 8   | Chips, tags, small controls  |
| `--radius-control`| 10  | Buttons, inputs, icon buttons|
| `--radius-card`   | 12  | Cards, panels, table wrapper |
| `--radius-window` | 16  | Large containers, dialogs    |
| pill / `rounded-full` | — | Composer, avatars, live dots, send button |

Hardcoded `rounded-[Npx]` should match a row above — `12px` for a card,
`10px` for a control, `8px` for a chip. Respect concentric radius: a
child's radius = parent radius − padding.

### Spacing

Base unit is 4px (Tailwind default). Common paddings: controls `px-3`,
cards `p-3`/`px-3.5 py-2.5`, page gutters `px-8 py-10` (wide pages) or
`px-6 py-6` (chat). Gaps between stacked blocks: `gap-4`; between related
inline items: `gap-2` / `gap-1.5`.

### Shadows

Use the semantic shadow tokens, never ad-hoc `shadow-*`:

- `shadow-hairline` — a 1px ring, no blur. Structure/dividers only.
- `shadow-btn` — resting control.
- `shadow-card` — cards and panels.
- `shadow-raised` — floating buttons, "jump to latest".
- `shadow-overlay` — popovers, selects, tooltips.

In dark mode the ring goes to a low-alpha **light** hairline so the edge
dissolves into the shadow as one stroke.

### Materials

Depth is a small system of composable classes, all token-driven:

- `.material-key` — a **raised control face**. Animatable `background-color`
  under a static gloss gradient, plus `--key-shadow` (ring + top highlight +
  contact + ambient). Hover lightens and lifts; `:active` darkens and
  switches to an inset shadow so the key visibly **sinks**.
- `.material-accent` — the **silver metal primary key**. Sets the local
  `--_key-*` hooks from the `--key-accent-*` tokens (silver face + metallic
  gloss in dark, graphite in light). Pair with `.material-key`.
- `.material-danger` — the same hooks recolored to semantic red for
  destructive keys. Pair with `.material-key`.
- `.material-well` — a **recessed** track or field (`--inset` fill + inset
  shadow). Composers, segmented tracks, readouts.
- `.material-bevel` — the bare top-light/bottom-shade edge for panels that
  already carry `shadow-card` / `shadow-overlay`.

Neutral key gradients stay within ±4% lightness of their base. The silver
primary key is the exception: its `--key-accent-gloss` runs a bright top to
a shaded bottom on purpose, to read as lit metal. Nothing is ever tinted —
the metal is pure greyscale, and the `--ink` scale is unchanged so contrast
holds. Retune via the `--key-*` / `--well` / `--bevel` tokens in
`foundation.css`; never re-derive per component.

---

## 5. Motion

Motion follows Emil Kowalski's decision order: *should this animate at all →
what is its purpose → which easing → how fast.*

**Frequency gates it.** Anything a user triggers dozens of times a day gets
instant feedback or a ≤150ms color/opacity transition, never a custom
animation. Sidebar rows, nav hovers and card hovers are all in this bucket.

**Rules this codebase holds to:**

- Never enter from `scale(0)` — `zoom-in-95` (0.95) with `opacity-0`. Nothing
  in the real world appears from nothing.
- Never `ease-in` on a UI element; enters and exits use `ease-out`.
- Popovers are origin-aware: Select, Dropdown and Tooltip all scale from their
  trigger via `origin-(--radix-*-content-transform-origin)`. Modals are the
  exception and stay centered.
- Tooltips delay before the first one, then open instantly. The
  `TooltipProvider` in `App.tsx` sets `delayDuration={500}`
  `skipDelayDuration={300}` — do not set per-tooltip delays.
- Press feedback is `active:scale-[0.96]`, that exact value. On a
  `.material-key` it also swaps to the pressed face + inset shadow so the
  control sinks as it scales.
- Name the properties you transition (`transition-[box-shadow,transform]`),
  never `transition: all`.
- Stagger an infrequent staged entrance 30–80ms per item. The Dashboard grid
  uses 50ms and runs **once** — it polls every 3s while a campaign is running,
  and re-staggering on every poll would be unbearable. That's what the
  `revealed` ref guards.
- Contextual icon swaps animate `scale 0.25→1`, `opacity 0→1`, `blur 4px→0`
  (the sidebar theme toggle).


Shared easing tokens (in `foundation.css`):

- `--ease-out-strong` `cubic-bezier(0.23,1,0.32,1)` — enters, expands.
- `--ease-in-out-strong` — symmetric reveals.
- `--ease-link` — underline/hover accents.

Durations: micro-feedback `120–150ms`, reveals/collapses `300ms`, theme
flip `400ms`. Buttons press with `active:scale-[0.96]`. Spring swaps
(icon/label morphs) use `SPRING_SWAP` from `@/lib/ease`. Everything honors
`prefers-reduced-motion`.

---

## 6. Icons

**Hugeicons** is the icon set — the free stroke variants from
`@hugeicons/core-free-icons`, rendered through `@hugeicons/react`.

Do **not** import from `@hugeicons/react` or `lucide-react` directly in a
component. Import named icons from the barrel at
[`@/components/icons`](src/components/icons.tsx), which wraps each glyph as
a lucide-compatible component:

```tsx
import { Plus, Trash2, Sparkles } from "@/components/icons"

<Plus className="size-4" strokeWidth={1.5} />
```

Default stroke width is `1.5`. Size with the `size-*` utility (CSS width
overrides the SVG attribute). To add a new icon, map its name to a
Hugeicons glyph inside the barrel — keep names lucide-familiar so call
sites read naturally.

`lucide-react` is **not** installed. Registry components import from it, so
repoint those after every `shadcn add`.

Hugeicons' `01` variants are **chevrons** (no stem) and `02` are **true
arrows** — `ChevronDown` uses `ArrowDown01Icon`, `ArrowDown`/`ArrowUp` use
the `02` variants. Check the path data before picking; the names don't tell
you which is which.

Prefer a simple glyph at 18px. A sparkle or any multi-part mark collapses
into a blur at rail size — that's why Skills uses a book, not a sparkle.

The one hand-written `<svg>` left is `motion/loader.tsx`, which is animated
loader geometry rather than an icon. Leave it.

---

## 7. Components

- **shadcn/ui** (`src/components/ui/*`) — the base layer. Button, Badge,
  Input, Label, Textarea, Tooltip, Separator, ScrollArea. Prefer these.
- **shadcn chat components** — `attachment`, `message`, `bubble`,
  `marker`, `message-scroller`, installed from the registry
  (`pnpm dlx shadcn@latest add …`). These are the canonical chat
  primitives; build on them rather than hand-rolling a new bubble,
  attachment, or scroller. Note they import `cn` from the `cn` package
  (shadcn's own clsx+tailwind-merge build), which is correct — do not
  rewrite those imports to `@/lib/utils`.

  Two things the registry does **not** ship and this repo supplies:
  the `shimmer`, `scroll-fade-x/y` and `scrollbar-none` utilities in
  `index.css`, and the `icon-xs` / `icon-sm` Button sizes the attachment
  actions expect. Keep both when upgrading.

  Registry components import icons from `lucide-react`, which this project
  does not install. After every `shadcn add`, repoint those imports at
  `@/components/icons` (see `select`, `dropdown-menu`, `message-scroller`,
  `sonner`). Decline the `button.tsx` overwrite — ours carries the `static`
  prop, `scale-[0.96]` and the extra icon sizes.

### Use the primitive, not a hand-rolled one

| Need | Use | Never |
| --- | --- | --- |
| Destructive confirm | `ConfirmDialog` (AlertDialog) | `window.confirm` |
| Transient success/failure | `toast` from `sonner` | an inline error that never clears |
| Persistent inline error | `Alert` + `AlertTitle`/`AlertDescription` | a bare red `<p>` |
| Loading placeholder | `Skeleton` | a hand-built pulsing `div` |
| Any option list | `Select` | a native `<select>` |
| Card surface | `Card` | a `div` with border + radius classes |
| Icon-only button | `IconAction` | a `<button>` with a `title` attribute |

`ConfirmDialog` and `IconAction` are the two project wrappers — they exist so
destructive confirms and tooltip'd icon buttons are written the same way
everywhere. `sonner`'s `Toaster` is mounted once in `App.tsx` and is passed
the theme explicitly (this app owns `.dark`; it does not use `next-themes`).
- **Motion primitives** (`src/components/motion/*`) — animated Button,
  Select, morph Popover, loaders. Use where the extra motion is warranted.
- **Agent surface** (`src/components/agents/*`) — the chat/transcript
  system: prompt input, message scroller, tool results, todo list,
  loading states. All read the same tokens.

### Page header

Every top-level page opens with `PageHeader` (`title`, optional
`description`, `action`, `back`). Do not hand-roll a page title — this is
what keeps headings, descriptions and primary actions aligned across
Skills and New campaign.

### Dashboard

The Dashboard is the product's front door and follows the AI-chat home
pattern rather than a records table: a centered question, the shared
`PromptInput` composer that starts a campaign from a bare target,
suggestion chips, then recent campaigns as a two-column card grid under a
`Marker` separator.

Each card is a single link target (`after:inset-0` overlay) with its row
actions lifted to `z-20` above it. Those actions are visible by default
and only hide behind hover on hover-capable pointers
(`[@media(hover:hover)]:opacity-0`) — never invisible-but-tappable on
touch.

### Sidebar

Modeled on grok.com's rail. Fixed-left on `--canvas`, collapsible between
`w-60` and a `60px` icon rail; the collapsed state persists in
`localStorage` and `main` pads to match. Top to bottom:

1. Wordmark (accent dot) + a collapse toggle.
2. Quiet nav rows — **New campaign** (compose icon), Dashboard, Skills.
   Grok styles its compose row like any other row, so there is no filled
   button in the rail.
3. A **Recent** history list of campaigns, with a pulsing accent dot on
   running ones.
4. A muted footer line with a shield icon.

Rows are `rounded-[8px]`, 18px icons, `--ink-2` text that goes `--ink` on
hover with a `--hover` fill; the active row gets a `--hover` fill and
`--ink` text. **No chips, badges, or boxed labels in the rail** — keep it
a clean, typographic list.

**Below `md` the rail does not dock.** It slides in as an overlay with a
backdrop, `main` drops its padding, and a menu button appears top-left.
A docked 240px rail leaves 80px of content at 320px, so any new fixed
chrome must be gated the same way.

### Responsive floor

Every screen must work at **320px wide and 200% zoom** with no horizontal
scrolling and nothing clipped. This is the one layout rule that is not
negotiable per-page.

### Buttons

Buttons are physical keys. `default` is the **polished silver key**
(`.material-key .material-accent`, `--key-accent-fg` text) — the single
filled primary action per view, and the only bright metal on the screen.
`secondary` / `outline` are **neutral raised keys** (`.material-key`),
`ghost` stays flat for icon buttons and toolbars, `destructive` is the red
key (`.material-key .material-danger`, `--key-fg` for AA). Sizes `sm` (h-8)
default in this app.

A second silver key on the same view breaks "exactly one filled action per
view". Focus rings are neutral (`--ink-2`), not the key color.

Every pressable control presses with `active:scale-[0.96]` — that exact
value, not `0.95` — and key surfaces additionally sink via `.material-key`.

### Status & live signals

Running state = a pulsing amber dot + label. Completed states use
`StatusBadge` with a semantic dot. Never more than one live signal per row.

---

## 8. Accessibility

- Focus-visible ring is neutral (`2px --ink-2`) at `2px` offset; inputs
  suppress the ring in favor of a border shift.
- Meet WCAG AA on text: `--ink`/`--ink-2` on `--page`/`--surface` pass;
  `--ink-3` is for non-essential labels only.
- Every icon button has an `aria-label` and `title`. Icons inside labeled
  controls are `aria-hidden`.
- All motion collapses under `prefers-reduced-motion`.
