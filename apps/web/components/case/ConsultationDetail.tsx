"use client";

import { useAuth } from "@clerk/nextjs";
import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { councilJson } from "@/lib/council-api";
import { Markdown } from "./Markdown";
import { ConsensusView } from "./ConsensusView";

type CaseState = {
  step?: number;
  symptoms?: string;
  fqLines?: string[];
  fqAnswers?: string[];
  councilRoster?: Array<{ id?: string; name?: string; specialty?: string }>;
  physicians?: Array<{
    id?: string;
    name?: string;
    specialty?: string;
    assessment?: string;
  }>;
  research?: Array<Record<string, unknown>>;
  consensus?: Record<string, unknown> | null;
  plan?: string;
  message?: string;
};

type ConsultationDetailResponse = {
  id: string;
  case_id: string;
  case_title: string | null;
  summary: string;
  primary_dx: string | null;
  icd_code: string | null;
  urgency: string | null;
  confidence: number | null;
  created_at: string;
  case_state: CaseState;
};

const TABS = [
  { key: "intake", numeral: "I", label: "Intake" },
  { key: "followup", numeral: "II", label: "Follow-up" },
  { key: "council", numeral: "III", label: "Council" },
  { key: "research", numeral: "IV", label: "Research" },
  { key: "consensus", numeral: "V", label: "Consensus" },
  { key: "plan", numeral: "VI", label: "Plan" },
  { key: "message", numeral: "VII", label: "Message" },
] as const;

type TabKey = (typeof TABS)[number]["key"];

function availabilityFor(state: CaseState, key: TabKey): boolean {
  switch (key) {
    case "intake":    return !!state.symptoms;
    case "followup":  return Array.isArray(state.fqLines) && state.fqLines.length > 0;
    case "council":   return Array.isArray(state.physicians) && state.physicians.length > 0;
    case "research":  return Array.isArray(state.research) && state.research.length > 0;
    case "consensus": return !!state.consensus;
    case "plan":      return typeof state.plan === "string" && state.plan.trim().length > 0;
    case "message":   return typeof state.message === "string" && state.message.trim().length > 0;
  }
}

export function ConsultationDetail({ consultationId }: { consultationId: string }) {
  const { getToken, isLoaded, isSignedIn } = useAuth();
  const [data, setData] = useState<ConsultationDetailResponse | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState<TabKey>("intake");

  const load = useCallback(async () => {
    if (!isSignedIn) return;
    setLoading(true);
    try {
      const tok = await getToken().catch(() => null);
      const row = await councilJson<ConsultationDetailResponse>(
        `/api/patient/consultations/${consultationId}`,
        { method: "GET", token: tok }
      );
      setData(row);
      setErr(null);
      // Jump to the most advanced tab that has data, so the first view feels complete.
      for (const t of [...TABS].reverse()) {
        if (availabilityFor(row.case_state, t.key)) {
          setTab(t.key);
          break;
        }
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Failed to load consultation.");
    } finally {
      setLoading(false);
    }
  }, [consultationId, getToken, isSignedIn]);

  useEffect(() => {
    if (isLoaded) void load();
  }, [isLoaded, load]);

  const state: CaseState = useMemo(() => data?.case_state ?? {}, [data]);

  if (!isLoaded) return null;
  if (!isSignedIn) {
    return (
      <div className="plate-card p-8">
        <p className="mono-label">Sign in to view this consultation.</p>
      </div>
    );
  }
  if (loading) {
    return (
      <div className="plate-card p-8 space-y-3">
        <div className="stage-progress" aria-hidden />
        <p className="mono-label atlas-pulse">loading</p>
      </div>
    );
  }
  if (err || !data) {
    return (
      <div className="plate-card p-8">
        <p className="mono-label text-urgent mb-1">Fault</p>
        <p className="text-[15px] text-ink">{err ?? "Consultation not found."}</p>
        <div className="mt-4">
          <Link href="/patient" className="mono-label hover:text-indigo transition-colors">
            ← back to file
          </Link>
        </div>
      </div>
    );
  }

  const createdAt = new Date(data.created_at).toLocaleString(undefined, {
    year: "numeric", month: "short", day: "numeric",
    hour: "numeric", minute: "2-digit",
  });

  return (
    <div className="space-y-6">
      {/* Header strip */}
      <header className="plate-card p-6 md:p-8">
        <div className="flex items-baseline justify-between flex-wrap gap-3 mb-3">
          <p className="mono-label">{createdAt}</p>
          {data.urgency && (
            <span className="mono-label px-2 py-0.5 rounded-full bg-cornflower-soft text-ink-muted">
              {data.urgency}
            </span>
          )}
        </div>
        <h1 className="font-display text-[clamp(1.75rem,3.5vw,2.5rem)] leading-[1.05] text-ink mb-2">
          {data.primary_dx || "Unspecified diagnosis"}
        </h1>
        {data.icd_code && (
          <p className="mono-label text-ink-muted">
            ICD-10 <span className="diamond" /> <span className="font-mono text-ink">{data.icd_code}</span>
          </p>
        )}
        {typeof data.confidence === "number" && (
          <div className="flex items-center gap-2 mt-4 max-w-md">
            <div className="relative h-1.5 flex-1 rounded-full bg-periwinkle-soft overflow-hidden">
              <div
                className="absolute inset-y-0 left-0 rounded-full bg-cornflower"
                style={{ width: `${Math.max(0, Math.min(100, data.confidence))}%` }}
              />
            </div>
            <span className="mono-label tabular-nums">{data.confidence}% confidence</span>
          </div>
        )}
      </header>

      {/* Tab strip */}
      <nav className="flex flex-wrap gap-2" aria-label="Stages">
        {TABS.map((t) => {
          const available = availabilityFor(state, t.key);
          const active = tab === t.key;
          return (
            <button
              key={t.key}
              type="button"
              onClick={() => available && setTab(t.key)}
              disabled={!available}
              aria-current={active ? "page" : undefined}
              className={[
                "inline-flex items-baseline gap-2 rounded-full px-4 py-1.5 text-[13px] border transition-colors",
                active
                  ? "bg-indigo text-paper border-indigo"
                  : available
                    ? "bg-surface text-ink-muted border-line-strong hover:border-indigo hover:text-indigo"
                    : "bg-paper-deep/60 text-ink-faint border-line",
              ].join(" ")}
            >
              <span className="mono-label tabular-nums">{t.numeral}</span>
              <span>{t.label}</span>
            </button>
          );
        })}
      </nav>

      {/* Tab panel */}
      <section className="plate-card p-6 md:p-8 min-h-[220px]">
        {tab === "intake" && (
          <Panel heading="Presenting symptoms" empty="No symptoms recorded.">
            {state.symptoms ? (
              <p className="text-[15px] text-ink leading-relaxed whitespace-pre-wrap">
                {state.symptoms}
              </p>
            ) : null}
          </Panel>
        )}

        {tab === "followup" && (
          <Panel heading="Clarifying questions" empty="No follow-up recorded.">
            {Array.isArray(state.fqLines) && state.fqLines.length > 0 ? (
              <ol className="space-y-5">
                {state.fqLines.map((q, i) => (
                  <li key={i} className="space-y-1 pl-5 border-l border-line">
                    <p className="mono-label text-ink-muted">Q{String(i + 1).padStart(2, "0")}</p>
                    <p className="font-display text-[1.05rem] text-ink leading-snug">{q}</p>
                    <p className="text-[14px] text-ink-slate leading-relaxed whitespace-pre-wrap">
                      {(state.fqAnswers?.[i] ?? "").trim() || <em className="text-ink-faint">no answer</em>}
                    </p>
                  </li>
                ))}
              </ol>
            ) : null}
          </Panel>
        )}

        {tab === "council" && (
          <Panel heading="Specialist assessments" empty="No specialist assessments.">
            {Array.isArray(state.physicians) && state.physicians.length > 0 ? (
              <div className="space-y-5">
                {state.physicians.map((p, i) => (
                  <article key={p.id ?? i} className="border-t border-line pt-4 first:border-t-0 first:pt-0">
                    <p className="mono-label text-ink-muted mb-1">{p.specialty ?? "Specialist"}</p>
                    <h3 className="font-display text-[1.125rem] text-ink mb-2">{p.name ?? "—"}</h3>
                    <div className="text-[14.5px] text-ink-slate leading-relaxed whitespace-pre-wrap">
                      {p.assessment || <em className="text-ink-faint">no assessment</em>}
                    </div>
                  </article>
                ))}
              </div>
            ) : null}
          </Panel>
        )}

        {tab === "research" && (
          <Panel heading="Literature" empty="No references captured.">
            {Array.isArray(state.research) && state.research.length > 0 ? (
              <ul className="space-y-3">
                {state.research.map((p, i) => {
                  const title = String((p as Record<string, unknown>)?.title ?? "Untitled");
                  const url = String((p as Record<string, unknown>)?.url ?? "");
                  const year = (p as Record<string, unknown>)?.year;
                  const summary = String((p as Record<string, unknown>)?.summary ?? "");
                  return (
                    <li key={i} className="border-t border-line pt-3 first:border-t-0 first:pt-0">
                      {url ? (
                        <a
                          href={url}
                          target="_blank"
                          rel="noreferrer"
                          className="font-display text-[1.0625rem] text-ink-muted hover:text-indigo"
                        >
                          {title}
                        </a>
                      ) : (
                        <p className="font-display text-[1.0625rem] text-ink-muted">{title}</p>
                      )}
                      {year != null && <p className="mono-label text-ink-faint">{String(year)}</p>}
                      {summary && (
                        <p className="text-[14px] text-ink-slate mt-1 leading-relaxed">{summary}</p>
                      )}
                    </li>
                  );
                })}
              </ul>
            ) : null}
          </Panel>
        )}

        {tab === "consensus" && (
          <Panel heading="Consensus diagnosis" empty="No consensus recorded.">
            {state.consensus ? (
              <ConsensusView consensus={state.consensus as Record<string, unknown>} />
            ) : null}
          </Panel>
        )}

        {tab === "plan" && (
          <Panel heading="Coordinated plan" empty="No plan written.">
            {state.plan ? <Markdown>{state.plan}</Markdown> : null}
          </Panel>
        )}

        {tab === "message" && (
          <Panel heading="Patient message" empty="No patient-facing message.">
            {state.message ? <Markdown>{state.message}</Markdown> : null}
          </Panel>
        )}
      </section>
    </div>
  );
}

function Panel({
  heading,
  empty,
  children,
}: {
  heading: string;
  empty: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <h2 className="font-display text-[1.25rem] text-ink mb-4">{heading}</h2>
      {children ?? <p className="text-[15px] text-ink-faint italic">{empty}</p>}
    </div>
  );
}
