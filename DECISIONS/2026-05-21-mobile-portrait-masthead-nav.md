# Mobile-portrait masthead navigation

**Status:** design approved, awaiting implementation plan
**Date:** 2026-05-21
**Scope:** signed-in pages only (`/case`, `/patient`, `/patient/consultations/[id]`)

## Problem

On portrait phones (≈ 375–430px wide, below Tailwind's `sm` breakpoint of 640px), the application's masthead hides every text navigation link via `hidden sm:inline`. The visible chrome on mobile portrait collapses to just the brand mark and the Clerk `UserButton`. There is no way to reach the patient file from the workspace, no way to return to the workspace from the patient file, and no way to go back to the patient file list from a consultation detail page — all of those links live in the masthead and are hidden.

Concretely:

- `apps/web/app/case/page.tsx:33` — `"Patient file →"` link is `hidden sm:inline`.
- `apps/web/app/patient/page.tsx:30` — `"← Workspace"` link is `hidden sm:inline`.
- `apps/web/app/patient/consultations/[consultationId]/page.tsx:32` — `"← Patient file"` link is `hidden sm:inline`.

Each page inlines a near-identical masthead, so the bug class is duplicated three times.

## Goals

1. Portrait phone users can move between the Workspace and Patient file at any time, from any signed-in page.
2. No regression at `sm` and above — the existing text-link layout stays as it is today.
3. The three duplicated mastheads collapse to one shared component, so future header changes touch one file.

## Non-goals

- Landing page (`/`) navigation. It is public-facing and structured differently; out of scope.
- Footer chrome on mobile. Out of scope.
- A new design system or icon set. Use the `lucide-react` library already installed.
- Hamburger menus and bottom tab bars were considered and rejected in favor of a compact icon header (see "Alternatives considered").

## Design

### Shared component

Create `apps/web/components/nav/MastheadNav.tsx`. It receives only the props that differ per page:

```ts
type MastheadNavProps = {
  active: "workspace" | "patient";
  plateLabel: React.ReactNode; // existing code uses JSX with <span className="diamond" />
  userIdSuffix: string;        // last 8 of userId, or "—"
};
```

`plateLabel` is a `ReactNode` (not a plain string) because the current mastheads render the divider as `<span className="diamond" />`, a CSS-styled element, not the unicode `◆` character. Callers pass the same JSX they have today.

Everything else (brand mark, Attending tag, `UserButton`, icon row) is identical across pages and lives inside the component.

The three signed-in pages each replace their inline `<header>` block with a single call:

```tsx
<MastheadNav
  active="workspace"
  plateLabel={<>Workspace <span className="diamond" /> Plate XXIV</>}
  userIdSuffix={userId?.slice(-8) ?? "—"}
/>
```

### Layout by breakpoint

| Element                              | `< sm` (portrait phone) | `sm` – `md` | `md+` |
| ------------------------------------ | :---------------------: | :---------: | :---: |
| Brand `◆ MedAI Council`              |            ✓            |      ✓      |   ✓   |
| Plate label (`Workspace ◆ Plate XXIV`) |          hidden         |      ✓      |   ✓   |
| **Icon row** (Workspace + Patient file) |       **✓ (new)**       |    hidden   |  hidden |
| Text links (`Patient file →`, `← Workspace`) |       hidden       |      ✓      |   ✓   |
| `Attending ◆ ##`                     |          hidden         |    hidden   |   ✓   |
| `UserButton`                         |            ✓            |      ✓      |   ✓   |

Portrait phones see: *brand · icon row · avatar*. Nothing else.

### Icon row

- Icons from `lucide-react`: **`NotebookPen`** for Workspace (active draft), **`Folder`** for Patient file (archived consultations).
- Each icon is wrapped in a Next `<Link>` with a 40×40px tap target and an `aria-label` of `"Workspace"` or `"Patient file"`.
- Visibility class: `flex sm:hidden` on the icon row container. The existing text-link row gets `hidden sm:inline-flex` (or equivalent) so the two are mutually exclusive at every breakpoint.
- **Active state:** the icon matching the `active` prop gets `text-indigo` and a 2px `border-b border-indigo` underline aligned with the masthead's existing bottom border, so it reads like a tabbed plate. It also receives `aria-current="page"`.
- **Inactive state:** `text-ink-muted` with `hover:text-ink`, no underline.
- Tapping the active link routes to the same route — Next.js handles same-route navigations as a no-op. No special guard needed.

### What changes per page

- **`/case/page.tsx`** → `<MastheadNav active="workspace" plateLabel="Workspace ◆ Plate XXIV" userIdSuffix={...} />`. Drops ~30 lines of inline header.
- **`/patient/page.tsx`** → `active="patient"`, `plateLabel="Patient file ◆ Plate XXV"`. The "← Workspace" text link is preserved at `sm+` via the shared component, and the icon row makes it reachable at `< sm`.
- **`/patient/consultations/[consultationId]/page.tsx`** → `active="patient"` (the user is inside the patient file). The in-body "← Patient file" link near the page content (not the masthead one) is unchanged.

## Alternatives considered

- **Minimal fix — drop `hidden sm:inline` on the Patient-file link only.** Rejected: leaves the duplicated mastheads in place, doesn't help mobile users on `/patient` or the consultation detail page, and keeps three drift-prone copies of the header.
- **Bottom tab bar.** Rejected: only two destinations exist; a persistent bottom bar is heavy for that. Adds a second navigation surface to keep in sync with the masthead.
- **Hamburger menu.** Rejected: hides primary navigation behind an extra tap for a two-destination app. The compact icon header gives one-tap access without taking more space than a hamburger button would.

## Risks and edge cases

- **Icon ambiguity.** `NotebookPen` vs `Folder` may not be self-evident; `aria-label`s carry meaning for screen readers, and the active-state underline gives a visual anchor. If user testing shows confusion, swap to glyphs that match the editorial diamond aesthetic.
- **Landscape phones.** A landscape iPhone is wider than `sm`, so it falls into the text-link layout — no special handling needed. Confirmed by inspection of Tailwind defaults.
- **Future destinations.** If a third top-level destination is added (e.g., a settings page), three icons still fit in the portrait header. Beyond three, revisit the pattern.

## Testing

- Manual at 375px, 393px, 430px portrait widths on `/case`, `/patient`, `/patient/consultations/[id]`: confirm icons render, both nav links work, active state shows on the current page.
- Manual at 640px (`sm`) and 768px (`md`): confirm the existing text-link behavior is unchanged.
- Manual landscape phone (~700px wide): confirms text-link layout reappears at `sm+`.
- Accessibility spot check with VoiceOver / NVDA: brand + Workspace + Patient file + UserButton announce in order; active link reports `aria-current="page"`.
- No new unit tests — this is a presentational composition. Existing tests covering these pages should continue to pass; verify during implementation.

## Out of scope (for follow-up)

- Refining the landing page (`/`) masthead for mobile portrait.
- Mobile footer behavior.
- A broader audit of `hidden sm:inline` / `hidden md:inline` usage across the rest of the app.
