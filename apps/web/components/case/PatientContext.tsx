"use client";

import { useAuth } from "@clerk/nextjs";
import { useCallback, useEffect, useState } from "react";
import { councilJson } from "@/lib/council-api";

type Hit = {
  id: string;
  score: number;
  metadata: Record<string, unknown>;
  document: string;
};

export function PatientContext({
  query,
  topK = 3,
}: {
  query: string;
  topK?: number;
}) {
  const { getToken, isSignedIn } = useAuth();
  const [hits, setHits] = useState<Hit[]>([]);
  const [loading, setLoading] = useState(false);

  const fetchHits = useCallback(async () => {
    if (!isSignedIn) return;
    if (query.trim().length < 20) {
      setHits([]);
      return;
    }
    setLoading(true);
    try {
      const tok = await getToken().catch(() => null);
      const data = await councilJson<{ hits: Hit[] }>("/api/patient/retrieve", {
        method: "POST",
        token: tok,
        body: JSON.stringify({ query, top_k: topK }),
      });
      setHits(data.hits ?? []);
    } catch {
      setHits([]);
    } finally {
      setLoading(false);
    }
  }, [getToken, isSignedIn, query, topK]);

  // Debounce: only fetch 800ms after the query stabilises.
  useEffect(() => {
    const t = setTimeout(() => void fetchHits(), 800);
    return () => clearTimeout(t);
  }, [fetchHits]);

  if (!isSignedIn) return null;
  if (hits.length === 0 && !loading) return null;

  return (
    <aside className="rounded-xl border border-line bg-periwinkle-soft/50 p-4 space-y-3">
      <div className="flex items-center justify-between">
        <p className="mono-label">
          Similar past consultations
          {loading && <span className="atlas-pulse"> · searching</span>}
          {!loading && hits.length > 0 && (
            <span> <span className="diamond" /> {hits.length} found</span>
          )}
        </p>
      </div>
      {hits.length > 0 && (
        <ul className="space-y-2">
          {hits.map((h) => {
            const date = String(h.metadata.created_at ?? "").slice(0, 10);
            const dx = String(h.metadata.primary_dx ?? "—");
            const urgency = String(h.metadata.urgency ?? "");
            const scorePct = Math.round(h.score * 100);
            const snippet = (h.document || "").slice(0, 280);
            return (
              <li
                key={h.id}
                className="rounded-lg bg-surface border border-line p-3 text-[14px]"
              >
                <div className="flex items-baseline justify-between gap-3 mb-1">
                  <span className="font-display text-ink">{dx}</span>
                  <span className="mono-label">
                    {date} <span className="diamond" /> {urgency || "—"}{" "}
                    <span className="diamond" /> match {scorePct}%
                  </span>
                </div>
                <p className="text-ink-slate leading-relaxed">
                  {snippet}
                  {snippet.length === 280 ? "…" : ""}
                </p>
              </li>
            );
          })}
        </ul>
      )}
    </aside>
  );
}
