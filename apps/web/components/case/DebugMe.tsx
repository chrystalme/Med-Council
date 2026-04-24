"use client";

import { useAuth, useSession } from "@clerk/nextjs";
import { useCallback, useEffect, useState } from "react";
import { councilJson } from "@/lib/council-api";
import {
  CLERK_PRO_PLAN_SLUGS,
  clerkPlanSlugChecks,
  computeWorkspaceIsPro,
} from "@/lib/entitlements";

type MeDebug = {
  user_id: string | null;
  email: string | null;
  plan: "free" | "pro";
  debug?: {
    jwt_plan_from_claims: "free" | "pro" | null;
    clerk_api_plan: "free" | "pro";
    raw_claims: Record<string, unknown>;
    clerk_jwt_verification_enabled?: boolean;
    plan_from_unverified_jwt_claims?: "free" | "pro" | null;
    fix_hint?: string;
  };
};

const DEV_FORCE_PRO =
  typeof process !== "undefined" &&
  process.env.NEXT_PUBLIC_DEV_FORCE_PRO === "1";

export function DebugMe() {
  const { isLoaded, isSignedIn, getToken, has } = useAuth();
  const { session } = useSession();
  const [data, setData] = useState<MeDebug | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [tokenPreview, setTokenPreview] = useState<string | null>(null);

  const run = useCallback(
    async (force = false) => {
      setLoading(true);
      setErr(null);
      try {
        if (force && session) {
          try {
            await session.touch?.();
          } catch {
            /* ignore */
          }
        }
        const tok = await getToken({ skipCache: force }).catch(() => null);
        setTokenPreview(tok ? `${tok.slice(0, 16)}…${tok.slice(-8)}` : "(none)");
        const url = force ? "/api/me?debug=1&refresh=1" : "/api/me?debug=1";
        const res = await councilJson<MeDebug>(url, {
          method: "GET",
          token: tok,
        });
        setData(res);
      } catch (e) {
        setErr(e instanceof Error ? e.message : "Fetch failed");
      } finally {
        setLoading(false);
      }
    },
    [getToken, session]
  );

  useEffect(() => {
    // On-mount fetch once auth is ready — idiomatic useEffect usage.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (isLoaded && isSignedIn) void run(false);
  }, [isLoaded, isSignedIn, run]);

  // Pure derivations — must run before any conditional return (Rules of Hooks).
  const slugChecks = clerkPlanSlugChecks(has);
  const workspaceIsPro = computeWorkspaceIsPro({
    devForcePro: DEV_FORCE_PRO,
    apiPlan: data?.plan,
    has,
    isLoaded,
  });

  if (!isLoaded) {
    return <p className="mono-label atlas-pulse">loading Clerk…</p>;
  }
  if (!isSignedIn) {
    return (
      <p className="mono-label">
        You are not signed in. Sign in on the landing page first, then come
        back here.
      </p>
    );
  }

  const jwt = data?.debug?.jwt_plan_from_claims;
  const api = data?.debug?.clerk_api_plan;
  const resolved = data?.plan;
  const verifyOn = data?.debug?.clerk_jwt_verification_enabled;
  const unverifiedPlan = data?.debug?.plan_from_unverified_jwt_claims;
  const fixHint = data?.debug?.fix_hint;

  return (
    <div className="space-y-5">
      {fixHint && (
        <div className="rounded-xl border border-urgent/40 bg-urgent-soft px-4 py-3 text-[14px] text-ink leading-relaxed">
          <p className="mono-label text-urgent mb-1">Action</p>
          <p>{fixHint}</p>
        </div>
      )}
      <div className="flex gap-3">
        <button
          type="button"
          onClick={() => void run(false)}
          disabled={loading}
          className="btn-ghost h-10"
        >
          Refetch
        </button>
        <button
          type="button"
          onClick={() => void run(true)}
          disabled={loading}
          className="btn-indigo h-10"
        >
          Force refresh (new JWT + bust cache)
        </button>
      </div>

      <dl className="grid grid-cols-[160px_1fr] gap-y-2 gap-x-4 text-[14px]">
        <dt className="mono-label">user_id</dt>
        <dd className="font-mono text-ink">{data?.user_id ?? "—"}</dd>
        <dt className="mono-label">email</dt>
        <dd className="font-mono text-ink">{data?.email ?? "—"}</dd>
        <dt className="mono-label">resolved plan</dt>
        <dd>
          <span
            className={[
              "inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 border text-[13px]",
              resolved === "pro"
                ? "border-indigo bg-indigo-soft text-ink-muted"
                : "border-line-strong bg-periwinkle-soft text-ink-muted",
            ].join(" ")}
          >
            {resolved ?? "—"}
          </span>
        </dd>
        <dt className="mono-label">API verifies Clerk JWT</dt>
        <dd className="font-mono text-ink">
          {verifyOn === undefined
            ? "—"
            : verifyOn
              ? "yes (CLERK_ISSUER set)"
              : "no — set CLERK_ISSUER in apps/api/.env to match JWT iss"}
        </dd>
        <dt className="mono-label">plan from token (decode only)</dt>
        <dd className="font-mono text-ink">
          {unverifiedPlan ?? "—"}{" "}
          <span className="text-ink-slate text-[13px] font-sans">
            (what the API would resolve if verification succeeded and claims match)
          </span>
        </dd>
        <dt className="mono-label">jwt plan</dt>
        <dd className="font-mono text-ink">{String(jwt ?? "null")}</dd>
        <dt className="mono-label">admin-api plan</dt>
        <dd className="font-mono text-ink">{api ?? "—"}</dd>
        <dt className="mono-label">NEXT_PUBLIC_DEV_FORCE_PRO</dt>
        <dd className="font-mono text-ink">{DEV_FORCE_PRO ? "1 (UI forced Pro)" : "off"}</dd>
        <dt className="mono-label">workspace UI isPro</dt>
        <dd>
          <span
            className={[
              "inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 border text-[13px]",
              workspaceIsPro
                ? "border-indigo bg-indigo-soft text-ink-muted"
                : "border-line-strong bg-periwinkle-soft text-ink-muted",
            ].join(" ")}
          >
            {workspaceIsPro ? "true" : "false"}
          </span>
          <span className="text-ink-slate text-[13px] ml-2">
            (same merge as <code className="font-mono text-[12px]">useIsPro</code>: dev env OR
            resolved API plan OR Clerk <code className="font-mono text-[12px]">has(plan)</code>)
          </span>
        </dd>
        <dt className="mono-label">bearer token</dt>
        <dd className="font-mono text-ink-slate truncate">{tokenPreview ?? "—"}</dd>
      </dl>

      {err && (
        <p className="text-[13px] text-urgent bg-urgent-soft border border-urgent/30 rounded-lg px-3 py-2">
          {err}
        </p>
      )}

      <div>
        <p className="mono-label mb-2">
          Clerk <code className="font-mono text-[12px]">has(&#123; plan &#125;)</code> by slug (
          {CLERK_PRO_PLAN_SLUGS.length} configured)
        </p>
        <p className="text-[13px] text-ink-slate mb-2">
          If your Dashboard uses a different slug, add it to{" "}
          <code className="font-mono text-[12px]">CLERK_PRO_PLAN_SLUGS</code> in{" "}
          <code className="font-mono text-[12px]">lib/entitlements.ts</code>.
        </p>
        <div className="rounded-xl border border-line-strong overflow-hidden">
          <table className="w-full text-left text-[13px]">
            <thead className="bg-periwinkle-soft mono-label text-ink-muted">
              <tr>
                <th className="px-3 py-2 font-normal">slug</th>
                <th className="px-3 py-2 font-normal">has(&#123; plan &#125;)</th>
              </tr>
            </thead>
            <tbody>
              {slugChecks.map((row) => (
                <tr key={row.slug} className="border-t border-line-strong">
                  <td className="px-3 py-1.5 font-mono">{row.slug}</td>
                  <td className="px-3 py-1.5 font-mono">{row.active ? "true" : "false"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {data?.debug && (
        <div>
          <p className="mono-label mb-2">Raw JWT claims</p>
          <pre className="whitespace-pre-wrap rounded-xl border border-line bg-paper-deep p-4 text-[12px] leading-relaxed text-ink-slate overflow-x-auto font-mono">
            {JSON.stringify(data.debug.raw_claims, null, 2)}
          </pre>
        </div>
      )}

      <div className="rounded-xl border border-line bg-surface-tint p-4 text-[13.5px] text-ink-slate space-y-1.5">
        <p className="mono-label text-ink-muted">How to read this</p>
        <p>
          <strong>user_id is — and API verifies JWT = no</strong> — FastAPI is
          ignoring your Bearer token. Set{" "}
          <code className="font-mono text-[12px]">CLERK_ISSUER</code> in{" "}
          <code className="font-mono text-[12px]">apps/api/.env</code> to the same
          value as <code className="font-mono text-[12px]">iss</code> in raw claims
          (e.g. <code className="font-mono text-[12px]">https://…clerk.accounts.dev</code>
          ). Restart <code className="font-mono text-[12px]">api:dev</code>.
        </p>
        <p>
          <strong>workspace UI isPro = true</strong> matches the case workspace
          (models, paywall chrome, voice, etc.). It is true if dev-force is on,
          <strong> or </strong>
          <strong>resolved=pro</strong>, <strong> or </strong> any Clerk slug
          row is <code>true</code> (even when <strong>resolved=free</strong>).
        </p>
        <p>
          <strong>resolved=pro</strong> means the FastAPI session agrees — best
          for server-gated features (Whisper, etc.). If UI is Pro but API calls
          return 402, fix JWT template / <code>CLERK_SECRET_KEY</code> /{" "}
          <code>auth.py</code> plan detection.
        </p>
        <p>
          <strong>jwt plan=pro</strong> but <strong>resolved=free</strong> —
          shouldn&rsquo;t happen; report it.
        </p>
        <p>
          <strong>jwt plan=free</strong> + <strong>admin-api plan=pro</strong> —
          your JWT template doesn&rsquo;t emit plan claims, but the Clerk
          Admin API lookup saved us. Working as designed.
        </p>
        <p>
          <strong>both free, but you did subscribe</strong> — none of the
          Clerk Billing endpoints matched. Share the <code>raw_claims</code>{" "}
          above and the <code>plans[].slug</code> from{" "}
          <code>localStorage.__clerk_environment</code>.
        </p>
      </div>
    </div>
  );
}
