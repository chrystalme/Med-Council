"use client";

import { useAuth } from "@clerk/nextjs";
import { useCallback, useEffect, useRef, useState } from "react";
import { CouncilApiError, councilFetch, type CouncilErrorDetail } from "@/lib/council-api";
import { useClientMounted, useIsPro } from "@/lib/entitlements";

type Props = {
  onTranscript: (text: string) => void;
  /** Disable the mic while an upstream step is busy. */
  disabled?: boolean;
  /** Short label for the screen-reader. */
  label?: string;
  /** Called on 402/415 server errors so callers can show an upgrade modal. */
  onPaywallError?: (err: unknown) => void;
};

type Mode = "browser" | "server";

// Narrowly-typed browser SpeechRecognition. Chrome/Edge expose `webkitSpeechRecognition`.
type Recognition = {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  onresult: ((e: { results: ArrayLike<{ 0: { transcript: string }; isFinal: boolean }>; resultIndex: number }) => void) | null;
  onerror: ((e: { error: string }) => void) | null;
  onend: (() => void) | null;
  start(): void;
  stop(): void;
};

function createBrowserRecognition(): Recognition | null {
  if (typeof window === "undefined") return null;
  const Ctor =
    (window as unknown as { SpeechRecognition?: new () => Recognition }).SpeechRecognition ??
    (window as unknown as { webkitSpeechRecognition?: new () => Recognition }).webkitSpeechRecognition;
  if (!Ctor) return null;
  const rec = new Ctor();
  rec.lang = (typeof navigator !== "undefined" && navigator.language) || "en-US";
  rec.continuous = false;
  rec.interimResults = true;
  return rec;
}

export function VoiceInput({ onTranscript, disabled, label = "Voice input", onPaywallError }: Props) {
  const { getToken } = useAuth();
  const { isPro } = useIsPro();

  const [listening, setListening] = useState(false);
  const [processing, setProcessing] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const recRef = useRef<Recognition | null>(null);
  const mediaRecRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<BlobPart[]>([]);
  const committedRef = useRef<string>("");

  const mounted = useClientMounted();
  const mode: Mode = mounted && isPro ? "server" : "browser";

  // Detect browser support for Free tier. Deferred to a post-mount effect
  // so SSR output matches the first client render (otherwise `typeof window`
  // diverges and React complains about hydration mismatches on `disabled` /
  // `title` attrs).
  const [browserSupported, setBrowserSupported] = useState(false);
  useEffect(() => {
    const supported = !!(
      (window as unknown as { SpeechRecognition?: unknown }).SpeechRecognition ||
      (window as unknown as { webkitSpeechRecognition?: unknown }).webkitSpeechRecognition
    );
    queueMicrotask(() => setBrowserSupported(supported));
  }, []);

  const stopBrowser = useCallback(() => {
    try {
      recRef.current?.stop();
    } catch {
      /* ignore */
    }
  }, []);

  const stopServer = useCallback(() => {
    try {
      mediaRecRef.current?.stop();
    } catch {
      /* ignore */
    }
  }, []);

  // Cleanup on unmount.
  useEffect(() => {
    return () => {
      stopBrowser();
      stopServer();
    };
  }, [stopBrowser, stopServer]);

  const startBrowser = useCallback(() => {
    setErr(null);
    const rec = createBrowserRecognition();
    if (!rec) {
      setErr("Voice input needs Chrome or Edge, or upgrade to Pro.");
      return;
    }
    recRef.current = rec;
    committedRef.current = "";

    rec.onresult = (e) => {
      let finalChunk = "";
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const r = e.results[i];
        if (r.isFinal) finalChunk += r[0].transcript;
      }
      if (finalChunk) {
        committedRef.current += (committedRef.current ? " " : "") + finalChunk.trim();
      }
    };
    rec.onerror = (e) => {
      if (e.error !== "aborted") setErr(`Mic error: ${e.error}`);
    };
    rec.onend = () => {
      setListening(false);
      const text = committedRef.current.trim();
      if (text) onTranscript(text);
    };

    try {
      rec.start();
      setListening(true);
    } catch (exc) {
      setErr(exc instanceof Error ? exc.message : "Could not start voice input.");
    }
  }, [onTranscript]);

  const startServer = useCallback(async () => {
    setErr(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mr = new MediaRecorder(stream, { mimeType: "audio/webm" });
      chunksRef.current = [];
      mediaRecRef.current = mr;

      mr.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data);
      };
      mr.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop());
        setListening(false);
        setProcessing(true);
        try {
          const blob = new Blob(chunksRef.current, { type: "audio/webm" });
          const fd = new FormData();
          fd.append("audio", blob, "audio.webm");
          const tok = await getToken().catch(() => null);
          const res = await councilFetch("/api/speech/transcribe", {
            method: "POST",
            token: tok,
            body: fd,
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
          const data = (await res.json()) as { text: string };
          if (data.text) onTranscript(data.text);
        } catch (exc) {
          setErr(exc instanceof Error ? exc.message : "Transcription failed.");
          onPaywallError?.(exc);
        } finally {
          setProcessing(false);
        }
      };

      mr.start();
      setListening(true);
    } catch (exc) {
      setErr(exc instanceof Error ? exc.message : "Mic access was denied.");
    }
  }, [getToken, onTranscript, onPaywallError]);

  const onClick = () => {
    if (disabled || processing) return;
    if (listening) {
      if (mode === "server") stopServer();
      else stopBrowser();
      return;
    }
    if (mode === "server") void startServer();
    else startBrowser();
  };

  const canStart = !disabled && !processing && (mode === "server" || browserSupported);

  return (
    <div className="inline-flex items-center gap-2">
      <button
        type="button"
        onClick={onClick}
        disabled={!canStart && !listening}
        suppressHydrationWarning
        aria-label={label}
        title={
          !browserSupported && mode === "browser"
            ? "Voice input needs Chrome or Edge, or upgrade to Pro"
            : label
        }
        className={[
          "inline-flex items-center justify-center h-8 w-8 rounded-full border transition-colors",
          listening
            ? "bg-cornflower border-cornflower text-paper"
            : processing
              ? "bg-periwinkle-soft border-line-strong text-ink-muted"
              : "bg-surface border-line-strong text-ink-muted hover:border-indigo hover:text-indigo",
          !canStart && !listening ? "opacity-40 cursor-not-allowed" : "cursor-pointer",
        ].join(" ")}
      >
        {/* Simple mic glyph */}
        <svg
          aria-hidden
          viewBox="0 0 24 24"
          width="14"
          height="14"
          fill="currentColor"
        >
          <path d="M12 14a3 3 0 0 0 3-3V6a3 3 0 0 0-6 0v5a3 3 0 0 0 3 3Zm5-3a5 5 0 0 1-10 0H5a7 7 0 0 0 6 6.92V21h2v-3.08A7 7 0 0 0 19 11h-2Z" />
        </svg>
      </button>
      {listening && (
        <span className="mono-label text-cornflower atlas-pulse">rec</span>
      )}
      {processing && (
        <span className="mono-label text-ink-muted atlas-pulse">transcribing</span>
      )}
      {err && (
        <span className="text-[11px] text-ink-muted" title={err}>
          · {err.length > 40 ? err.slice(0, 40) + "…" : err}
        </span>
      )}
    </div>
  );
}
