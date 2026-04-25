"use client";

import { useAuth } from "@clerk/nextjs";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  CouncilApiError,
  councilFetch,
  councilJson,
  type CouncilErrorDetail,
} from "@/lib/council-api";

export type AttachmentRow = {
  id: string;
  kind: "file" | "pasted";
  filename: string | null;
  mime_type: string | null;
  size_bytes: number;
  text_preview: string;
  created_at: string;
};

const ACCEPT = ".pdf,.txt,.md,.csv,.json,image/*";

function humanSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/**
 * Case-level test-results panel. One document per case — the council reads it
 * alongside every follow-up question, so there's no per-question tagging.
 *
 * Accepts a file upload OR pasted text (e.g. pasted lab values). Lists the
 * current attachment so it can be replaced or removed.
 */
export function TestAttachment({
  caseId,
  disabled = false,
  onChange,
  onPaywallError,
}: {
  caseId: string | null;
  disabled?: boolean;
  onChange?: () => void;
  onPaywallError?: (err: unknown) => void;
}) {
  const { getToken } = useAuth();
  const [rows, setRows] = useState<AttachmentRow[]>([]);
  const [pasted, setPasted] = useState("");
  const [status, setStatus] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement | null>(null);
  const pasteTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const refresh = useCallback(async () => {
    if (!caseId) {
      setRows([]);
      return;
    }
    try {
      const tok = await getToken().catch(() => null);
      const data = await councilJson<{ attachments: AttachmentRow[] }>(
        `/api/cases/${caseId}/attachments`,
        { method: "GET", token: tok }
      );
      setRows(data.attachments ?? []);
    } catch {
      /* ignore */
    }
  }, [caseId, getToken]);

  useEffect(() => {
    // Refresh the attachment list on mount and whenever the caseId closure
    // inside `refresh` changes — this is the on-mount fetch pattern.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void refresh();
  }, [refresh]);

  const uploadForm = useCallback(
    async (fd: FormData) => {
      if (disabled) return;
      if (!caseId) {
        setErr("Save the case first before attaching.");
        return;
      }
      setStatus("Saving…");
      setErr(null);
      try {
        const tok = await getToken().catch(() => null);
        const res = await councilFetch(`/api/cases/${caseId}/attachments`, {
          method: "POST",
          token: tok,
          body: fd,
        });
        if (!res.ok) {
          const body = await res.text();
          let msg = body.slice(0, 200);
          let code: string | undefined;
          let detail: CouncilErrorDetail | string | undefined = body;
          try {
            const parsed = JSON.parse(body);
            detail = parsed?.detail;
            if (detail && typeof detail === "object") {
              msg = (detail as CouncilErrorDetail).message ?? msg;
              code = (detail as CouncilErrorDetail).code;
            } else if (typeof detail === "string") {
              msg = detail;
            }
          } catch {
            /* ignore */
          }
          throw new CouncilApiError(msg, { status: res.status, code, detail });
        }
        await refresh();
        onChange?.();
        setStatus("Saved");
        setTimeout(() => setStatus(null), 1500);
      } catch (e) {
        setErr(e instanceof Error ? e.message : "Upload failed");
        setStatus(null);
        onPaywallError?.(e);
      }
    },
    [caseId, disabled, getToken, onChange, onPaywallError, refresh]
  );

  const onFileChange = useCallback(
    async (e: React.ChangeEvent<HTMLInputElement>) => {
      const f = e.target.files?.[0];
      if (!f) return;
      const fd = new FormData();
      fd.append("file", f);
      fd.append("kind", "file");
      await uploadForm(fd);
      if (fileRef.current) fileRef.current.value = "";
    },
    [uploadForm]
  );

  const commitPasted = useCallback(
    async (text: string) => {
      if (!text.trim()) return;
      const fd = new FormData();
      fd.append("text", text);
      fd.append("kind", "pasted");
      await uploadForm(fd);
      setPasted("");
    },
    [uploadForm]
  );

  const onPasteChange = (val: string) => {
    setPasted(val);
    if (pasteTimer.current) clearTimeout(pasteTimer.current);
    pasteTimer.current = setTimeout(() => {
      void commitPasted(val);
    }, 900);
  };

  const onDelete = useCallback(
    async (id: string) => {
      if (!caseId) return;
      if (disabled) return;
      try {
        const tok = await getToken().catch(() => null);
        await councilJson(`/api/cases/${caseId}/attachments/${id}`, {
          method: "DELETE",
          token: tok,
        });
        await refresh();
        onChange?.();
      } catch {
        /* ignore */
      }
    },
    [caseId, disabled, getToken, onChange, refresh]
  );

  const totalBytes = rows.reduce((n, r) => n + (r.size_bytes || 0), 0);

  return (
    <div className="rounded-xl border border-line bg-paper-deep/40 p-4 space-y-3">
      <div className="flex items-baseline justify-between gap-3">
        <div>
          <p className="mono-label">
            Test results <span className="diamond" /> optional
          </p>
          <p className="text-[13px] text-ink-slate mt-0.5 max-w-[56ch]">
            One document covers every question — upload a lab PDF, imaging
            report, or paste the values. The council reads it alongside the
            full case.
          </p>
        </div>
        <span className="mono-label text-ink-faint shrink-0">
          {rows.length} file{rows.length === 1 ? "" : "s"}
          {rows.length > 0 && (
            <>
              {" "}
              <span className="diamond" /> {humanSize(totalBytes)}
            </>
          )}
        </span>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <label
          className={[
            "inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full border text-[13px] cursor-pointer transition-colors",
            caseId && !disabled
              ? "border-line-strong text-ink-muted hover:border-indigo hover:text-indigo"
              : "border-line text-ink-faint opacity-60 cursor-not-allowed",
          ].join(" ")}
        >
          <span aria-hidden>📎</span> Attach document
          <input
            ref={fileRef}
            type="file"
            accept={ACCEPT}
            className="hidden"
            disabled={!caseId || disabled}
            onChange={onFileChange}
          />
        </label>

        {status && (
          <span className="mono-label text-cornflower">{status}</span>
        )}
        {err && (
          <span className="mono-label text-urgent" title={err}>
            error
          </span>
        )}
      </div>

      <textarea
        className="field min-h-[60px] text-[13.5px]"
        placeholder="Or paste the test values here (auto-saves after a short pause)…"
        disabled={!caseId || disabled}
        value={pasted}
        onChange={(e) => onPasteChange(e.target.value)}
      />

      {rows.length > 0 && (
        <ul className="space-y-1.5">
          {rows.map((r) => (
            <li
              key={r.id}
              className="flex items-start justify-between gap-3 text-[13px] bg-surface border border-line rounded-lg p-2.5"
            >
              <div className="min-w-0 flex-1">
                <div className="flex items-baseline gap-2 flex-wrap">
                  <p className="font-medium text-ink truncate">
                    {r.kind === "file"
                      ? r.filename || "attachment"
                      : "pasted text"}
                  </p>
                  <span className="mono-label text-ink-faint">
                    {humanSize(r.size_bytes)}
                  </span>
                </div>
                <p className="text-ink-slate truncate mt-0.5">
                  {(r.text_preview || "").slice(0, 140)}
                  {r.text_preview && r.text_preview.length > 140 ? "…" : ""}
                </p>
              </div>
              <button
                type="button"
                onClick={() => void onDelete(r.id)}
                disabled={disabled}
                className="mono-label text-ink-muted hover:text-urgent transition-colors shrink-0"
                title="Remove attachment"
              >
                ✕
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
