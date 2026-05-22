import { auth, currentUser } from "@clerk/nextjs/server";
import Link from "next/link";
import { MastheadNav } from "@/components/nav/MastheadNav";
import { CaseWorkspace } from "@/components/case/CaseWorkspace";

export default async function CaseWorkspacePage() {
  const { userId } = await auth();
  const user = await currentUser();

  const firstName = user?.firstName ?? "friend";

  return (
    <div className="min-h-screen flex flex-col">
      <MastheadNav
        active="workspace"
        plateLabel={<>Workspace <span className="diamond" /> Plate XXIV</>}
        backLink={{ href: "/patient", label: <>Patient file →</> }}
        userIdSuffix={userId?.slice(-8) ?? "—"}
      />

      {/* Main workspace */}
      <main className="flex-1 px-6 md:px-14 py-12 md:py-16 relative">
        <div className="max-w-6xl w-full mx-auto">
          {/* Page introduction with plate-style composition */}
          <div className="grid grid-cols-12 gap-6 mb-12">
            <div className="col-span-12 lg:col-span-8">
              <div className="flex items-center gap-3 mb-5">
                <span className="stage-marker">The Case File</span>
                <span className="h-px w-16 bg-line-strong" />
                <span className="plate-counter">open folio</span>
              </div>

              <h1 className="font-display text-[clamp(2rem,4.5vw,3.75rem)] leading-[1.02] text-ink mb-6 text-balance">
                Good to see you,&nbsp;
                <em className="italic text-indigo font-normal">{firstName}</em>.
              </h1>

              <p className="text-[1.0625rem] text-ink-slate max-w-[52ch] leading-relaxed text-pretty">
                Describe the symptoms, attach any test results, and the council
                will deliberate — triage, per-specialist assessment, literature
                scan, consensus, and a plan — all on this plate.
              </p>
            </div>

            {/* Colophon panel */}
            <aside className="col-span-12 lg:col-span-4 lg:pl-8 lg:border-l lg:border-line">
              <div className="flex items-center gap-2 mb-4">
                <span className="stage-marker">Colophon</span>
              </div>
              <dl className="space-y-3">
                <div className="flex items-baseline justify-between gap-4 pb-2 border-b border-line/70">
                  <dt className="mono-label">Council</dt>
                  <dd className="font-display text-[15px] text-ink">16 seats</dd>
                </div>
                <div className="flex items-baseline justify-between gap-4 pb-2 border-b border-line/70">
                  <dt className="mono-label">Stages</dt>
                  <dd className="font-display text-[15px] text-ink">VII plates</dd>
                </div>
                <div className="flex items-baseline justify-between gap-4">
                  <dt className="mono-label">State</dt>
                  <dd className="flex items-center gap-2 font-display text-[15px] text-ink">
                    <span className="h-1.5 w-1.5 rounded-full bg-cornflower atlas-pulse" />
                    live
                  </dd>
                </div>
              </dl>
            </aside>
          </div>

          {/* Workspace plate */}
          <div className="plate-card p-6 md:p-10 relative overflow-hidden">
            {/* Corner tick */}
            <span aria-hidden className="absolute top-4 left-4 plate-corner block h-3 w-3" />
            <span aria-hidden className="absolute top-4 right-4 mono-label text-ink-whisper">
              Plate XXIV · recto
            </span>

            <div className="pt-6">
              <CaseWorkspace />
            </div>
          </div>
        </div>
      </main>

      <footer className="px-6 md:px-14 py-6 flex flex-wrap items-center justify-between gap-3 border-t border-line mono-label">
        <Link
          href="/"
          className="hover:text-indigo transition-colors inline-flex items-center gap-2"
        >
          <span aria-hidden>←</span> Return to masthead
        </Link>
        <span>
          MedAI Council <span className="diamond" /> workspace
        </span>
      </footer>
    </div>
  );
}
