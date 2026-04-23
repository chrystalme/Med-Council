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

/** Clerk Billing plan slugs — extend if your Dashboard uses another slug. */
export const CLERK_PRO_PLAN_SLUGS = [
  "pro",
  "pro_plan",
  "pro_monthly",
  "pro_yearly",
  "medai_pro",
  "premium",
] as const;

/** Per-slug `has({ plan })` for dashboards and `/debug` billing checks. */
export function clerkPlanSlugChecks(
  has: ReturnType<typeof useAuth>["has"]
): { slug: string; active: boolean }[] {
  return CLERK_PRO_PLAN_SLUGS.map((slug) => {
    if (!has) return { slug, active: false };
    try {
      return { slug, active: Boolean(has({ plan: slug })) };
    } catch {
      return { slug, active: false };
    }
  });
}

/** True when Clerk Billing reports any configured Pro plan slug. */
export function clerkReportsPro(
  has: ReturnType<typeof useAuth>["has"],
  isLoaded: boolean
): boolean {
  if (!isLoaded || !has) return false;
  try {
    for (const slug of CLERK_PRO_PLAN_SLUGS) {
      if (has({ plan: slug })) return true;
    }
  } catch {
    /* Billing not enabled in client SDK */
  }
  return false;
}

/** Same boolean merge as `useIsPro()` — for `/debug` and tests. */
export function computeWorkspaceIsPro(input: {
  devForcePro: boolean;
  apiPlan: "free" | "pro" | null | undefined;
  has: ReturnType<typeof useAuth>["has"];
  isLoaded: boolean;
}): boolean {
  return (
    input.devForcePro ||
    input.apiPlan === "pro" ||
    clerkReportsPro(input.has, input.isLoaded)
  );
}

/**
 * `false` on the server and the first client render, then `true` after mount.
 * Use with {@link useIsPro} when branching **visible DOM** (classes, children,
 * `disabled`, etc.): Clerk can report Pro on the first client paint while SSR
 * still looked unpaid, which causes React hydration mismatches.
 */
export function useClientMounted(): boolean {
  const [mounted, setMounted] = useState(false);
  useEffect(() => {
    setMounted(true);
  }, []);
  return mounted;
}

/**
 * Pro for **client UI** (models picker, paywall chrome, voice mode, etc.).
 *
 * True if any of: dev env override, `/api/me` says `pro`, or Clerk Billing
 * `has({ plan })` matches a known Pro slug after `isLoaded`. The API and Clerk
 * are OR'd so a stale or mis-parsed `/api/me` that still says `free` does not
 * hide Pro when the Clerk session already reflects a paid plan. Server routes
 * still enforce their own `effective_plan()` — align JWT / `CLERK_SECRET_KEY`
 * there if you see 402s while the UI shows Pro.
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

  const isPro = computeWorkspaceIsPro({
    devForcePro: DEV_FORCE_PRO,
    apiPlan: serverPlan,
    has,
    isLoaded,
  });

  return { isPro, refresh };
}
