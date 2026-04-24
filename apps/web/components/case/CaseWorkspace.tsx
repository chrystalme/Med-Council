'use client';

import { useAuth } from '@clerk/nextjs';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { councilJson, CouncilApiError } from '@/lib/council-api';

/**
 * Turn any thrown value from a council API call into a human-readable,
 * stage-aware error message. Structured `CouncilApiError` codes get specific
 * copy that tells the user what to try next (switch model, wait, upgrade…)
 * instead of leaking provider stack traces as a "Fault" banner.
 */
function formatCouncilError(e: unknown, stageLabel: string): string {
  if (e instanceof CouncilApiError) {
    const detail = e.detail;
    const structured =
      detail && typeof detail === 'object' && 'code' in detail
        ? (detail as { code?: string; message?: string })
        : null;
    const code = structured?.code ?? e.code;
    const message = structured?.message;

    if (code === 'bad_model') {
      return (
        (message ?? 'The selected model was rejected by the provider.') +
        ' Pick another model from the selector at the top of the page and click the stage again.'
      );
    }
    if (code === 'groq_not_configured') {
      return (
        message ??
        'Free-tier model is unavailable (server misconfigured). Pick a Pro model or try again later.'
      );
    }
    if (code === 'provider_unavailable') {
      return (
        (message ?? 'The model provider is temporarily unavailable.') +
        ' Try again in a moment, or switch to a different model.'
      );
    }
    if (code === 'consultation_cap' || code === 'attachment_size' || code === 'voice_premium') {
      // Upgrade-modal codes are already handled upstream; just surface the message.
      return message ?? `${stageLabel} failed.`;
    }
    return message ?? `${stageLabel} failed (HTTP ${e.status}).`;
  }
  if (e instanceof Error) return e.message;
  return `${stageLabel} failed.`;
}
import { PaywallBanner } from './PaywallBanner';
import { Markdown } from './Markdown';
import { ConsensusView } from './ConsensusView';
import { ModelSelector, useStoredModelKey } from './ModelSelector';
import { VoiceInput } from './VoiceInput';
import { VoiceOutput } from './VoiceOutput';
import { PatientContext } from './PatientContext';
import { TestAttachment } from './TestAttachment';
import { UpgradeModal, useUpgradePrompt } from './UpgradeModal';
import { EmailToPatient } from './EmailToPatient';

type Physician = {
  id: string;
  name: string;
  specialty: string;
  initials: string;
  assessment?: string;
};

type CaseState = {
  step: number;
  symptoms: string;
  fqLines: string[];
  fqAnswers: string[];
  councilRoster: Physician[];
  deliberationCaseSummary: string;
  deliberationFocusAreas: string[];
  deliberationReason: string;
  physicians: Physician[];
  research: Record<string, unknown>[];
  consensus: Record<string, unknown> | null;
  plan: string;
  message: string;
};

const LS_CASE = 'medai_case_id';
const LS_SAVED_CONSULTATION_FOR_CASE = 'medai_consultation_saved_for';

function parseNumberedQuestions(text: string): string[] {
  return text
    .split('\n')
    .map(l => l.replace(/^\d+\.\s*/, '').trim())
    .filter(Boolean);
}

const STAGES = [
  { label: 'Intake', numeral: 'I' },
  { label: 'Follow-up', numeral: 'II' },
  { label: 'Roster', numeral: 'III' },
  { label: 'Council', numeral: 'IV' },
  { label: 'Research', numeral: 'V' },
  { label: 'Consensus', numeral: 'VI' },
  { label: 'Plan', numeral: 'VII' },
  { label: 'Message', numeral: 'VIII' },
] as const;

export function CaseWorkspace() {
  const { getToken } = useAuth();
  const tokenFn = useCallback(async () => {
    try {
      return await getToken();
    } catch {
      return null;
    }
  }, [getToken]);

  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [symptoms, setSymptoms] = useState('');
  const [fqLines, setFqLines] = useState<string[]>([]);
  const [fqAnswers, setFqAnswers] = useState<string[]>(['', '', '', '']);
  const [councilRoster, setCouncilRoster] = useState<Physician[]>([]);
  const [deliberationCaseSummary, setDeliberationCaseSummary] = useState('');
  const [deliberationFocusAreas, setDeliberationFocusAreas] = useState<
    string[]
  >([]);
  const [deliberationReason, setDeliberationReason] = useState('');
  const [physicians, setPhysicians] = useState<Physician[]>([]);
  const [research, setResearch] = useState<Record<string, unknown>[]>([]);
  const [parseWarning, setParseWarning] = useState('');
  const [consensus, setConsensus] = useState<Record<string, unknown> | null>(
    null,
  );
  const [plan, setPlan] = useState('');
  const [message, setMessage] = useState('');
  const [followupQ, setFollowupQ] = useState('');
  const [followupPrior, setFollowupPrior] = useState('');
  const [followupReply, setFollowupReply] = useState('');

  const [step, setStep] = useState(0);
  const [maxStep, setMaxStep] = useState(0);
  const [modelKey, setModelKey] = useStoredModelKey('nvidia-nemotron-free');
  const [caseId, setCaseId] = useState<string | null>(null);
  const [consultationSaveError, setConsultationSaveError] = useState<string | null>(null);
  const consultationSavedRef = useRef<string | null>(null);
  const upgrade = useUpgradePrompt();

  const advanceTo = useCallback((n: number) => {
    setStep(n);
    setMaxStep(m => (n > m ? n : m));
  }, []);

  const fqJoined = useMemo(
    () =>
      fqLines
        .map((q, i) => `Q: ${q}\nA: ${(fqAnswers[i] ?? '').trim()}`)
        .join('\n\n'),
    [fqLines, fqAnswers],
  );

  const saveRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const autoRunRef = useRef<{
    2: boolean;
    3: boolean;
    4: boolean;
    5: boolean;
    6: boolean;
  }>({
    2: false,
    3: false,
    4: false,
    5: false,
    6: false,
  });

  const persistState = useCallback(async () => {
    if (typeof window === 'undefined') return;
    const id = localStorage.getItem(LS_CASE);
    if (!id) return;
    const tok = await tokenFn();
    const state: CaseState = {
      step,
      symptoms,
      fqLines,
      fqAnswers,
      councilRoster,
      deliberationCaseSummary,
      deliberationFocusAreas,
      deliberationReason,
      physicians,
      research,
      consensus,
      plan,
      message,
    };
    try {
      await councilJson(`/api/cases/${id}`, {
        method: 'PATCH',
        token: tok,
        body: JSON.stringify({
          state: JSON.parse(JSON.stringify(state)) as Record<string, unknown>,
        }),
      });
    } catch {
      /* autosave best-effort */
    }
  }, [
    tokenFn,
    step,
    symptoms,
    fqLines,
    fqAnswers,
    councilRoster,
    deliberationCaseSummary,
    deliberationFocusAreas,
    deliberationReason,
    physicians,
    research,
    consensus,
    plan,
    message,
  ]);

  useEffect(() => {
    if (typeof window === 'undefined') return;
    if (!localStorage.getItem(LS_CASE)) return;
    if (saveRef.current) clearTimeout(saveRef.current);
    saveRef.current = setTimeout(() => {
      void persistState();
    }, 900);
    return () => {
      if (saveRef.current) clearTimeout(saveRef.current);
    };
  }, [persistState]);

  useEffect(() => {
    if (typeof window === 'undefined') return;
    void (async () => {
      const id = localStorage.getItem(LS_CASE);
      if (!id) return;
      setCaseId(id);
      const tok = await tokenFn();
      try {
        const row = await councilJson<{
          state: CaseState;
        }>(`/api/cases/${id}`, { method: 'GET', token: tok });
        const s = row.state;
        if (s && typeof s === 'object') {
          const restored = typeof s.step === 'number' ? s.step : 0;

          // Presence-of-output drives which stages we consider "already done".
          // Saved-step alone is unreliable (a stage auto-advances before its
          // next-stage effect resolves), and using `restored > i` leaves the
          // user's current stage's flag = false, re-firing the stage on refresh.
          const hasPhysicians  = Array.isArray(s.physicians) && s.physicians.length > 0;
          const hasResearch    = Array.isArray(s.research)    && s.research.length > 0;
          const hasConsensus   = !!s.consensus;
          const hasPlan        = typeof s.plan === 'string'    && s.plan.trim().length > 0;
          const hasMessage     = typeof s.message === 'string' && s.message.trim().length > 0;

          autoRunRef.current[2] = hasPhysicians;
          autoRunRef.current[3] = hasResearch;
          autoRunRef.current[4] = hasConsensus;
          autoRunRef.current[5] = hasPlan;
          autoRunRef.current[6] = hasMessage;

          // maxStep = the furthest stage the user has actually reached. If the
          // message is saved we've completed step 6 → reached step 7 (review).
          // Without this, `maxStep` stays at `restored` and the auto-run guard
          // `step !== maxStep` can flip false when the user clicks back, which
          // teleports them forward again through the idempotent paths.
          let computedMax = restored;
          if (hasPhysicians) computedMax = Math.max(computedMax, 3);
          if (hasResearch)   computedMax = Math.max(computedMax, 4);
          if (hasConsensus)  computedMax = Math.max(computedMax, 5);
          if (hasPlan)       computedMax = Math.max(computedMax, 6);
          if (hasMessage)    computedMax = Math.max(computedMax, 7);
          setStep(restored);
          setMaxStep(computedMax);

          // Did we already post the consultation for this case? The ref is
          // in-memory only, so a refresh used to re-POST on reaching step 7
          // → duplicate patient-memory rows. Persist a localStorage sentinel
          // so the post stays idempotent across reloads.
          try {
            const savedFor = localStorage.getItem(LS_SAVED_CONSULTATION_FOR_CASE);
            if (savedFor === id) consultationSavedRef.current = id;
          } catch {
            /* ignore */
          }

          setSymptoms(s.symptoms ?? '');
          setFqLines(Array.isArray(s.fqLines) ? s.fqLines : []);
          setFqAnswers(
            Array.isArray(s.fqAnswers) && s.fqAnswers.length === 4
              ? s.fqAnswers
              : ['', '', '', ''],
          );
          setCouncilRoster(
            Array.isArray(s.councilRoster) ? s.councilRoster : [],
          );
          setDeliberationCaseSummary(s.deliberationCaseSummary ?? '');
          setDeliberationFocusAreas(
            Array.isArray(s.deliberationFocusAreas)
              ? s.deliberationFocusAreas
              : [],
          );
          setDeliberationReason(s.deliberationReason ?? '');
          setPhysicians(Array.isArray(s.physicians) ? s.physicians : []);
          setResearch(Array.isArray(s.research) ? s.research : []);
          setConsensus(s.consensus ?? null);
          setPlan(s.plan ?? '');
          setMessage(s.message ?? '');
        }
      } catch {
        localStorage.removeItem(LS_CASE);
      }
    })();
  }, [tokenFn]);


  const ensureCase = useCallback(async () => {
    if (typeof window === 'undefined') return '';
    let id = localStorage.getItem(LS_CASE);
    if (id) {
      if (caseId !== id) setCaseId(id);
      return id;
    }
    const tok = await tokenFn();
    const created = await councilJson<{ id: string }>(`/api/cases`, {
      method: 'POST',
      token: tok,
      body: JSON.stringify({ title: symptoms.slice(0, 80) || 'Case' }),
    });
    id = created.id;
    localStorage.setItem(LS_CASE, id);
    setCaseId(id);
    return id;
  }, [tokenFn, symptoms, caseId]);

  const runIntake = async () => {
    setErr(null);
    setBusy('Drafting follow-up questions…');
    const tok = await tokenFn();
    try {
      const data = await councilJson<{ questions: string }>(
        `/api/intake/followup`,
        {
          method: 'POST',
          token: tok,
          body: JSON.stringify({ symptoms, model: modelKey, case_id: caseId }),
        },
      );
      const lines = parseNumberedQuestions(data.questions);
      if (lines.length < 4) {
        setFqLines(lines.length ? lines : [data.questions]);
      } else {
        setFqLines(lines.slice(0, 8));
      }
      setFqAnswers(prev =>
        Array.from(
          { length: Math.max(4, lines.length) },
          (_, i) => prev[i] ?? '',
        ),
      );
      await ensureCase();
      advanceTo(1);
    } catch (e) {
      setErr(formatCouncilError(e, 'Intake'));
    } finally {
      setBusy(null);
    }
  };

  const runDeliberation = async () => {
    setErr(null);
    setBusy('Selecting deliberation experts…');
    const tok = await tokenFn();
    try {
      const data = await councilJson<{
        experts: Physician[];
        case_summary: string;
        focus_areas: string[];
        reason_for_selection: string;
      }>(`/api/deliberation/select-experts`, {
        method: 'POST',
        token: tok,
        body: JSON.stringify({ symptoms, followup_answers: fqJoined, model: modelKey, case_id: caseId }),
      });
      setCouncilRoster(data.experts ?? []);
      setDeliberationCaseSummary(data.case_summary ?? '');
      setDeliberationFocusAreas(data.focus_areas ?? []);
      setDeliberationReason(data.reason_for_selection ?? '');
      autoRunRef.current = { 2: false, 3: false, 4: false, 5: false, 6: false };
      advanceTo(2);
    } catch (e) {
      setErr(formatCouncilError(e, 'Expert selection'));
    } finally {
      setBusy(null);
    }
  };

  const runCouncil = async () => {
    setErr(null);
    setBusy('Running specialist deliberation…');
    const roster = councilRoster.length ? councilRoster : [];
    const ctx = [
      deliberationCaseSummary && `Case summary: ${deliberationCaseSummary}`,
      deliberationFocusAreas.length &&
        `Focus areas: ${deliberationFocusAreas.join('; ')}`,
      deliberationReason && `Selection rationale: ${deliberationReason}`,
    ]
      .filter(Boolean)
      .join('\n\n');

    const out: Physician[] = [];
    try {
      for (const p of roster) {
        setBusy(`Consulting ${p.name}…`);
        // Fetch a fresh Clerk token per specialist — each LLM call can take
        // 10–30s and Clerk's default session JWT TTL is 60s, so a token
        // captured before the loop would expire by specialist 3 or 4.
        // `getToken()` is cached inside Clerk; it only hits the network when
        // the cached token has less than ~10s left.
        const tok = await tokenFn();
        const prior = out.map(x => ({
          name: x.name,
          specialty: x.specialty,
          assessment: x.assessment ?? '',
        }));
        const data = await councilJson<{
          specialist: Physician;
          assessment: string;
        }>(`/api/council/physician`, {
          method: 'POST',
          token: tok,
          body: JSON.stringify({
            physician_id: p.id,
            symptoms,
            followup_answers: fqJoined,
            prior_assessments: prior,
            council_context: ctx,
            model: modelKey,
            case_id: caseId,
          }),
        });
        out.push({
          ...data.specialist,
          assessment: data.assessment,
        });
      }
      setPhysicians(out);
      advanceTo(3);
    } catch (e) {
      setErr(formatCouncilError(e, 'Council'));
    } finally {
      setBusy(null);
    }
  };

  const runResearch = async () => {
    setErr(null);
    setBusy('Searching literature…');
    const tok = await tokenFn();
    try {
      const data = await councilJson<{
        papers: Record<string, unknown>[];
        parse_warning?: string;
      }>(`/api/research`, {
        method: 'POST',
        token: tok,
        body: JSON.stringify({
          symptoms,
          followup_answers: fqJoined,
          assessments: physicians.map(p => ({
            name: p.name,
            specialty: p.specialty,
            assessment: p.assessment ?? '',
          })),
          model: modelKey,
            case_id: caseId,
        }),
      });
      setResearch(data.papers ?? []);
      setParseWarning(data.parse_warning ?? '');
      advanceTo(4);
    } catch (e) {
      setErr(formatCouncilError(e, 'Research'));
    } finally {
      setBusy(null);
    }
  };

  const runConsensus = async () => {
    setErr(null);
    setBusy('Building consensus assessment…');
    const tok = await tokenFn();
    try {
      const data = await councilJson<{ consensus: Record<string, unknown> }>(
        `/api/consensus`,
        {
          method: 'POST',
          token: tok,
          body: JSON.stringify({
            symptoms,
            followup_answers: fqJoined,
            assessments: physicians.map(p => ({
              name: p.name,
              specialty: p.specialty,
              assessment: p.assessment ?? '',
            })),
            research,
            model: modelKey,
            case_id: caseId,
          }),
        },
      );
      setConsensus(data.consensus ?? null);
      advanceTo(5);
    } catch (e) {
      setErr(formatCouncilError(e, 'Consensus'));
    } finally {
      setBusy(null);
    }
  };

  const runPlan = async () => {
    if (!consensus) return;
    // Idempotent: don't regenerate if we already have a plan for this case.
    if (plan && plan.trim().length > 0) {
      advanceTo(6);
      return;
    }
    setErr(null);
    setBusy('Drafting coordinated plan…');
    const tok = await tokenFn();
    try {
      const data = await councilJson<{ plan: string }>(`/api/plan`, {
        method: 'POST',
        token: tok,
        body: JSON.stringify({
          symptoms,
          followup_answers: fqJoined,
          consensus,
          assessments: physicians.map(p => ({
            name: p.name,
            specialty: p.specialty,
            assessment: p.assessment ?? '',
          })),
          model: modelKey,
            case_id: caseId,
        }),
      });
      setPlan(data.plan ?? '');
      advanceTo(6);
    } catch (e) {
      setErr(formatCouncilError(e, 'Plan'));
    } finally {
      setBusy(null);
    }
  };

  const runMessage = async () => {
    if (!consensus) return;
    // Idempotent: don't regenerate if we already have a patient message.
    if (message && message.trim().length > 0) {
      advanceTo(7);
      return;
    }
    setErr(null);
    setBusy('Writing patient-facing summary…');
    const tok = await tokenFn();
    try {
      const data = await councilJson<{ message: string }>(`/api/message`, {
        method: 'POST',
        token: tok,
        body: JSON.stringify({ symptoms, consensus, plan, model: modelKey, case_id: caseId }),
      });
      setMessage(data.message ?? '');
      advanceTo(7);
    } catch (e) {
      setErr(formatCouncilError(e, 'Message'));
    } finally {
      setBusy(null);
    }
  };

  const saveConsultation = useCallback(async () => {
    if (!caseId || !consensus) return;
    if (consultationSavedRef.current === caseId) return;
    consultationSavedRef.current = caseId;  // optimistic lock
    try {
      const tok = await tokenFn();
      const primary_dx =
        (consensus.primaryDiagnosis as string | undefined) ??
        (consensus.primary_diagnosis as string | undefined) ??
        null;
      const summary =
        typeof consensus.keyFindings === 'string'
          ? (consensus.keyFindings as string)
          : typeof consensus.key_findings === 'string'
            ? (consensus.key_findings as string)
            : message || plan || symptoms;

      // Fetch attachment texts for embedding input.
      let attachmentTexts: string[] = [];
      try {
        const attached = await councilJson<{
          attachments: { text_preview: string }[];
        }>(`/api/cases/${caseId}/attachments`, {
          method: 'GET',
          token: tok,
        });
        attachmentTexts = (attached.attachments ?? [])
          .map((a) => a.text_preview)
          .filter(Boolean);
      } catch {
        /* ignore — attachments are optional */
      }

      await councilJson('/api/patient/consultations', {
        method: 'POST',
        token: tok,
        body: JSON.stringify({
          case_id: caseId,
          summary,
          primary_dx,
          icd_code:
            (consensus.icdCode as string | undefined) ??
            (consensus.icd_code as string | undefined) ??
            null,
          urgency:
            (consensus.urgency as string | undefined) ??
            (consensus.urgencyLevel as string | undefined) ??
            null,
          confidence:
            typeof consensus.confidence === 'number'
              ? consensus.confidence
              : null,
          attachment_texts: attachmentTexts,
        }),
      });
      // Persist the "already saved" sentinel so a page refresh doesn't
      // re-POST the consultation and create a duplicate record.
      try {
        localStorage.setItem(LS_SAVED_CONSULTATION_FOR_CASE, caseId);
      } catch {
        /* ignore */
      }
      setConsultationSaveError(null);
    } catch (e) {
      consultationSavedRef.current = null;  // allow retry
      setConsultationSaveError(e instanceof Error ? e.message : 'Save failed');
      upgrade.show(e);
    }
  }, [caseId, consensus, message, plan, symptoms, tokenFn, upgrade]);

  useEffect(() => {
    if (step === 7 && consensus && caseId && !consultationSaveError) {
      // Idempotent save — the ref sentinel + localStorage key inside
      // saveConsultation prevents duplicate POSTs even as this effect fires
      // multiple times across renders.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      void saveConsultation();
    }
  }, [step, consensus, caseId, consultationSaveError, saveConsultation]);

  useEffect(() => {
    if (busy || err) return;
    // Only auto-advance when we're at the frontier. If the user has clicked
    // back to an earlier stage to review records, don't re-fire network calls.
    if (step !== maxStep) return;
    if (step === 2 && !autoRunRef.current[2]) {
      autoRunRef.current[2] = true;
      void runCouncil();
      return;
    }
    if (step === 3 && !autoRunRef.current[3]) {
      autoRunRef.current[3] = true;
      void runResearch();
      return;
    }
    if (step === 4 && !autoRunRef.current[4]) {
      autoRunRef.current[4] = true;
      void runConsensus();
      return;
    }
    if (step === 5 && !autoRunRef.current[5]) {
      autoRunRef.current[5] = true;
      void runPlan();
      return;
    }
    if (step === 6 && !autoRunRef.current[6]) {
      autoRunRef.current[6] = true;
      void runMessage();
    }
    // Deliberately omit runCouncil/runResearch/runConsensus/runPlan/runMessage
    // from deps — their identities change on every render (they close over
    // huge state), and including them would auto-advance stages endlessly.
    // The autoRunRef guard above is the real idempotency mechanism.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [step, maxStep, busy, err]);

  const runFollowup = async () => {
    if (!consensus) return;
    setErr(null);
    setBusy('Answering follow-up…');
    const tok = await tokenFn();
    try {
      const data = await councilJson<{ reply: string }>(
        `/api/message/followup`,
        {
          method: 'POST',
          token: tok,
          body: JSON.stringify({
            question: followupQ,
            prior_diagnostics: followupPrior,
            symptoms,
            followup_answers: fqJoined,
            consensus,
            plan,
            patient_message: message,
            model: modelKey,
            case_id: caseId,
          }),
        },
      );
      setFollowupReply(data.reply ?? '');
    } catch (e) {
      setErr(formatCouncilError(e, 'Follow-up'));
    } finally {
      setBusy(null);
    }
  };

  const reset = () => {
    localStorage.removeItem(LS_CASE);
    localStorage.removeItem(LS_SAVED_CONSULTATION_FOR_CASE);
    setCaseId(null);
    consultationSavedRef.current = null;
    setConsultationSaveError(null);
    setStep(0);
    setMaxStep(0);
    setSymptoms('');
    setFqLines([]);
    setFqAnswers(['', '', '', '']);
    setCouncilRoster([]);
    setPhysicians([]);
    setResearch([]);
    setConsensus(null);
    setPlan('');
    setMessage('');
    setErr(null);
    autoRunRef.current = { 2: false, 3: false, 4: false, 5: false, 6: false };
    setFollowupQ('');
    setFollowupPrior('');
    setFollowupReply('');
  };

  const resetWithConfirm = () => {
    const ok =
      typeof window === 'undefined' ||
      window.confirm(
        'Start over? This will clear the current in-browser case session and begin a new run.',
      );
    if (ok) reset();
  };

  const currentStage = STAGES[step] ?? STAGES[STAGES.length - 1];

  return (
    <div className='space-y-10'>
      <UpgradeModal prompt={upgrade.prompt} onClose={upgrade.close} />
      <PaywallBanner />

      {/* Stage timeline — horizontal on desktop, with numerals + status dots */}
      <div>
        <div className='flex flex-wrap items-center justify-between gap-3 mb-4'>
          <div className='flex items-center gap-3'>
            <span className='stage-marker'>Pipeline</span>
            <ModelSelector
              value={modelKey}
              onChange={setModelKey}
              disabled={!!busy}
            />
          </div>
          <button
            type='button'
            className='mono-label text-ink-muted hover:text-indigo transition-colors inline-flex items-center gap-2'
            onClick={resetWithConfirm}
            disabled={!!busy}
          >
            <span aria-hidden>⟲</span> reset <span className='diamond' /> start
            over
          </button>
        </div>

        <ol className='grid grid-cols-4 sm:grid-cols-8 gap-1.5'>
          {STAGES.map((stage, i) => {
            const state =
              i === step
                ? 'live'
                : i <= maxStep
                  ? 'done'
                  : 'idle';
            const reachable = i <= maxStep;
            return (
              <li key={stage.label} className='relative'>
                <button
                  type='button'
                  onClick={() => {
                    if (reachable && !busy) setStep(i);
                  }}
                  disabled={!reachable || !!busy}
                  suppressHydrationWarning
                  aria-current={i === step ? 'step' : undefined}
                  aria-label={`Stage ${stage.numeral} — ${stage.label}${reachable ? '' : ' (not yet reached)'}`}
                  className={[
                    'w-full text-left rounded-md p-1 -m-1 transition-colors',
                    reachable
                      ? 'cursor-pointer hover:bg-periwinkle-soft focus-visible:bg-periwinkle-soft'
                      : 'cursor-not-allowed opacity-70',
                  ].join(' ')}
                >
                  <div
                    className={[
                      'h-1 w-full rounded-full mb-2',
                      state === 'live'
                        ? 'bg-cornflower'
                        : state === 'done'
                          ? 'bg-slate'
                          : 'bg-periwinkle-soft',
                    ].join(' ')}
                  />
                  <div className='flex items-baseline gap-1.5'>
                    <span
                      className={[
                        'plate-numeral text-[1.125rem]',
                        state === 'live'
                          ? 'text-indigo'
                          : state === 'done'
                            ? 'text-slate'
                            : 'text-ink-whisper',
                      ].join(' ')}
                    >
                      {stage.numeral}
                    </span>
                    <span
                      className={[
                        'text-[12px] tracking-tight font-medium',
                        state === 'live'
                          ? 'text-ink'
                          : state === 'done'
                            ? 'text-ink-slate'
                            : 'text-ink-faint',
                      ].join(' ')}
                    >
                      {stage.label}
                    </span>
                  </div>
                </button>
              </li>
            );
          })}
        </ol>
      </div>

      {/* Status band — errors + busy line */}
      {err && (
        <div className='rounded-xl border border-urgent/30 bg-urgent-soft px-4 py-3 flex items-start gap-3'>
          <span className='mt-1 h-2 w-2 rounded-full bg-urgent shrink-0' />
          <div>
            <p className='mono-label text-urgent mb-0.5'>Fault</p>
            <p className='text-sm text-ink'>{err}</p>
          </div>
        </div>
      )}
      {busy && (
        <div
          role='status'
          aria-live='polite'
          className='rounded-xl border border-line bg-paper-deep/50 p-4 space-y-3'
        >
          <div className='stage-progress' aria-hidden />
          <div className='flex items-center gap-3'>
            <span className='stage-spinner' aria-hidden />
            <p className='mono-label text-ink-muted'>{busy}</p>
            <span className='sr-only'>Please wait — the council is working.</span>
          </div>
          <div className='space-y-2 pt-1'>
            <span className='skeleton h-3 w-3/4' aria-hidden />
            <span className='skeleton h-3 w-5/6' aria-hidden />
            <span className='skeleton h-3 w-2/3' aria-hidden />
          </div>
        </div>
      )}

      {/* Stage content — each wrapped with a hero numeral and hairline meridian */}
      <StageFrame numeral={currentStage.numeral} label={currentStage.label}>
        {step === 0 && (
          <section className='space-y-5'>
            <PatientContext query={symptoms} />
            <div className='flex items-center justify-between gap-4'>
              <label className='block font-display text-[1.375rem] text-ink'>
                Describe the symptoms.
              </label>
              <VoiceInput
                label='Dictate symptoms'
                disabled={!!busy}
                onPaywallError={upgrade.show}
                onTranscript={(t) =>
                  setSymptoms((s) => (s ? `${s.trimEnd()} ${t}` : t))
                }
              />
            </div>
            <p className='text-[15px] text-ink-slate leading-relaxed max-w-[56ch]'>
              Onset, severity, location, associated symptoms, medications,
              history. The council reasons from whatever you give it. Tap the
              mic to dictate.
            </p>
            <textarea
              className='field min-h-[180px]'
              placeholder='e.g. 54-year-old, sudden onset left-sided chest pressure radiating to jaw, diaphoretic, began 40 minutes ago…'
              value={symptoms}
              onChange={e => setSymptoms(e.target.value)}
            />
            <button
              type='button'
              className='btn-indigo'
              disabled={!symptoms.trim() || !!busy}
              onClick={() => void runIntake()}
            >
              Generate follow-up questions
              <span aria-hidden>→</span>
            </button>
          </section>
        )}

        {step === 1 && (
          <section className='space-y-6'>
            <div>
              <h3 className='font-display text-[1.375rem] text-ink mb-1.5'>
                Clarifying questions.
              </h3>
              <p className='text-[15px] text-ink-slate max-w-[56ch]'>
                The council drafted these from your intake. Answer what you can;
                blanks are fine. If you already have test results, attach them
                below — one document covers every question.
              </p>
            </div>
            <TestAttachment
              caseId={caseId}
              onPaywallError={upgrade.show}
            />
            <ol className='space-y-5 counter-reset-[q]'>
              {fqLines.map((q, i) => (
                <li
                  key={i}
                  className='space-y-2 pl-6 relative border-l border-line hover:border-cornflower transition-colors'
                >
                  <span className='mono-label absolute -left-0.5 top-0 -translate-x-full pr-3 text-ink-muted'>
                    Q{String(i + 1).padStart(2, '0')}
                  </span>
                  <div className='flex items-start justify-between gap-3'>
                    <p className='font-display text-[1.05rem] text-ink leading-snug flex-1'>
                      {q}
                    </p>
                    <VoiceOutput text={q} label={`Read question ${i + 1}`} onPaywallError={upgrade.show} />
                  </div>
                  <textarea
                    className='field min-h-[88px]'
                    placeholder='Your answer…'
                    value={fqAnswers[i] ?? ''}
                    onChange={e => {
                      setFqAnswers(prev => {
                        const n = [...prev];
                        n[i] = e.target.value;
                        return n;
                      });
                    }}
                  />
                  <div className='flex justify-end'>
                    <VoiceInput
                      label={`Dictate answer to Q${i + 1}`}
                      disabled={!!busy}
                      onPaywallError={upgrade.show}
                      onTranscript={(t) =>
                        setFqAnswers((prev) => {
                          const n = [...prev];
                          const existing = n[i] ?? '';
                          n[i] = existing ? `${existing.trimEnd()} ${t}` : t;
                          return n;
                        })
                      }
                    />
                  </div>
                </li>
              ))}
            </ol>
            <button
              type='button'
              className='btn-indigo'
              disabled={!!busy}
              onClick={() => void runDeliberation()}
            >
              Select the council
              <span aria-hidden>→</span>
            </button>
          </section>
        )}

        {step === 2 && (
          <section className='space-y-6'>
            <div>
              <h3 className='font-display text-[1.375rem] text-ink mb-1.5'>
                The council has been assembled.
              </h3>
              <p className='text-[15px] text-ink-slate max-w-[56ch]'>
                Selected from sixteen seats based on the evidence you provided.
              </p>
            </div>

            {deliberationCaseSummary && (
              <div className='rounded-xl bg-paper-deep border border-line p-5'>
                <p className='mono-label mb-2'>Case summary</p>
                <p className='text-[15px] text-ink leading-relaxed'>
                  {deliberationCaseSummary}
                </p>
              </div>
            )}

            {deliberationFocusAreas.length > 0 && (
              <div className='flex flex-wrap gap-2'>
                {deliberationFocusAreas.map(f => (
                  <span
                    key={f}
                    className='inline-flex items-center gap-1.5 text-[13px] px-3 py-1 rounded-full bg-indigo-soft border border-line-strong text-ink-muted'
                  >
                    <span className='h-1 w-1 rounded-full bg-indigo' /> {f}
                  </span>
                ))}
              </div>
            )}

            <ol className='grid sm:grid-cols-2 gap-3'>
              {councilRoster.map((p, i) => (
                <li
                  key={p.id}
                  className='flex items-center gap-4 p-4 rounded-xl border border-line bg-surface'
                >
                  <span className='flex items-center justify-center h-11 w-11 rounded-full border border-line-strong bg-periwinkle-soft font-display text-[15px] text-indigo shrink-0'>
                    {p.initials}
                  </span>
                  <div className='min-w-0'>
                    <p className='plate-counter mb-0.5'>
                      Seat {String(i + 1).padStart(2, '0')}
                    </p>
                    <p className='font-display text-[1.0625rem] text-ink truncate'>
                      {p.name}
                    </p>
                    <p className='text-[13px] text-ink-slate truncate'>
                      {p.specialty}
                    </p>
                  </div>
                </li>
              ))}
            </ol>

            <p className='mono-label text-ink-muted flex items-center gap-2'>
              <span className='h-1 w-1 rounded-full bg-cornflower atlas-pulse' />
              next <span className='diamond' /> specialist deliberation
            </p>
          </section>
        )}

        {step === 3 && (
          <section className='space-y-6'>
            <div>
              <h3 className='font-display text-[1.375rem] text-ink mb-1.5'>
                Specialist notes.
              </h3>
              <p className='text-[15px] text-ink-slate max-w-[56ch]'>
                Each physician read the prior assessments before writing their
                own. Disagreement is welcome.
              </p>
            </div>
            <ol className='space-y-4'>
              {physicians.map((p, i) => (
                <li key={p.id} className='plate-card p-6 relative'>
                  <div className='flex items-baseline justify-between gap-4 mb-3 pb-3 border-b border-line'>
                    <div className='flex items-center gap-3 min-w-0'>
                      <span className='plate-counter text-ink-faint shrink-0'>
                        {String(i + 1).padStart(2, '0')}
                      </span>
                      <span className='font-display text-[1.125rem] text-ink truncate'>
                        {p.name}
                      </span>
                      <span className='diamond shrink-0' />
                      <span className='text-[13px] text-ink-muted italic truncate'>
                        {p.specialty}
                      </span>
                    </div>
                  </div>
                  <pre className='whitespace-pre-wrap font-sans text-[14.5px] text-ink-slate leading-relaxed'>
                    {p.assessment}
                  </pre>
                </li>
              ))}
            </ol>
            <p className='mono-label text-ink-muted flex items-center gap-2'>
              <span className='h-1 w-1 rounded-full bg-cornflower atlas-pulse' />
              next <span className='diamond' /> literature research
            </p>
          </section>
        )}

        {step === 4 && (
          <section className='space-y-5'>
            <div>
              <h3 className='font-display text-[1.375rem] text-ink mb-1.5'>
                Literature.
              </h3>
              <p className='text-[15px] text-ink-slate max-w-[56ch]'>
                A focused search of the record, triangulated against the
                council&apos;s notes.
              </p>
            </div>
            {parseWarning && (
              <div className='rounded-xl border border-line-strong bg-periwinkle-soft px-4 py-2.5 text-[13px] text-ink-muted'>
                {parseWarning}
              </div>
            )}
            <ol className='space-y-3'>
              {research.map((r, i) => (
                <li
                  key={i}
                  className='p-5 rounded-xl border border-line bg-surface flex gap-4'
                >
                  <span className='plate-counter text-ink-faint shrink-0 w-8 pt-1'>
                    {String(i + 1).padStart(2, '0')}
                  </span>
                  <div className='min-w-0 flex-1'>
                    <p className='font-display text-[1.0625rem] text-ink leading-snug mb-1'>
                      {String(r.title ?? '')}
                    </p>
                    <p className='text-[13px] text-ink-slate'>
                      {String(r.authors ?? '')} <span className='diamond' />{' '}
                      <em className='not-italic text-ink-muted'>
                        {String(r.journal ?? '')}
                      </em>{' '}
                      <span className='diamond' /> {String(r.year ?? '')}
                    </p>
                    {typeof r.url === 'string' && r.url ? (
                      <a
                        href={r.url}
                        target='_blank'
                        rel='noreferrer'
                        className='text-[13px] text-indigo hover:text-cornflower inline-flex items-center gap-1.5 mt-2'
                      >
                        <span>PubMed / source</span>
                        <span aria-hidden>↗</span>
                      </a>
                    ) : null}
                  </div>
                </li>
              ))}
            </ol>
            <p className='mono-label text-ink-muted flex items-center gap-2'>
              <span className='h-1 w-1 rounded-full bg-cornflower atlas-pulse' />
              next <span className='diamond' /> cross-specialty consensus
            </p>
          </section>
        )}

        {step === 5 && consensus && (
          <section className='space-y-5'>
            <div>
              <h3 className='font-display text-[1.375rem] text-ink mb-1.5'>
                Consensus.
              </h3>
              <p className='text-[15px] text-ink-slate max-w-[56ch]'>
                Where the specialists converged. Where they did not, the
                disagreement is preserved.
              </p>
            </div>
            <ConsensusView consensus={consensus} />
            <p className='mono-label text-ink-muted flex items-center gap-2'>
              <span className='h-1 w-1 rounded-full bg-cornflower atlas-pulse' />
              next <span className='diamond' /> coordinated plan
            </p>
          </section>
        )}

        {step === 6 && (
          <section className='space-y-5'>
            <div>
              <h3 className='font-display text-[1.375rem] text-ink mb-1.5'>
                Plan.
              </h3>
              <p className='text-[15px] text-ink-slate max-w-[56ch]'>
                A single coordinated plan, drafted against the consensus and the
                specialists&apos; notes.
              </p>
            </div>
            <div className='rounded-xl border border-line bg-surface p-6'>
              <Markdown>{plan}</Markdown>
            </div>
            <p className='mono-label text-ink-muted flex items-center gap-2'>
              <span className='h-1 w-1 rounded-full bg-cornflower atlas-pulse' />
              next <span className='diamond' /> patient report
            </p>
          </section>
        )}

        {step === 7 && (
          <section className='space-y-8'>
            <div className='flex items-start justify-between gap-4'>
              <div>
                <h3 className='font-display text-[1.375rem] text-ink mb-1.5'>
                  For the patient.
                </h3>
                <p className='text-[15px] text-ink-slate max-w-[56ch]'>
                  Plain language. Actionable. Written to be read at the kitchen
                  table.
                </p>
              </div>
              <div className='flex items-center gap-2'>
                <VoiceOutput text={message} label='Read patient message aloud' onPaywallError={upgrade.show} />
              </div>
            </div>

            <EmailToPatient
              consensus={consensus}
              plan={plan}
              message={message}
              onPaywallError={upgrade.show}
            />

            <div className='rounded-xl border border-line bg-surface p-6'>
              <Markdown>{message}</Markdown>
            </div>

            <div className='rounded-xl border border-line-strong bg-paper-deep p-6 space-y-4'>
              <div className='flex items-baseline justify-between'>
                <p className='font-display text-[1.125rem] text-ink'>
                  Follow-up question
                </p>
                <span className='mono-label'>
                  Q &amp; A <span className='diamond' /> extended
                </span>
              </div>
              <textarea
                className='field min-h-[96px]'
                placeholder='Ask a clarifying question…'
                value={followupQ}
                onChange={e => setFollowupQ(e.target.value)}
              />
              <textarea
                className='field min-h-[72px]'
                placeholder='Optional: prior diagnostics / labs the patient mentions'
                value={followupPrior}
                onChange={e => setFollowupPrior(e.target.value)}
              />
              <div className='flex items-center gap-3'>
                <button
                  type='button'
                  className='btn-ghost h-11'
                  disabled={!followupQ.trim() || !!busy}
                  onClick={() => void runFollowup()}
                >
                  Ask follow-up
                  <span aria-hidden>→</span>
                </button>
                <VoiceInput
                  label='Dictate follow-up question'
                  disabled={!!busy}
                  onPaywallError={upgrade.show}
                  onTranscript={(t) =>
                    setFollowupQ((s) => (s ? `${s.trimEnd()} ${t}` : t))
                  }
                />
              </div>
              {followupReply && (
                <div className='mt-4 border-t border-line pt-4'>
                  <div className='flex items-start justify-between gap-3 mb-2'>
                    <span className='mono-label'>Council reply</span>
                    <VoiceOutput text={followupReply} label='Read reply aloud' onPaywallError={upgrade.show} />
                  </div>
                  <Markdown>{followupReply}</Markdown>
                </div>
              )}
            </div>

            <button
              type='button'
              className='mono-label text-indigo hover:text-cornflower inline-flex items-center gap-2 transition-colors'
              onClick={resetWithConfirm}
            >
              <span aria-hidden>+</span> begin a new case
            </button>
          </section>
        )}
      </StageFrame>
    </div>
  );
}

function StageFrame({
  numeral,
  label,
  children,
}: {
  numeral: string;
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className='relative grid grid-cols-12 gap-4 md:gap-8'>
      {/* Decorative numeral column */}
      <div className='hidden md:flex md:col-span-2 relative pt-1 justify-end'>
        <div className='sticky top-8 flex flex-col items-end gap-2'>
          <span className='plate-numeral text-[5.5rem] text-indigo/25'>
            {numeral}
          </span>
          <span className='mono-label text-ink-muted'>Stage {numeral}</span>
          <span className='font-display italic text-[15px] text-ink-muted text-right max-w-[10ch] leading-tight'>
            {label}
          </span>
        </div>
      </div>

      {/* Content column */}
      <div className='col-span-12 md:col-span-10 md:border-l md:border-line md:pl-8 min-w-0'>
        <div className='md:hidden flex items-baseline gap-3 mb-5'>
          <span className='plate-numeral text-[2.5rem] text-indigo'>
            {numeral}
          </span>
          <span className='stage-marker'>
            Stage {numeral} <span className='diamond' /> {label}
          </span>
        </div>
        {children}
      </div>
    </div>
  );
}
