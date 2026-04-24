"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import type { CouncilApiError } from "@/lib/council-api";

type Prompt = {
  code?: string;
  title: string;
  message: string;
  cta?: { label: string; href: string };
};

const COPY: Record<string, Omit<Prompt, "code">> = {
  consultation_cap: {
    title: "You've reached the Free limit of 4 consultations.",
    message:
      "Delete an older consultation on the patient file, or upgrade to Pro for unlimited memory.",
    cta: { label: "Manage consultations", href: "/patient" },
  },
  attachment_cap: {
    title: "Attachment limit reached.",
    message:
      "Free tier allows up to 5 attachments per case. Upgrade to Pro for 20 × 10 MB attachments.",
  },
  attachment_size: {
    title: "Attachment is too large.",
    message:
      "Free tier caps attachments at 1 MB. Upgrade to Pro for 10 MB files.",
  },
  attachment_type: {
    title: "That file type isn't supported yet.",
    message: "We currently accept PDF, plain text, markdown, CSV, JSON, and images.",
  },
  voice_premium: {
    title: "High-quality voice requires Pro.",
    message:
      "Free tier uses your browser's built-in speech engine. Upgrade to Pro for Whisper + premium TTS voices.",
  },
  email_premium: {
    title: "Emailing summaries requires Pro.",
    message:
      "Upgrade to Pro to send the plan and patient message as a formatted email via Resend.",
  },
  email_not_configured: {
    title: "Email sending isn't set up yet.",
    message:
      "The server doesn't have RESEND_API_KEY and RESEND_FROM_EMAIL configured. Ask an admin to add them.",
  },
  email_send_failed: {
    title: "Couldn't send that email.",
    message: "The provider rejected the request. Please try again in a moment.",
  },
  premium_model: {
    title: "That model is Pro-only.",
    message: "We've routed this run to the default free model instead.",
  },
};

function fallback(message: string, code?: string): Prompt {
  return {
    code,
    title: code ? code.replace(/_/g, " ") : "Something needs your attention.",
    message,
  };
}

export function pickPrompt(err: CouncilApiError): Prompt | null {
  // 402 = paywall, 415 = unsupported media, 502/503 = provider misconfig.
  if (![402, 415, 502, 503].includes(err.status)) return null;
  const code = err.code;
  if (code && COPY[code]) {
    return { code, ...COPY[code] };
  }
  // Unknown code on one of these statuses — still surface a modal so the
  // user isn't left with a silent failure.
  return fallback(err.message, code);
}

/**
 * Mount once near the root (inside CaseWorkspace is fine) and call
 * `showUpgradePrompt(err)` after any councilJson throw. Keeps state local.
 */
export function UpgradeModal({
  prompt,
  onClose,
}: {
  prompt: Prompt | null;
  onClose: () => void;
}) {
  useEffect(() => {
    if (!prompt) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [prompt, onClose]);

  if (!prompt) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-ink/50 backdrop-blur-sm p-4"
      onClick={onClose}
    >
      <div
        className="plate-card max-w-md w-full p-6 space-y-4"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
      >
        <div className="flex items-center gap-2">
          <span className="h-1.5 w-1.5 rounded-full bg-cornflower" />
          <p className="mono-label">Pro feature</p>
        </div>
        <h3 className="font-display text-[1.375rem] text-ink leading-tight">
          {prompt.title}
        </h3>
        <p className="text-[15px] text-ink-slate leading-relaxed">
          {prompt.message}
        </p>
        <div className="flex items-center justify-end gap-3 pt-2">
          <button
            type="button"
            onClick={onClose}
            className="btn-ghost h-10 px-4"
          >
            Got it
          </button>
          {prompt.cta ? (
            <Link href={prompt.cta.href} className="btn-indigo">
              {prompt.cta.label}
            </Link>
          ) : (
            <Link href="/#pricing" className="btn-indigo">
              See Pro plan
            </Link>
          )}
        </div>
      </div>
    </div>
  );
}

export function useUpgradePrompt() {
  const [prompt, setPrompt] = useState<Prompt | null>(null);
  const show = useCallback((err: unknown) => {
    if (err && typeof err === "object" && "status" in err && "code" in err) {
      const p = pickPrompt(err as CouncilApiError);
      if (p) {
        setPrompt(p);
        return true;
      }
    }
    return false;
  }, []);
  const close = useCallback(() => setPrompt(null), []);
  return { prompt, show, close };
}
