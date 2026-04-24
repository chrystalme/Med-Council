"use client";

import { useAuth } from "@clerk/nextjs";
import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { councilJson } from "@/lib/council-api";

type Consultation = {
  id: string;
  case_id: string;
  summary: string;
  primary_dx: string | null;
  icd_code: string | null;
  urgency: string | null;
  confidence: number | null;
  created_at: string;
};

type ListResponse = {
  plan: "free" | "pro";
  cap: number | null;
  consultations: Consultation[];
};

function urgencyTone(u: string | null): {
  label: string;
  tone: "indigo" | "cornflower" | "slate";
} {
  const v = (u ?? "").toLowerCase();
  if (v.includes("emerg")) return { label: "Emergent", tone: "indigo" };
  if (v.includes("urg")) return { label: "Urgent", tone: "cornflower" };
  return { label: u ? u[0].toUpperCase() + u.slice(1) : "Routine", tone: "slate" };
}

export function PatientFile() {
  const { getToken, isLoaded, isSignedIn } = useAuth();
  const [state, setState] = useState<ListResponse | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [deleting, setDeleting] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!isSignedIn) return;
    setLoading(true);
    try {
      const tok = await getToken().catch(() => null);
      const data = await councilJson<ListResponse>("/api/patient/consultations", {
        method: "GET",
        token: tok,
      });
      setState(data);
      setErr(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Failed to load consultations.");
    } finally {
      setLoading(false);
    }
  }, [getToken, isSignedIn]);

  useEffect(() => {
    if (isLoaded) void load();
  }, [isLoaded, load]);

  const onDelete = async (id: string) => {
    if (typeof window !== "undefined" && !window.confirm("Delete this consultation? This removes it from the vector index too.")) {
      return;
    }
    setDeleting(id);
    try {
      const tok = await getToken().catch(() => null);
      await councilJson(`/api/patient/consultations/${id}`, {
        method: "DELETE",
        token: tok,
      });
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Delete failed.");
    } finally {
      setDeleting(null);
    }
  };

  if (!isLoaded) return null;

  if (!isSignedIn) {
    return (
      <div className="plate-card p-8">
        <p className="mono-label">Sign in to view the patient file.</p>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="plate-card p-8">
        <p className="mono-label atlas-pulse">loading</p>
      </div>
    );
  }

  if (err) {
    return (
      <div className="plate-card p-8">
        <p className="mono-label text-urgent mb-1">Fault</p>
        <p className="text-[15px] text-ink">{err}</p>
      </div>
    );
  }

  const consultations = state?.consultations ?? [];
  const plan = state?.plan ?? "free";
  const cap = state?.cap;
  const used = consultations.length;
  const remaining = cap != null ? Math.max(0, cap - used) : null;

  return (
    <div className="space-y-6">
      {/* Quota chip */}
      <div className="flex flex-wrap items-center gap-3">
        <span
          className={[
            "inline-flex items-center gap-2 rounded-full px-3 py-1 border text-[13px]",
            plan === "pro"
              ? "border-indigo bg-indigo-soft text-ink-muted"
              : "border-line-strong bg-periwinkle-soft text-ink-muted",
          ].join(" ")}
        >
          <span
            className={[
              "h-1.5 w-1.5 rounded-full",
              plan === "pro" ? "bg-indigo" : "bg-cornflower atlas-pulse",
            ].join(" ")}
          />
          {plan === "pro" ? "Pro · unlimited consultations" : `Free · ${used} of ${cap} consultations used`}
        </span>
        {plan !== "pro" && remaining === 0 && (
          <span className="text-[13px] text-urgent">
            Limit reached — delete one or upgrade to save another case.
          </span>
        )}
      </div>

      {consultations.length === 0 ? (
        <div className="plate-card p-8">
          <p className="mono-label mb-2">No consultations yet</p>
          <p className="text-[15px] text-ink-slate">
            Complete a case in the workspace. When the pipeline reaches the
            patient message stage, it&rsquo;s saved here automatically.
          </p>
        </div>
      ) : (
        <ol className="grid gap-4 md:grid-cols-2">
          {consultations.map((c) => {
            const u = urgencyTone(c.urgency);
            const dateFull = new Date(c.created_at).toLocaleDateString(undefined, {
              year: "numeric",
              month: "short",
              day: "numeric",
            });
            return (
              <li
                key={c.id}
                className="plate-card p-5 relative flex flex-col gap-3 hover:border-indigo/40 transition-colors"
              >
                <Link
                  href={`/patient/consultations/${c.id}`}
                  aria-label={`Open consultation — ${c.primary_dx || "Unspecified"} from ${dateFull}`}
                  className="absolute inset-0 rounded-[inherit] z-0 focus-visible:ring-2 focus-visible:ring-indigo"
                />
                <span
                  aria-hidden
                  className="plate-corner absolute top-3 left-3 h-3 w-3"
                />
                <div className="flex items-baseline justify-between gap-3 pt-4 relative z-10 pointer-events-none">
                  <p className="mono-label">{dateFull}</p>
                  <span
                    className={[
                      "mono-label px-2 py-0.5 rounded-full",
                      u.tone === "indigo"
                        ? "bg-indigo-soft text-ink-muted"
                        : u.tone === "cornflower"
                          ? "bg-cornflower-soft text-ink-muted"
                          : "bg-slate-soft text-ink-muted",
                    ].join(" ")}
                  >
                    {u.label}
                  </span>
                </div>
                <h2 className="font-display text-[1.25rem] leading-tight text-ink relative z-10 pointer-events-none">
                  {c.primary_dx || "Unspecified"}
                </h2>
                {c.icd_code && (
                  <p className="mono-label text-ink-muted relative z-10 pointer-events-none">
                    ICD-10 <span className="diamond" />{" "}
                    <span className="font-mono text-ink">{c.icd_code}</span>
                  </p>
                )}
                {typeof c.confidence === "number" && (
                  <div className="flex items-center gap-2 relative z-10 pointer-events-none">
                    <div className="relative h-1.5 flex-1 rounded-full bg-periwinkle-soft overflow-hidden">
                      <div
                        className="absolute inset-y-0 left-0 rounded-full bg-cornflower"
                        style={{
                          width: `${Math.max(0, Math.min(100, c.confidence))}%`,
                        }}
                      />
                    </div>
                    <span className="mono-label tabular-nums">
                      {c.confidence}%
                    </span>
                  </div>
                )}
                <p className="text-[14px] text-ink-slate leading-relaxed line-clamp-4 relative z-10 pointer-events-none">
                  {c.summary}
                </p>
                <div className="flex items-center justify-between gap-3 pt-1 mt-auto relative z-10">
                  <span className="mono-label text-indigo pointer-events-none">
                    open detail →
                  </span>
                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation();
                      e.preventDefault();
                      void onDelete(c.id);
                    }}
                    disabled={deleting === c.id}
                    className="mono-label text-ink-muted hover:text-urgent transition-colors disabled:opacity-50"
                  >
                    {deleting === c.id ? "deleting…" : "delete"}
                  </button>
                </div>
              </li>
            );
          })}
        </ol>
      )}
    </div>
  );
}
