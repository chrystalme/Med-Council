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
  question_index: number | null;
  created_at: string;
};

const ACCEPT = ".pdf,.txt,.md,.csv,.json,image/*";

function humanSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function TestAttachment({
  caseId,
  questionIndex,
  onChange,
  onPaywallError,
}: {
  caseId: string | null;
  questionIndex: number;
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
    if (!caseId) return;
    try {
      const tok = await getToken().catch(() => null);
      const data = await councilJson<{ attachments: AttachmentRow[] }>(
        `/api/cases/${caseId}/attachments`,
        { method: "GET", token: tok }
      );
      setRows(
        (data.attachments ?? []).filter(
          (r) => r.question_index === questionIndex
        )
      );
    } catch {
      /* ignore */
    }
  }, [caseId, getToken, questionIndex]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const uploadForm = useCallback(
    async (fd: FormData) => {
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
          throw new CouncilApiError(msg, {
            status: res.status,
            code,
            detail,
          });
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
    [caseId, getToken, onChange, onPaywallError, refresh]
  );

  const onFileChange = useCallback(
    async (e: React.ChangeEvent<HTMLInputElement>) => {
      const f = e.target.files?.[0];
      if (!f) return;
      const fd = new FormData();
      fd.append("file", f);
      fd.append("kind", "file");
      fd.append("question_index", String(questionIndex));
      await uploadForm(fd);
      if (fileRef.current) fileRef.current.value = "";
    },
    [questionIndex, uploadForm]
  );

  const commitPasted = useCallback(
    async (text: string) => {
      if (!text.trim()) return;
      const fd = new FormData();
      fd.append("text", text);
      fd.append("kind", "pasted");
      fd.append("question_index", String(questionIndex));
      await uploadForm(fd);
      setPasted("");
    },
    [questionIndex, uploadForm]
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
    [caseId, getToken, onChange, refresh]
  );

  return (
    <div className="rounded-lg border border-line/70 bg-paper-deep/40 p-3 space-y-2">
      <div className="flex items-center justify-between">
        <span className="mono-label">Test results (optional)</span>
        <div className="flex items-center gap-2">
          {status && (
            <span className="mono-label text-cornflower">{status}</span>
          )}
          {err && (
            <span className="mono-label text-urgent" title={err}>
              error
            </span>
          )}
          <label
            className={[
              "mono-label inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full border cursor-pointer transition-colors",
              caseId
                ? "border-line-strong text-ink-muted hover:border-indigo hover:text-indigo"
                : "border-line text-ink-faint opacity-60 cursor-not-allowed",
            ].join(" ")}
          >
            <span aria-hidden>📎</span> attach file
            <input
              ref={fileRef}
              type="file"
              accept={ACCEPT}
              className="hidden"
              disabled={!caseId}
              onChange={onFileChange}
            />
          </label>
        </div>
      </div>

      <textarea
        className="field min-h-[52px] text-[13px]"
        placeholder="Or paste test values here (auto-saves)…"
        disabled={!caseId}
        value={pasted}
        onChange={(e) => onPasteChange(e.target.value)}
      />

      {rows.length > 0 && (
        <ul className="space-y-1.5">
          {rows.map((r) => (
            <li
              key={r.id}
              className="flex items-start justify-between gap-3 text-[13px] bg-surface border border-line rounded p-2"
            >
              <div className="min-w-0 flex-1">
                <p className="font-medium text-ink truncate">
                  {r.kind === "file"
                    ? r.filename || "attachment"
                    : "pasted text"}
                  <span className="ml-2 mono-label">
                    {humanSize(r.size_bytes)}
                  </span>
                </p>
                <p className="text-ink-slate truncate">
                  {(r.text_preview || "").slice(0, 100)}
                  {r.text_preview && r.text_preview.length > 100 ? "…" : ""}
                </p>
              </div>
              <button
                type="button"
                onClick={() => void onDelete(r.id)}
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
