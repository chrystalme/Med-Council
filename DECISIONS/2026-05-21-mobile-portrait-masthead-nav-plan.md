# Mobile-portrait masthead navigation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give portrait-phone users a way to navigate between the workspace and the patient file on every signed-in page, by extracting a shared masthead component with an icon-only nav row visible at `< sm`.

**Architecture:** A single `MastheadNav` React component replaces the inline `<header>` blocks in three signed-in pages. At `sm+` it renders today's text-link layout unchanged. At `< sm` (portrait phones) it renders a compact icon row (Workspace + Patient file) with the active page highlighted. Per-page differences (active state, plate label, back-link target/text) are passed in as props.

**Tech Stack:** Next.js 16, React 19, TypeScript 5, Tailwind CSS v4, `lucide-react` (already installed). No new dependencies.

**Spec:** `DECISIONS/2026-05-21-mobile-portrait-masthead-nav.md`

**Spec deviation noted:** The plan adds a `backLink` prop (not present in the spec's `MastheadNavProps`). Reason: the three pages each have a different `sm+` back-link (`Patient file →`, `← Workspace`, `← Patient file`). Without `backLink` the component would either regress one page's UX or have to inspect the route — both worse than threading one extra prop.

**Verification commands** (used at the end of every task):

- Type check: `cd apps/web && npx tsc --noEmit` — should print nothing (success).
- Lint: `cd apps/web && pnpm lint` — should print nothing or only warnings unrelated to touched files.

Manual viewport verification is a single task at the end (Task 5).

---

## File Structure

- **Create:** `apps/web/components/nav/MastheadNav.tsx` — shared masthead used by all signed-in pages. Single responsibility: render the masthead chrome with the right active state, plate label, and back-link for the current page.
- **Modify:** `apps/web/app/case/page.tsx` — replace inline header with `<MastheadNav active="workspace" …/>`.
- **Modify:** `apps/web/app/patient/page.tsx` — replace inline header with `<MastheadNav active="patient" …/>`.
- **Modify:** `apps/web/app/patient/consultations/[consultationId]/page.tsx` — replace inline header with `<MastheadNav active="patient" …/>`.

No other files need to change.

---

## Task 1: Create the shared `MastheadNav` component

**Files:**
- Create: `apps/web/components/nav/MastheadNav.tsx`

- [ ] **Step 1: Create the directory and component file**

Create `apps/web/components/nav/MastheadNav.tsx` with this exact content:

```tsx
import { UserButton } from "@clerk/nextjs";
import Link from "next/link";
import { Folder, NotebookPen } from "lucide-react";
import type { ReactNode } from "react";

type Destination = "workspace" | "patient";

export type MastheadNavProps = {
  active: Destination;
  plateLabel: ReactNode;
  backLink: { href: "/case" | "/patient"; label: ReactNode };
  userIdSuffix: string;
};

export function MastheadNav({
  active,
  plateLabel,
  backLink,
  userIdSuffix,
}: MastheadNavProps) {
  return (
    <header className="px-6 md:px-14 pt-7 pb-5 flex items-center justify-between border-b border-line">
      <Link href="/" className="flex items-baseline gap-4 group">
        <div className="flex items-center gap-2.5">
          <span
            aria-hidden
            className="block h-2.5 w-2.5 rounded-sm bg-indigo rotate-45 group-hover:bg-cornflower transition-colors"
          />
          <span className="font-display text-[1.25rem] tracking-tight text-ink">
            MedAI Council
          </span>
        </div>
        <span className="mono-label hidden sm:inline">{plateLabel}</span>
      </Link>

      <div className="flex items-center gap-5">
        {/* Portrait-phone icon row (< sm). Mutually exclusive with the text back-link below. */}
        <nav aria-label="Primary" className="flex items-center gap-1 sm:hidden">
          <IconLink
            href="/case"
            label="Workspace"
            icon={<NotebookPen className="h-5 w-5" aria-hidden />}
            isActive={active === "workspace"}
          />
          <IconLink
            href="/patient"
            label="Patient file"
            icon={<Folder className="h-5 w-5" aria-hidden />}
            isActive={active === "patient"}
          />
        </nav>

        {/* sm+ back-link (preserves today's per-page wording) */}
        <Link
          href={backLink.href}
          className="mono-label hover:text-indigo transition-colors hidden sm:inline"
        >
          {backLink.label}
        </Link>

        <span className="mono-label hidden md:inline">
          Attending <span className="diamond" /> {userIdSuffix}
        </span>
        <UserButton
          appearance={{
            elements: {
              avatarBox: "h-9 w-9 border border-line-strong rounded-full",
            },
          }}
        />
      </div>
    </header>
  );
}

type IconLinkProps = {
  href: "/case" | "/patient";
  label: string;
  icon: ReactNode;
  isActive: boolean;
};

function IconLink({ href, label, icon, isActive }: IconLinkProps) {
  return (
    <Link
      href={href}
      aria-label={label}
      aria-current={isActive ? "page" : undefined}
      className={[
        "inline-flex items-center justify-center h-10 w-10 transition-colors",
        isActive
          ? "text-indigo border-b-2 border-indigo -mb-[1px]"
          : "text-ink-muted hover:text-ink",
      ].join(" ")}
    >
      {icon}
    </Link>
  );
}
```

Notes on the code:

- The `-mb-[1px]` on the active state pulls the underline down so it visually merges with the masthead's own `border-b border-line` (the masthead's border is 1px). This makes the active state read like a tabbed plate.
- The icon row uses `sm:hidden` and the text back-link uses `hidden sm:inline` so the two are mutually exclusive at every breakpoint.
- `IconLink` is a private helper kept in the same file; it is small enough that splitting it into its own file would harm cohesion.

- [ ] **Step 2: Type-check**

Run:
```bash
cd apps/web && npx tsc --noEmit
```
Expected: no output (success). If you see "Cannot find module 'lucide-react'" the import path is wrong — should be `from "lucide-react"` exactly.

- [ ] **Step 3: Lint**

Run:
```bash
cd apps/web && pnpm lint
```
Expected: no output, or warnings only for files you didn't touch.

- [ ] **Step 4: Commit**

```bash
git add apps/web/components/nav/MastheadNav.tsx
git commit -m "feat(web): add shared MastheadNav with mobile-portrait icon nav"
```

---

## Task 2: Migrate `/case/page.tsx` to use `MastheadNav`

**Files:**
- Modify: `apps/web/app/case/page.tsx:14-48` (the inline `<header>` block)

- [ ] **Step 1: Apply the edit**

In `apps/web/app/case/page.tsx`:

1. Remove the `UserButton` import:
   - Delete: `import { UserButton } from "@clerk/nextjs";`
2. Add the new import directly after the existing `Link` import:
   - Add: `import { MastheadNav } from "@/components/nav/MastheadNav";`
3. Replace the entire `<header className="px-6 md:px-14 pt-7 pb-5 …"> … </header>` block (lines 14–48 in the current file) with:

```tsx
      <MastheadNav
        active="workspace"
        plateLabel={<>Workspace <span className="diamond" /> Plate XXIV</>}
        backLink={{ href: "/patient", label: <>Patient file →</> }}
        userIdSuffix={userId?.slice(-8) ?? "—"}
      />
```

Keep the rest of the file (`<main>` and `<footer>`) untouched. Use the Edit tool with `old_string` matching the full original `<header>` block to avoid accidental drift.

- [ ] **Step 2: Type-check**

Run:
```bash
cd apps/web && npx tsc --noEmit
```
Expected: no output.

- [ ] **Step 3: Lint**

Run:
```bash
cd apps/web && pnpm lint
```
Expected: no output.

- [ ] **Step 4: Commit**

```bash
git add apps/web/app/case/page.tsx
git commit -m "feat(web): use MastheadNav on /case"
```

---

## Task 3: Migrate `/patient/page.tsx` to use `MastheadNav`

**Files:**
- Modify: `apps/web/app/patient/page.tsx:13-44` (the inline `<header>` block)

- [ ] **Step 1: Apply the edit**

In `apps/web/app/patient/page.tsx`:

1. Remove the `UserButton` import:
   - Delete: `import { UserButton } from "@clerk/nextjs";`
2. Add the new import directly after the existing `Link` import:
   - Add: `import { MastheadNav } from "@/components/nav/MastheadNav";`
3. Replace the entire `<header className="px-6 md:px-14 pt-7 pb-5 …"> … </header>` block (lines 13–44 in the current file) with:

```tsx
      <MastheadNav
        active="patient"
        plateLabel={<>Patient file <span className="diamond" /> Plate XXV</>}
        backLink={{ href: "/case", label: <>← Workspace</> }}
        userIdSuffix={userId?.slice(-8) ?? "—"}
      />
```

Keep the rest of the file (`<main>` and `<footer>`) untouched.

- [ ] **Step 2: Type-check**

Run:
```bash
cd apps/web && npx tsc --noEmit
```
Expected: no output.

- [ ] **Step 3: Lint**

Run:
```bash
cd apps/web && pnpm lint
```
Expected: no output.

- [ ] **Step 4: Commit**

```bash
git add apps/web/app/patient/page.tsx
git commit -m "feat(web): use MastheadNav on /patient"
```

---

## Task 4: Migrate `/patient/consultations/[consultationId]/page.tsx` to use `MastheadNav`

**Files:**
- Modify: `apps/web/app/patient/consultations/[consultationId]/page.tsx:15-44` (the inline `<header>` block)

- [ ] **Step 1: Apply the edit**

In `apps/web/app/patient/consultations/[consultationId]/page.tsx`:

1. Remove the `UserButton` import:
   - Delete: `import { UserButton } from "@clerk/nextjs";`
2. Add the new import directly after the existing `Link` import:
   - Add: `import { MastheadNav } from "@/components/nav/MastheadNav";`
3. Replace the entire `<header className="px-6 md:px-14 pt-7 pb-5 …"> … </header>` block (lines 15–44 in the current file) with:

```tsx
      <MastheadNav
        active="patient"
        plateLabel={<>Consultation <span className="diamond" /> full record</>}
        backLink={{ href: "/patient", label: <>← Patient file</> }}
        userIdSuffix={userId?.slice(-8) ?? "—"}
      />
```

Keep the rest of the file (`<main>` and `<footer>`) untouched. The in-footer `<Link href="/patient">← Return to the file</Link>` stays as-is — it is not part of the masthead.

- [ ] **Step 2: Type-check**

Run:
```bash
cd apps/web && npx tsc --noEmit
```
Expected: no output.

- [ ] **Step 3: Lint**

Run:
```bash
cd apps/web && pnpm lint
```
Expected: no output.

- [ ] **Step 4: Commit**

```bash
git add "apps/web/app/patient/consultations/[consultationId]/page.tsx"
git commit -m "feat(web): use MastheadNav on consultation detail"
```

---

## Task 5: Manual viewport verification

This task is purely visual / interactive. No file changes, no commit.

**Files:** none modified.

- [ ] **Step 1: Start the dev server**

Run:
```bash
cd apps/web && pnpm dev
```
Wait for the line `Ready in <n>ms`. The app will be at `http://localhost:3000`.

- [ ] **Step 2: Sign in**

Open `http://localhost:3000/case` in a browser. Sign in via Clerk if not already.

- [ ] **Step 3: Portrait-phone check at 375px (smallest common width)**

In the browser devtools, switch to responsive mode and set the viewport to **375 × 812** (iPhone SE / iPhone 12 mini).

Visit each of:
- `/case`
- `/patient`
- `/patient/consultations/<any-existing-id>` (open one from `/patient`)

For each page confirm **all** of:
- The masthead shows: brand mark, then two icon buttons (Workspace + Patient file), then the UserButton avatar. Nothing else.
- The icon for the current page has an indigo color and an indigo underline that sits flush with the masthead's bottom border.
- The other icon has muted ink color, no underline.
- Tapping the non-active icon navigates correctly.
- Tapping the active icon either does nothing visible or refreshes the same route (acceptable).
- No layout overflow — the masthead row fits within 375px without horizontal scroll.

- [ ] **Step 4: Portrait-phone check at 393px and 430px**

Repeat Step 3 at **393 × 852** (iPhone 14 Pro) and **430 × 932** (iPhone 14 Pro Max). Behavior should be identical.

- [ ] **Step 5: `sm` breakpoint check at 640px**

Set viewport to **640 × 800**. For each of the three signed-in pages, confirm:
- The icon row is gone.
- The text back-link (`Patient file →` on /case, `← Workspace` on /patient, `← Patient file` on /consultations/[id]) is visible.
- The plate label (`Workspace ◆ Plate XXIV`, etc.) is visible next to the brand.
- The `Attending ◆ …` tag is still hidden (it only appears at `md+`).

- [ ] **Step 6: `md` breakpoint check at 768px**

Set viewport to **768 × 1024**. Confirm:
- Everything from Step 5 still holds.
- The `Attending ◆ <last-8-of-userId>` tag is now visible.

- [ ] **Step 7: Accessibility spot-check**

In devtools, inspect each icon link. Confirm:
- The active link has `aria-current="page"`.
- The inactive link does not have `aria-current`.
- Each has `aria-label="Workspace"` or `aria-label="Patient file"` respectively.

If a screen reader is available, run VoiceOver / NVDA across the masthead at 375px and confirm announcement order is: brand → Workspace (link) → Patient file (link, current page) → account menu.

- [ ] **Step 8: Stop the dev server**

`Ctrl-C` in the dev-server terminal.

No commit for this task — verification only.

---

## Done criteria

- All four code commits land on the branch.
- `cd apps/web && npx tsc --noEmit` and `cd apps/web && pnpm lint` both pass.
- Task 5 viewport checks all pass on the three signed-in pages.
- `git log` shows four contiguous commits: one for the component, three for the page migrations.
