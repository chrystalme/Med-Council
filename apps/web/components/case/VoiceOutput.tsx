"use client";

import { useAuth } from "@clerk/nextjs";
import { useCallback, useEffect, useRef, useState } from "react";
import { CouncilApiError, councilFetch, type CouncilErrorDetail } from "@/lib/council-api";
import { useIsPro } from "@/lib/entitlements";

type Props = {
  text: string;
  /** Voice hint for server TTS; ignored by the browser engine. */
  voice?: string;
  /** Tooltip / SR label. */
  label?: string;
  disabled?: boolean;
  /** Called on 402/415 so the parent can open an upgrade modal. */
  onPaywallError?: (err: unknown) => void;
};

export function VoiceOutput({
  text,
  voice = "alloy",
  label = "Read aloud",
  disabled,
  onPaywallError,
}: Props) {
  const { getToken } = useAuth();
  const isPro = useIsPro();

  const [playing, setPlaying] = useState(false);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const browserUtterRef = useRef<SpeechSynthesisUtterance | null>(null);

  // Cleanup on unmount.
  useEffect(() => {
    return () => {
      try {
        audioRef.current?.pause();
        if (typeof window !== "undefined") window.speechSynthesis?.cancel();
      } catch {
        /* ignore */
      }
    };
  }, []);

  const stopAll = useCallback(() => {
    try {
      audioRef.current?.pause();
    } catch {
      /* ignore */
    }
    try {
      if (typeof window !== "undefined") window.speechSynthesis?.cancel();
    } catch {
      /* ignore */
    }
    setPlaying(false);
  }, []);

  const speakBrowser = useCallback(() => {
    setErr(null);
    if (typeof window === "undefined" || !window.speechSynthesis) {
      setErr("Browser TTS unavailable.");
      return;
    }
    const u = new SpeechSynthesisUtterance(text);
    u.rate = 1.02;
    u.pitch = 1;
    u.onend = () => setPlaying(false);
    u.onerror = () => setPlaying(false);
    browserUtterRef.current = u;
    window.speechSynthesis.cancel();
    window.speechSynthesis.speak(u);
    setPlaying(true);
  }, [text]);

  const speakServer = useCallback(async () => {
    setErr(null);
    setLoading(true);
    try {
      const tok = await getToken().catch(() => null);
      const res = await councilFetch("/api/speech/synthesize", {
        method: "POST",
        token: tok,
        body: JSON.stringify({ text, voice }),
      });
      if (!res.ok) {
        const body = await res.text();
        let msg = body.slice(0, 200) || `HTTP ${res.status}`;
        let code: string | undefined;
        let detail: CouncilErrorDetail | string | undefined = body;
        try {
          const parsed = JSON.parse(body);
          detail = parsed?.detail;
          if (detail && typeof detail === "object") {
            msg = (detail as CouncilErrorDetail).message ?? msg;
            code = (detail as CouncilErrorDetail).code;
          }
        } catch {
          /* ignore */
        }
        throw new CouncilApiError(msg, { status: res.status, code, detail });
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const audio = new Audio(url);
      audio.onended = () => {
        setPlaying(false);
        URL.revokeObjectURL(url);
      };
      audio.onerror = () => {
        setPlaying(false);
        URL.revokeObjectURL(url);
        setErr("Playback failed.");
      };
      audioRef.current = audio;
      await audio.play();
      setPlaying(true);
    } catch (exc) {
      setErr(exc instanceof Error ? exc.message : "TTS failed.");
      onPaywallError?.(exc);
    } finally {
      setLoading(false);
    }
  }, [getToken, text, voice, onPaywallError]);

  const onClick = () => {
    if (disabled || loading || !text.trim()) return;
    if (playing) {
      stopAll();
      return;
    }
    if (isPro) void speakServer();
    else speakBrowser();
  };

  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled || loading || !text.trim()}
      aria-label={label}
      title={label}
      className={[
        "inline-flex items-center justify-center h-7 w-7 rounded-full border transition-colors",
        playing
          ? "bg-cornflower border-cornflower text-paper"
          : "bg-transparent border-line-strong text-ink-muted hover:border-indigo hover:text-indigo",
        (disabled || loading || !text.trim()) && !playing
          ? "opacity-40 cursor-not-allowed"
          : "cursor-pointer",
        err ? "border-urgent/40" : "",
      ].join(" ")}
    >
      <svg
        aria-hidden
        viewBox="0 0 24 24"
        width="12"
        height="12"
        fill="currentColor"
      >
        {loading ? (
          <circle cx="12" cy="12" r="4">
            <animate attributeName="r" dur="1s" values="3;6;3" repeatCount="indefinite" />
          </circle>
        ) : (
          <path d="M3 10v4h4l5 5V5L7 10H3Zm13.5 2a4.5 4.5 0 0 0-2.5-4.03v8.06A4.5 4.5 0 0 0 16.5 12Z" />
        )}
      </svg>
    </button>
  );
}
