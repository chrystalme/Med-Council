"use client";

/** Step 6 placeholder — real billing will use Clerk + Stripe. */
export function PaywallBanner() {
  if (process.env.NEXT_PUBLIC_FEATURE_PAYWALL !== "1") {
    return null;
  }
  return (
    <div className="mb-8 rounded-2xl border border-line-strong bg-periwinkle-soft px-5 py-4 text-[15px] text-ink leading-relaxed">
      <div className="flex items-center gap-2 mb-1.5">
        <span className="h-1.5 w-1.5 rounded-full bg-cornflower" />
        <p className="mono-label text-ink-muted">Paid tiers <span className="diamond" /> preview</p>
      </div>
      <p className="text-ink-slate">
        Step 6 will gate premium runs via Clerk and Stripe. This banner is
        non-blocking — set{" "}
        <code className="font-mono text-[13px] text-ink bg-surface px-1.5 py-0.5 rounded">
          NEXT_PUBLIC_FEATURE_PAYWALL=0
        </code>{" "}
        in{" "}
        <code className="font-mono text-[13px] text-ink bg-surface px-1.5 py-0.5 rounded">
          apps/web/.env.local
        </code>{" "}
        to hide it.
      </p>
    </div>
  );
}
