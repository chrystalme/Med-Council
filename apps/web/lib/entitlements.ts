"use client";

import { useAuth, useSession } from "@clerk/nextjs";
import { useCallback, useEffect, useState } from "react";
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
export function useIsPro(): { isPro: boolean; refresh: () => Promise<void> } {
  const { has, isLoaded, isSignedIn, getToken } = useAuth();
  const { session } = useSession();
  const [serverPlan, setServerPlan] = useState<"free" | "pro" | null>(null);

  const fetchPlan = useCallback(
    async (force = false) => {
      if (!isSignedIn) {
        setServerPlan(null);
        return;
      }
      try {
        // Force a fresh session token — on an upgrade the old token lacks
        // the new plan claim, and the Clerk Admin API cache on the server
        // may also be stale. `session.touch()` + `getToken({skipCache:true})`
        // refreshes the session so the next /api/me call reflects reality.
        if (force && session) {
          try {
            await session.touch?.();
          } catch {
            /* ignore */
          }
        }
        const tok = await getToken({ skipCache: force }).catch(() => null);
        const url = force ? "/api/me?refresh=1" : "/api/me";
        const data = await councilJson<MeResponse>(url, {
          method: "GET",
          token: tok,
        });
        setServerPlan(data.plan);
      } catch {
        /* keep previous state — fall through to client-side has() */
      }
    },
    [getToken, isSignedIn, session]
  );

  useEffect(() => {
    if (!isLoaded) return;
    void fetchPlan(false);
    // Re-fetch every 30s while mounted so an upgrade propagates without
    // requiring a manual refresh. Cheap: just one /api/me call.
    const t = setInterval(() => void fetchPlan(false), 30_000);
    return () => clearInterval(t);
  }, [isLoaded, fetchPlan]);

  // Also re-fetch whenever the window gains focus — users typically return
  // from the Clerk billing portal via a new tab, and focusing the app tab
  // is the natural moment to rehydrate their plan.
  useEffect(() => {
    const onFocus = () => void fetchPlan(true);
    if (typeof window !== "undefined") {
      window.addEventListener("focus", onFocus);
      return () => window.removeEventListener("focus", onFocus);
    }
  }, [fetchPlan]);

  const refresh = useCallback(() => fetchPlan(true), [fetchPlan]);

  let isPro = false;
  if (DEV_FORCE_PRO) {
    isPro = true;
  } else if (serverPlan === "pro") {
    isPro = true;
  } else if (serverPlan === "free") {
    isPro = false;
  } else if (isLoaded) {
    // Server hasn't answered yet — Clerk client helper as a last resort.
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
        if (has?.({ plan: slug })) {
          isPro = true;
          break;
        }
      }
    } catch {
      /* Billing not enabled in client SDK */
    }
  }

  return { isPro, refresh };
}
