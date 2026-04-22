"use client";

import { Markdown } from "./Markdown";

type Urgency = "routine" | "urgent" | "emergent" | (string & {});

function pickUrgency(value: unknown): Urgency {
  const v = String(value ?? "").toLowerCase().trim();
  if (v.includes("emerg")) return "emergent";
  if (v.includes("urg")) return "urgent";
  if (v) return "routine";
  return "routine";
}

const URGENCY_SCALE: Urgency[] = ["routine", "urgent", "emergent"];

const URGENCY_COPY: Record<string, { label: string; caption: string }> = {
  routine: { label: "Routine", caption: "scheduled follow-up · no immediate intervention" },
  urgent: { label: "Urgent", caption: "prompt medical review · same-day attention" },
  emergent: { label: "Emergent", caption: "immediate intervention · do not delay" },
};

export function ConsensusView({ consensus }: { consensus: Record<string, unknown> }) {
  const primary = String(consensus.primaryDiagnosis ?? consensus.primary_diagnosis ?? "");
  const differential =
    (consensus.differentialDiagnosis as string[] | undefined) ??
    (consensus.differential_diagnosis as string[] | undefined);
  const icd = String(consensus.icdCode ?? consensus.icd_code ?? "");
  const confRaw = Number(consensus.confidence ?? 0);
  const confidence = Number.isFinite(confRaw) ? Math.max(0, Math.min(100, Math.round(confRaw))) : 0;
  const urgency = pickUrgency(consensus.urgency ?? consensus.urgencyLevel);
  const prognosis = String(consensus.prognosis ?? "");
  const keyFindings = String(consensus.keyFindings ?? consensus.key_findings ?? "");

  const urgencyIdx = URGENCY_SCALE.indexOf(urgency as (typeof URGENCY_SCALE)[number]);

  return (
    <div className="space-y-8">
      {/* Diagnosis header */}
      <header className="space-y-3">
        <p className="mono-label">Primary diagnosis</p>
        <h3 className="font-display text-[1.75rem] md:text-[2rem] leading-[1.1] text-ink">
          {primary || "—"}
        </h3>
        {icd && (
          <p className="inline-flex items-center gap-2 text-[13px] text-ink-muted">
            <span className="mono-label">ICD-10</span>
            <span className="font-mono text-ink">{icd}</span>
          </p>
        )}
      </header>

      {/* Meters row — confidence + urgency side by side */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
        {/* Confidence meter */}
        <div className="rounded-xl border border-line bg-surface p-5">
          <div className="flex items-baseline justify-between mb-3">
            <p className="mono-label">Confidence</p>
            <p className="font-display text-[1.5rem] text-ink leading-none tabular-nums">
              {confidence}
              <span className="text-[0.875rem] text-ink-faint ml-1">%</span>
            </p>
          </div>
          <div className="relative h-2 w-full rounded-full bg-periwinkle-soft overflow-hidden">
            <div
              className="absolute inset-y-0 left-0 rounded-full bg-cornflower transition-[width] duration-700 ease-out"
              style={{ width: `${confidence}%` }}
            />
          </div>
          <div className="mt-2 flex justify-between mono-label text-ink-whisper">
            <span>low</span>
            <span>medium</span>
            <span>high</span>
          </div>
        </div>

        {/* Urgency meter */}
        <div className="rounded-xl border border-line bg-surface p-5">
          <div className="flex items-baseline justify-between mb-3">
            <p className="mono-label">Urgency</p>
            <p className="font-display text-[1.5rem] text-ink leading-none capitalize">
              {URGENCY_COPY[urgency]?.label ?? urgency}
            </p>
          </div>
          <div className="flex items-stretch gap-1.5">
            {URGENCY_SCALE.map((level, i) => {
              const active = i <= urgencyIdx;
              return (
                <div
                  key={level}
                  className={[
                    "flex-1 h-2 rounded-full transition-colors duration-500",
                    active
                      ? i === 2
                        ? "bg-indigo"
                        : i === 1
                          ? "bg-cornflower"
                          : "bg-slate"
                      : "bg-periwinkle-soft",
                  ].join(" ")}
                />
              );
            })}
          </div>
          <div className="mt-2 flex justify-between mono-label text-ink-whisper">
            {URGENCY_SCALE.map((l) => (
              <span
                key={l}
                className={l === urgency ? "text-ink-muted" : undefined}
              >
                {l}
              </span>
            ))}
          </div>
          <p className="mt-3 text-[13px] text-ink-slate leading-relaxed">
            {URGENCY_COPY[urgency]?.caption}
          </p>
        </div>
      </div>

      {/* Differential */}
      {Array.isArray(differential) && differential.length > 0 && (
        <section className="rounded-xl border border-line bg-surface-tint p-5">
          <p className="mono-label mb-3">Differential diagnosis</p>
          <ul className="space-y-1.5">
            {differential.map((d, i) => (
              <li
                key={i}
                className="flex items-baseline gap-3 text-[14.5px] text-ink"
              >
                <span className="plate-counter text-ink-faint w-7 tabular-nums">
                  {String(i + 1).padStart(2, "0")}
                </span>
                <span>{String(d)}</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {/* Key findings */}
      {keyFindings && (
        <section>
          <p className="mono-label mb-2">Key findings</p>
          <div className="rounded-xl border border-line bg-surface p-5">
            <Markdown>{keyFindings}</Markdown>
          </div>
        </section>
      )}

      {/* Prognosis */}
      {prognosis && (
        <section>
          <p className="mono-label mb-2">Prognosis</p>
          <div className="rounded-xl border border-line bg-surface p-5">
            <Markdown>{prognosis}</Markdown>
          </div>
        </section>
      )}
    </div>
  );
}
