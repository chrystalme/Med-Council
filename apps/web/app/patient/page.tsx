import { auth, currentUser } from "@clerk/nextjs/server";
import Link from "next/link";
import { MastheadNav } from "@/components/nav/MastheadNav";
import { PatientFile } from "@/components/case/PatientFile";

export default async function PatientFilePage() {
  const { userId } = await auth();
  const user = await currentUser();
  const firstName = user?.firstName ?? "friend";

  return (
    <div className="min-h-screen flex flex-col">
      <MastheadNav
        active="patient"
        plateLabel={<>Patient file <span className="diamond" /> Plate XXV</>}
        backLink={{ href: "/case", label: <>← Workspace</> }}
        userIdSuffix={userId?.slice(-8) ?? "—"}
      />

      <main className="flex-1 px-6 md:px-14 py-12 md:py-16 relative">
        <div className="max-w-5xl w-full mx-auto">
          <div className="grid grid-cols-12 gap-6 mb-10">
            <div className="col-span-12 lg:col-span-8">
              <div className="flex items-center gap-3 mb-5">
                <span className="stage-marker">The file</span>
                <span className="h-px w-16 bg-line-strong" />
                <span className="plate-counter">prior consultations</span>
              </div>
              <h1 className="font-display text-[clamp(2rem,4.5vw,3.5rem)] leading-[1.02] text-ink mb-5 text-balance">
                {firstName}&rsquo;s file.
              </h1>
              <p className="text-[1.0625rem] text-ink-slate max-w-[52ch] leading-relaxed text-pretty">
                Every completed consultation is saved here and indexed for
                context-aware retrieval on your next visit. Delete any entry
                to remove it from both the record and the search index.
              </p>
            </div>
          </div>

          <PatientFile />
        </div>
      </main>

      <footer className="px-6 md:px-14 py-6 flex flex-wrap items-center justify-between gap-3 border-t border-line mono-label">
        <Link
          href="/case"
          className="hover:text-indigo transition-colors inline-flex items-center gap-2"
        >
          <span aria-hidden>←</span> Return to workspace
        </Link>
        <span>
          MedAI Council <span className="diamond" /> patient record
        </span>
      </footer>
    </div>
  );
}
