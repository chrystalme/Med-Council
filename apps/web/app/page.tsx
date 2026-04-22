import Link from "next/link";
import { PricingTable, Show, SignInButton, SignUpButton } from "@clerk/nextjs";

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

const PROCESS = [
  { numeral: "I", name: "Intake", caption: "symptoms & context" },
  { numeral: "II", name: "Follow-up", caption: "clarifying questions" },
  { numeral: "III", name: "Council", caption: "specialist roster" },
  { numeral: "IV", name: "Research", caption: "literature scan" },
  { numeral: "V", name: "Consensus", caption: "cross-specialty" },
  { numeral: "VI", name: "Plan", caption: "coordinated care" },
  { numeral: "VII", name: "Message", caption: "for the patient" },
];

export default function LandingPage() {
  return (
    <div className="min-h-screen flex flex-col">
      {/* Masthead */}
      <header className="px-8 md:px-14 pt-8 pb-5 flex items-center justify-between border-b border-line">
        <div className="flex items-baseline gap-4">
          <div className="flex items-center gap-2.5">
            <span
              aria-hidden
              className="block h-2.5 w-2.5 rounded-sm bg-indigo rotate-45"
            />
            <span className="font-display text-[1.35rem] tracking-tight text-ink">
              MedAI Council
            </span>
          </div>
          <span className="mono-label hidden sm:inline">
            An Atlas <span className="diamond" /> Edition III
          </span>
        </div>
        <div className="mono-label flex items-center gap-3">
          <Link href="#pricing" className="hover:text-indigo transition-colors hidden sm:inline">
            Pricing
          </Link>
          <span className="hidden md:inline">Plate 00 <span className="diamond" /> Cover</span>
          <span className="inline-flex items-center gap-1.5">
            <span className="h-1.5 w-1.5 rounded-full bg-cornflower atlas-pulse" />
            system online
          </span>
        </div>
      </header>

      {/* Hero — asymmetric, with a gigantic XVI watermark */}
      <main className="relative flex-1 px-6 md:px-14 pt-16 pb-20">
        <div className="grid grid-cols-12 gap-x-6 gap-y-10">
          {/* Left: eyebrow + headline + body + CTAs */}
          <section className="col-span-12 lg:col-span-8 relative">
            <div className="flex items-center gap-3 mb-6 rise rise-1">
              <span className="stage-marker">Prolegomenon</span>
              <span className="h-px w-16 bg-line-strong" />
              <span className="plate-counter">Plate I</span>
            </div>

            <h1 className="font-display text-[clamp(2.75rem,7vw,6.5rem)] leading-[0.98] text-ink mb-8 text-balance rise rise-2">
              Sixteen&nbsp;specialists.
              <br />
              <em className="italic text-indigo font-normal">One</em>{" "}
              deliberation.
              <br />
              A single, considered
              <br />
              <em className="italic">assessment.</em>
            </h1>

            <div className="flex items-center gap-4 mb-10 rise rise-3">
              <span className="h-px w-12 bg-ink-whisper" />
              <span className="diamond" />
              <span className="h-px w-12 bg-ink-whisper" />
            </div>

            <p className="text-[1.125rem] md:text-[1.1875rem] text-ink-slate max-w-[46ch] leading-relaxed mb-10 text-pretty rise rise-4">
              Describe a symptom in plain language. A council of reasoning
              specialists — cardiology, neurology, endocrinology, and thirteen
              more — deliberates, consults the literature, and returns a
              diagnosis, a plan, and a message you can act on.
            </p>

            <div className="flex flex-wrap items-center gap-3 mb-6 rise rise-5">
              <Show when="signed-out">
                <SignUpButton mode="modal"><button className="btn-indigo">Begin a consultation →</button></SignUpButton>
                <SignInButton mode="modal"><button className="btn-ghost">I already have an account</button></SignInButton>
              </Show>
              <Show when="signed-in">
                <Link href="/case" className="btn-indigo">
                  Continue to the council
                  <span aria-hidden>→</span>
                </Link>
              </Show>
            </div>

            <p className="mono-label max-w-md leading-relaxed rise rise-6">
              Demonstration only <span className="diamond" /> outputs are not a
              substitute for licensed medical advice
            </p>
          </section>

          {/* Right: Specialist register — typographic atlas sidebar */}
          <aside className="col-span-12 lg:col-span-4 relative lg:pl-10 lg:border-l lg:border-line">
            {/* Gigantic watermark XVI sitting behind the register */}
            <div
              aria-hidden
              className="hidden lg:block absolute -top-6 -right-6 z-0 watermark text-[18rem] rise rise-2"
            >
              XVI
            </div>

            <div className="relative z-10">
              <div className="flex items-center justify-between mb-5 rise rise-3">
                <span className="stage-marker">The register</span>
                <span className="plate-counter">16 seats</span>
              </div>
              <ol className="space-y-[0.35rem] rise rise-4">
                {SPECIALISTS.map((s, i) => (
                  <li
                    key={s}
                    className="group flex items-baseline gap-4 py-1.5 border-b border-line/70 last:border-b-0"
                  >
                    <span className="plate-counter w-7 tabular-nums text-ink-faint">
                      {String(i + 1).padStart(2, "0")}
                    </span>
                    <span className="font-display text-[1.0625rem] text-ink leading-tight group-hover:text-indigo transition-colors">
                      {s}
                    </span>
                  </li>
                ))}
              </ol>
              <p className="mono-label mt-7 leading-relaxed max-w-[28ch] rise rise-5">
                Four to six specialists are selected per case. The full roster
                deliberates when the evidence is ambiguous.
              </p>
            </div>
          </aside>
        </div>
      </main>

      {/* Process rail — horizontal atlas strip */}
      <section className="border-t border-line bg-paper-deep/60 relative">
        <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-line-deep to-transparent" />
        <div className="px-6 md:px-14 py-10 md:py-14">
          <div className="flex items-center gap-4 mb-8">
            <span className="stage-marker">Method</span>
            <span className="h-px flex-1 bg-line-strong" />
            <span className="plate-counter">seven plates</span>
          </div>
          <ol className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-7 gap-x-6 gap-y-8">
            {PROCESS.map((s) => (
              <li key={s.name} className="relative pl-4 border-l border-line-strong">
                <p className="plate-numeral text-[2.5rem] mb-1 text-indigo">
                  {s.numeral}
                </p>
                <p className="font-display text-[1.125rem] text-ink leading-tight">
                  {s.name}
                </p>
                <p className="mono-label mt-1.5 normal-case tracking-wider text-ink-faint">
                  {s.caption}
                </p>
              </li>
            ))}
          </ol>
        </div>
      </section>

      {/* Pricing — Clerk Billing */}
      <section id="pricing" className="border-t border-line relative scroll-mt-20">
        <div className="px-6 md:px-14 py-16 md:py-20 max-w-5xl mx-auto">
          <div className="flex items-center gap-4 mb-8">
            <span className="stage-marker">Pricing</span>
            <span className="h-px flex-1 bg-line-strong" />
            <span className="plate-counter">two tiers</span>
          </div>
          <div className="grid grid-cols-12 gap-6 mb-10">
            <div className="col-span-12 lg:col-span-7">
              <h2 className="font-display text-[clamp(2rem,4vw,3rem)] leading-[1.04] text-ink mb-4 text-balance">
                <em className="italic text-indigo font-normal">Two</em> tiers. No
                seat minimums.
              </h2>
              <p className="text-[1.0625rem] text-ink-slate max-w-[48ch] leading-relaxed text-pretty">
                Free gives you the full 7-stage pipeline on Nemotron, browser
                voice, and memory for up to four consultations. Pro unlocks
                premium models, Whisper transcription, unlimited saved
                consultations, 10 MB × 20 test attachments, and email
                delivery.
              </p>
            </div>
            <aside className="col-span-12 lg:col-span-5 lg:border-l lg:border-line lg:pl-6">
              <p className="stage-marker mb-3">What Pro adds</p>
              <ul className="space-y-2 text-[14.5px] text-ink-slate">
                <li className="flex items-baseline gap-3">
                  <span className="plate-counter text-ink-faint w-7 tabular-nums">I</span>
                  Claude Opus 4.7, GPT-5, Gemini 2.5 Pro, DeepSeek R1
                </li>
                <li className="flex items-baseline gap-3">
                  <span className="plate-counter text-ink-faint w-7 tabular-nums">II</span>
                  Whisper transcription + premium TTS voices
                </li>
                <li className="flex items-baseline gap-3">
                  <span className="plate-counter text-ink-faint w-7 tabular-nums">III</span>
                  Unlimited saved consultations (vector memory)
                </li>
                <li className="flex items-baseline gap-3">
                  <span className="plate-counter text-ink-faint w-7 tabular-nums">IV</span>
                  10 MB × 20 test attachments per case
                </li>
                <li className="flex items-baseline gap-3">
                  <span className="plate-counter text-ink-faint w-7 tabular-nums">V</span>
                  Email the summary directly to the patient
                </li>
              </ul>
            </aside>
          </div>

          <div className="plate-card p-6 md:p-8">
            <PricingTable />
          </div>
        </div>
      </section>

      {/* Footer colophon */}
      <footer className="px-6 md:px-14 py-6 flex flex-wrap items-center justify-between gap-3 border-t border-line mono-label">
        <span>
          MedAI Council <span className="diamond" /> a research artefact
        </span>
        <span>
          Inference <span className="diamond" /> OpenRouter{" "}
          <span className="diamond" /> nvidia/nemotron-3-super-120b-a12b
        </span>
      </footer>
    </div>
  );
}
