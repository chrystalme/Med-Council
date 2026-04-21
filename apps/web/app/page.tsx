import Link from "next/link";
import { Show, SignInButton, SignUpButton } from "@clerk/nextjs";

const SPECIALISTS = [
  "Internal medicine",
  "Cardiology",
  "Neurology",
  "Psychiatry",
  "Pulmonology",
  "Gastroenterology",
  "Endocrinology",
  "Rheumatology",
  "Dermatology",
  "Orthopaedics",
  "Pharmacology",
  "Obstetrics & gynaecology",
  "Oral medicine",
  "Ophthalmology",
  "ENT",
  "Urology",
];

export default function LandingPage() {
  return (
    <div className="min-h-screen flex flex-col">
      {/* Masthead */}
      <header className="px-8 md:px-14 pt-10 pb-6 flex items-center justify-between">
        <div className="flex items-baseline gap-3">
          <span className="font-display text-xl tracking-tight">
            MedAI Council
          </span>
          <span className="mono-label hidden sm:inline">Edition III</span>
        </div>
        <div className="mono-label">Case №0000 — Pending</div>
      </header>

      <div className="rule-ornament px-8 md:px-14 mb-10">
        <span className="font-display italic text-sm text-ink-muted">
          ·&nbsp;&nbsp;a clinical deliberation&nbsp;&nbsp;·
        </span>
      </div>

      {/* Hero */}
      <main className="flex-1 px-8 md:px-14 grid grid-cols-12 gap-6 pb-20">
        <section className="col-span-12 md:col-span-8">
          <p className="stage-marker mb-8">Prolegomenon — I</p>

          <h1 className="font-display text-[clamp(2.5rem,6vw,5.25rem)] leading-[1.04] text-ink mb-10">
            Sixteen specialists.
            <br />
            <em className="italic text-clay">One</em> deliberation.
            <br />
            A single, considered <em className="italic">assessment.</em>
          </h1>

          <p className="text-lg md:text-xl text-ink-muted max-w-2xl leading-relaxed mb-12 text-pretty">
            Describe a symptom in plain language. A council of reasoning
            specialists — cardiology, neurology, endocrinology and thirteen
            more — deliberates, consults the literature, and returns a
            diagnosis, a plan, and a message you can act on.
          </p>

          <div className="flex flex-wrap items-center gap-4">
            <Show when="signed-out">
              <SignUpButton mode="modal"><button className="h-12 px-7 bg-clay text-paper font-medium text-[15px] tracking-tight rounded-full hover:bg-clay-hover transition-colors cursor-pointer">Begin a consultation</button></SignUpButton>
              <SignInButton mode="modal"><button className="h-12 px-6 text-ink font-medium text-[15px] tracking-tight rounded-full border border-line-strong hover:bg-paper-deep transition-colors cursor-pointer">I already have an account</button></SignInButton>
            </Show>
            <Show when="signed-in">
              <Link
                href="/case"
                className="h-12 px-7 inline-flex items-center bg-clay text-paper font-medium text-[15px] tracking-tight rounded-full hover:bg-clay-hover transition-colors"
              >
                Continue to the council →
              </Link>
            </Show>
          </div>

          <p className="mono-label mt-10 max-w-md leading-relaxed">
            Demonstration only. Outputs are not a substitute for licensed
            medical advice.
          </p>
        </section>

        {/* Specialist roster — typographic decoration */}
        <aside className="hidden md:block md:col-span-4 md:border-l md:border-line md:pl-8">
          <p className="stage-marker mb-6">The council · sixteen seats</p>
          <ol className="space-y-2.5 font-display text-[15px] text-ink leading-tight">
            {SPECIALISTS.map((s, i) => (
              <li key={s} className="flex items-baseline gap-3">
                <span className="mono-label w-6 text-right text-ink-faint">
                  {String(i + 1).padStart(2, "0")}
                </span>
                <span>{s}</span>
              </li>
            ))}
          </ol>
          <p className="mono-label mt-8 leading-relaxed">
            Four to six specialists are selected per case. The full roster
            deliberates when the evidence is ambiguous.
          </p>
        </aside>
      </main>

      {/* Process strip — editorial footnote */}
      <section className="border-t border-line bg-paper-deep/60">
        <div className="px-8 md:px-14 py-10 grid grid-cols-2 md:grid-cols-7 gap-6 max-w-6xl">
          {[
            "Intake",
            "Triage",
            "Council",
            "Research",
            "Consensus",
            "Plan",
            "Message",
          ].map((stage, i) => (
            <div key={stage}>
              <p className="stage-marker">
                {["I", "II", "III", "IV", "V", "VI", "VII"][i]}
              </p>
              <p className="font-display text-lg text-ink mt-1">{stage}</p>
            </div>
          ))}
        </div>
      </section>

      <footer className="px-8 md:px-14 py-6 flex items-center justify-between border-t border-line mono-label">
        <span>MedAI Council · a research artefact</span>
        <span>
          Inference · OpenRouter · nvidia/nemotron-3-super-120b-a12b:free
        </span>
      </footer>
    </div>
  );
}
