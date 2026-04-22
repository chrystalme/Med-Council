"use client";

import { useAuth } from "@clerk/nextjs";
import { useEffect, useState } from "react";
import { councilJson } from "@/lib/council-api";

const DEV_FORCE_PRO =
  typeof process !== "undefined" &&
  process.env.NEXT_PUBLIC_DEV_FORCE_PRO === "1";

type MeResponse = {
  user_id: string | null;
  email: string | null;
  plan: "free" | "pro";
};

/**
 * Single source of truth for Pro entitlement.
 *
 * The priority is:
 *   1. NEXT_PUBLIC_DEV_FORCE_PRO=1 (local dev override)
 *   2. /api/me server response — this uses the same effective_plan()
 *      logic the backend's paywall enforces, so client UI and server
 *      gates never disagree.
 *   3. Clerk's client-side has({plan}) helper as a fallback when the
 *      backend is unreachable.
 */
export function useIsPro(): boolean {
  const { has, isLoaded, isSignedIn, getToken } = useAuth();
  const [serverPlan, setServerPlan] = useState<"free" | "pro" | null>(null);

  useEffect(() => {
    if (!isLoaded || !isSignedIn) {
      setServerPlan(null);
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const tok = await getToken().catch(() => null);
        const data = await councilJson<MeResponse>("/api/me", {
          method: "GET",
          token: tok,
        });
        if (!cancelled) setServerPlan(data.plan);
      } catch {
        /* keep null — fall through to client-side has() */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [isLoaded, isSignedIn, getToken]);

  if (DEV_FORCE_PRO) return true;
  if (serverPlan === "pro") return true;
  if (serverPlan === "free") return false;

  // Server hasn't answered yet — try the Clerk client helper as a last
  // resort. This only works if the JWT template surfaces plan claims.
  if (!isLoaded) return false;
  try {
    const slugs = [
      "pro",
      "pro_plan",
      "pro_monthly",
      "pro_yearly",
      "medai_pro",
      "premium",
    ];
    for (const slug of slugs) {
      if (has?.({ plan: slug })) return true;
    }
  } catch {
    /* Billing not enabled in client SDK */
  }
  return false;
}
