"use client";

import Link from "next/link";
import { useIsPro } from "@/lib/entitlements";

/**
 * Inline banner shown at the top of the workspace when the user is on the
 * Free tier and `NEXT_PUBLIC_FEATURE_PAYWALL=1`. Pro users see nothing.
 *
 * The real entitlement check reaches Clerk Billing via useIsPro(); the env
 * gate lets devs hide the banner while developing locally without signing
 * in as a Pro user.
 */
export function PaywallBanner() {
  const isPro = useIsPro();
  if (isPro) return null;
  if (process.env.NEXT_PUBLIC_FEATURE_PAYWALL !== "1") return null;

  return (
    <aside className="rounded-2xl border border-line-strong bg-periwinkle-soft px-5 py-4 text-[15px] text-ink leading-relaxed">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="flex items-center gap-2 mb-1.5">
            <span className="h-1.5 w-1.5 rounded-full bg-cornflower atlas-pulse" />
            <p className="mono-label text-ink-muted">Free tier <span className="diamond" /> limits apply</p>
          </div>
          <p className="text-ink-slate text-[14.5px]">
            Free tier: Nemotron model, browser voice, 4 saved consultations,
            5 test attachments per case (1 MB each). Upgrade for premium models,
            Whisper voice, unlimited memory, and 10 MB × 20 attachments.
          </p>
        </div>
        <Link href="/#pricing" className="btn-indigo shrink-0">
          Upgrade to Pro →
        </Link>
      </div>
    </aside>
  );
}
