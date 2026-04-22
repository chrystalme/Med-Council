"use client";

import { useAuth, useSession } from "@clerk/nextjs";
import { useCallback, useEffect, useState } from "react";
import { councilJson } from "@/lib/council-api";

type MeDebug = {
  user_id: string | null;
  email: string | null;
  plan: "free" | "pro";
  debug?: {
    jwt_plan_from_claims: "free" | "pro" | null;
    clerk_api_plan: "free" | "pro";
    raw_claims: Record<string, unknown>;
  };
};

export function DebugMe() {
  const { isLoaded, isSignedIn, getToken } = useAuth();
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
    if (isLoaded && isSignedIn) void run(false);
  }, [isLoaded, isSignedIn, run]);

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

  return (
    <div className="space-y-5">
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
        <dt className="mono-label">jwt plan</dt>
        <dd className="font-mono text-ink">{String(jwt ?? "null")}</dd>
        <dt className="mono-label">admin-api plan</dt>
        <dd className="font-mono text-ink">{api ?? "—"}</dd>
        <dt className="mono-label">bearer token</dt>
        <dd className="font-mono text-ink-slate truncate">{tokenPreview ?? "—"}</dd>
      </dl>

      {err && (
        <p className="text-[13px] text-urgent bg-urgent-soft border border-urgent/30 rounded-lg px-3 py-2">
          {err}
        </p>
      )}

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
          <strong>resolved=pro</strong> means the app treats you as Pro — every
          paywalled feature unlocks. You&rsquo;re done.
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
