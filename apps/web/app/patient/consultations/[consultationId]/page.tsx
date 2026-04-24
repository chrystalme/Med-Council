import { auth } from "@clerk/nextjs/server";
import { UserButton } from "@clerk/nextjs";
import Link from "next/link";
import { ConsultationDetail } from "@/components/case/ConsultationDetail";

export default async function ConsultationDetailPage({
  params,
}: {
  params: Promise<{ consultationId: string }>;
}) {
  const { consultationId } = await params;
  const { userId } = await auth();

  return (
    <div className="min-h-screen flex flex-col">
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
          <span className="mono-label hidden sm:inline">
            Consultation <span className="diamond" /> full record
          </span>
        </Link>
        <div className="flex items-center gap-5">
          <Link href="/patient" className="mono-label hover:text-indigo transition-colors hidden sm:inline">
            ← Patient file
          </Link>
          <span className="mono-label hidden md:inline">
            Attending <span className="diamond" /> {userId?.slice(-8) ?? "—"}
          </span>
          <UserButton
            appearance={{
              elements: { avatarBox: "h-9 w-9 border border-line-strong rounded-full" },
            }}
          />
        </div>
      </header>

      <main className="flex-1 px-6 md:px-14 py-10 md:py-14">
        <div className="max-w-5xl w-full mx-auto">
          <ConsultationDetail consultationId={consultationId} />
        </div>
      </main>

      <footer className="px-6 md:px-14 py-6 flex flex-wrap items-center justify-between gap-3 border-t border-line mono-label">
        <Link href="/patient" className="hover:text-indigo transition-colors inline-flex items-center gap-2">
          <span aria-hidden>←</span> Return to the file
        </Link>
        <span>MedAI Council <span className="diamond" /> detail</span>
      </footer>
    </div>
  );
}
