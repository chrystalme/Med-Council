"use client";

import { useAuth } from "@clerk/nextjs";

/** Does the current Clerk user have the Pro plan entitlement? */
export function useIsPro(): boolean {
  const { has, isLoaded } = useAuth();
  if (!isLoaded) return false;
  // Clerk Billing surfaces plan entitlements via `has({ plan: "..." })`.
  // We accept both "pro" and "pro_monthly"/"pro_yearly" slugs as Pro.
  try {
    if (has?.({ plan: "pro" })) return true;
    if (has?.({ plan: "pro_monthly" })) return true;
    if (has?.({ plan: "pro_yearly" })) return true;
  } catch {
    /* older SDK / Billing not configured */
  }
  return false;
}
