"use client";

import { useAuth } from "@clerk/nextjs";
import { useCallback, useState } from "react";
import { councilJson } from "@/lib/council-api";
import { useIsPro } from "@/lib/entitlements";

type Props = {
  consensus: Record<string, unknown> | null;
  plan: string;
  message: string;
  defaultName?: string;
  onPaywallError?: (err: unknown) => void;
};

const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

export function EmailToPatient({
  consensus,
  plan,
  message,
  defaultName,
  onPaywallError,
}: Props) {
  const { getToken } = useAuth();
  const isPro = useIsPro();

  const [open, setOpen] = useState(false);
  const [to, setTo] = useState("");
  const [name, setName] = useState(defaultName ?? "");
  const [subject, setSubject] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [sent, setSent] = useState(false);

  const canSend = isPro && EMAIL_RE.test(to.trim()) && !busy && (plan.trim().length > 0 || message.trim().length > 0);

  const send = useCallback(async () => {
    setBusy(true);
    setErr(null);
    setSent(false);
    try {
      const tok = await getToken().catch(() => null);
      await councilJson("/api/patient/email", {
        method: "POST",
        token: tok,
        body: JSON.stringify({
          to: to.trim(),
          patient_name: name.trim() || null,
          subject: subject.trim() || null,
          consensus,
          plan,
          message,
        }),
      });
      setSent(true);
      setTimeout(() => {
        setOpen(false);
        setSent(false);
      }, 1600);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Send failed");
      onPaywallError?.(e);
    } finally {
      setBusy(false);
    }
  }, [consensus, getToken, message, name, onPaywallError, plan, subject, to]);

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => {
          if (!isPro) {
            onPaywallError?.({
              status: 402,
              code: "email_premium",
              message: "Emailing the summary requires Pro.",
            });
            return;
          }
          setOpen(true);
        }}
        className={[
          "inline-flex items-center gap-2 rounded-full border text-[13.5px] px-4 py-1.5 transition-colors",
          isPro
            ? "border-indigo bg-indigo text-paper hover:bg-indigo-hover"
            : "border-line-strong text-ink-muted hover:border-indigo hover:text-indigo",
        ].join(" ")}
      >
        <span aria-hidden>✉</span>
        Email summary to patient
        {!isPro && <span className="mono-label opacity-70">pro</span>}
      </button>
    );
  }

  return (
    <div className="rounded-xl border border-line-strong bg-surface p-5 space-y-3">
      <div className="flex items-baseline justify-between">
        <p className="font-display text-[1.125rem] text-ink">
          Email this summary
        </p>
        <span className="mono-label text-indigo">pro feature</span>
      </div>
      <p className="text-[13.5px] text-ink-slate leading-relaxed">
        Sends the plan and patient message as a formatted email via Resend.
        The reply-to is your Clerk account address.
      </p>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <label className="block space-y-1">
          <span className="mono-label">To</span>
          <input
            type="email"
            autoComplete="email"
            className="field text-[13.5px] py-2"
            placeholder="patient@example.com"
            value={to}
            onChange={(e) => setTo(e.target.value)}
          />
        </label>
        <label className="block space-y-1">
          <span className="mono-label">Patient name (optional)</span>
          <input
            type="text"
            className="field text-[13.5px] py-2"
            placeholder="e.g. Alex"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </label>
      </div>

      <label className="block space-y-1">
        <span className="mono-label">Subject (optional)</span>
        <input
          type="text"
          className="field text-[13.5px] py-2"
          placeholder="Your MedAI Council consultation summary"
          value={subject}
          onChange={(e) => setSubject(e.target.value)}
        />
      </label>

      {err && (
        <p className="text-[13px] text-urgent bg-urgent-soft border border-urgent/30 rounded-lg px-3 py-2">
          {err}
        </p>
      )}
      {sent && (
        <p className="text-[13px] text-ink bg-slate-soft border border-line-strong rounded-lg px-3 py-2">
          ✓ Email sent.
        </p>
      )}

      <div className="flex items-center justify-end gap-2 pt-1">
        <button
          type="button"
          onClick={() => setOpen(false)}
          className="mono-label text-ink-muted hover:text-ink transition-colors px-3 py-1"
        >
          cancel
        </button>
        <button
          type="button"
          onClick={() => void send()}
          disabled={!canSend}
          className="btn-indigo h-10"
        >
          {busy ? "Sending…" : "Send email"}
        </button>
      </div>
    </div>
  );
}
