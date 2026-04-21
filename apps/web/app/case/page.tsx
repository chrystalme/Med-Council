import { auth, currentUser } from "@clerk/nextjs/server";
import { UserButton } from "@clerk/nextjs";
import Link from "next/link";

export default async function CaseWorkspacePage() {
  const { userId } = await auth();
  const user = await currentUser();

  const firstName = user?.firstName ?? "friend";

  return (
    <div className="min-h-screen flex flex-col">
      {/* Header */}
      <header className="px-8 md:px-14 pt-8 pb-6 flex items-center justify-between border-b border-line">
        <Link href="/" className="flex items-baseline gap-3">
          <span className="font-display text-xl tracking-tight">
            MedAI Council
          </span>
          <span className="mono-label hidden sm:inline">Workspace</span>
        </Link>
        <div className="flex items-center gap-4">
          <span className="mono-label hidden sm:inline">
            User · {userId?.slice(-8) ?? "—"}
          </span>
          <UserButton
            appearance={{
              elements: {
                avatarBox: "h-9 w-9 border border-line-strong",
              },
            }}
          />
        </div>
      </header>

      {/* Main workspace */}
      <main className="flex-1 px-8 md:px-14 py-14 max-w-5xl w-full">
        <p className="stage-marker mb-6">Workspace — the case file</p>

        <h1 className="font-display text-[clamp(2rem,4.5vw,3.75rem)] leading-[1.05] text-ink mb-8">
          Good to see you,&nbsp;
          <em className="italic text-clay">{firstName}</em>.
        </h1>

        <p className="text-lg text-ink-muted max-w-2xl leading-relaxed mb-12 text-pretty">
          The consultation workspace will land here in the next step. For now,
          you&rsquo;re signed in and the council is listening.
        </p>

        {/* Placeholder card — will become the intake form in step 2b */}
        <div className="bg-surface border border-line rounded-2xl p-8 md:p-10 shadow-[0_1px_2px_rgba(24,22,26,0.03),0_8px_24px_rgba(24,22,26,0.04)] max-w-3xl">
          <p className="stage-marker mb-4">Step 2b · intake</p>
          <h2 className="font-display text-2xl md:text-3xl mb-4 text-ink">
            Symptom intake &mdash; <span className="italic text-ink-muted">coming next</span>
          </h2>
          <p className="text-ink-muted leading-relaxed mb-6">
            The next commit brings the symptom intake form, follow-up
            questions, and triage. After that: council deliberation, research,
            consensus, plan, and the patient-facing message &mdash; each a
            separate editorial chapter.
          </p>
          <div className="rule-ornament mb-6">
            <span className="mono-label">checkpoint</span>
          </div>
          <ul className="space-y-2 text-[15px] text-ink-muted">
            <li className="flex items-baseline gap-3">
              <span className="mono-label text-clay">✓</span>
              <span>Next.js 16 + Tailwind v4 + Fraunces / Geist typography</span>
            </li>
            <li className="flex items-baseline gap-3">
              <span className="mono-label text-clay">✓</span>
              <span>Clerk authentication with protected <code className="font-mono text-[13px]">/case</code></span>
            </li>
            <li className="flex items-baseline gap-3">
              <span className="mono-label text-ink-faint">·</span>
              <span className="text-ink-faint">API client + FastAPI JWT verification (step 2b)</span>
            </li>
            <li className="flex items-baseline gap-3">
              <span className="mono-label text-ink-faint">·</span>
              <span className="text-ink-faint">Intake &amp; triage chapters (step 2b)</span>
            </li>
          </ul>
        </div>
      </main>

      <footer className="px-8 md:px-14 py-6 flex items-center justify-between border-t border-line mono-label">
        <Link href="/" className="hover:text-ink transition-colors">
          ← Return to masthead
        </Link>
        <span>MedAI Council · workspace</span>
      </footer>
    </div>
  );
}
