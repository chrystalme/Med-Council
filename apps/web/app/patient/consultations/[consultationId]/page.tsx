import { auth } from "@clerk/nextjs/server";
import Link from "next/link";
import { MastheadNav } from "@/components/nav/MastheadNav";
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
      <MastheadNav
        active="patient"
        plateLabel={<>Consultation <span className="diamond" /> full record</>}
        backLink={{ href: "/patient", label: <>← Patient file</> }}
        userIdSuffix={userId?.slice(-8) ?? "—"}
      />

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
