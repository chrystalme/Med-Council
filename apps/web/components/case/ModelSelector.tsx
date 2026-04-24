"use client";

import { useAuth } from "@clerk/nextjs";
import { useCallback, useEffect, useState } from "react";
import { councilJson } from "@/lib/council-api";
import { useClientMounted } from "@/lib/entitlements";

type ModelOption = {
  key: string;
  id: string;
  label: string;
  tier: "free" | "pro";
  description: string;
  locked: boolean;
};

type ModelsResponse = {
  default: string;
  plan: "free" | "pro";
  models: ModelOption[];
};

const LS_MODEL = "medai_model_key";

export function ModelSelector({
  value,
  onChange,
  disabled,
}: {
  value: string;
  onChange: (key: string) => void;
  disabled?: boolean;
}) {
  const { getToken } = useAuth();
  const mounted = useClientMounted();
  const [state, setState] = useState<ModelsResponse | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const tok = await getToken().catch(() => null);
      const data = await councilJson<ModelsResponse>("/api/models", {
        method: "GET",
        token: tok,
      });
      setState(data);
      setErr(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Failed to load models");
    }
  }, [getToken]);

  useEffect(() => {
    // Fetch model list once when the component mounts — classic useEffect.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
  }, [load]);

  if (err) {
    // Fail silently — the selector is a nice-to-have; the backend silently
    // falls back to the default when the key is missing or invalid.
    return null;
  }

  // Don't render the live `<select>` until after mount. Server HTML and the
  // first client render both emit the skeleton placeholder, so there's nothing
  // for React to diff against a post-fetch state. Prevents hydration warnings
  // on the `disabled` attribute (which flips once `state` arrives).
  if (!mounted || !state) {
    return (
      <div className="inline-flex items-center gap-2" aria-busy="true">
        <span className="mono-label text-ink-faint">Model</span>
        <span className="skeleton h-7 w-40 rounded-full" aria-hidden />
      </div>
    );
  }

  const selected = state.models.find((m) => m.key === value)
    ?? state.models.find((m) => m.key === state.default);

  return (
    <div className="inline-flex items-center gap-2">
      <span className="mono-label text-ink-faint">Model</span>
      <div className="relative inline-flex">
        <select
          value={selected?.key ?? ""}
          disabled={!!disabled}
          onChange={(e) => {
            const next = e.target.value;
            onChange(next);
            try {
              localStorage.setItem(LS_MODEL, next);
            } catch {
              /* ignore private-mode */
            }
          }}
          className="appearance-none bg-surface border border-line-strong hover:border-line-deep focus:border-indigo focus:outline-none focus:ring-2 focus:ring-indigo-soft text-[13px] text-ink pl-3 pr-8 py-1.5 rounded-full transition-colors disabled:opacity-50 cursor-pointer"
        >
          {state.models.map((m) => (
            <option key={m.key} value={m.key} disabled={m.locked}>
              {m.label}
              {m.locked ? " (Pro)" : ""}
              {m.tier === "free" ? " · free" : ""}
            </option>
          ))}
        </select>
        <span
          aria-hidden
          className="pointer-events-none absolute inset-y-0 right-2.5 inline-flex items-center text-ink-faint"
        >
          ▾
        </span>
      </div>
      {selected?.tier === "pro" && !selected.locked && (
        <span className="mono-label text-indigo">pro</span>
      )}
      {selected?.locked && (
        <span
          className="mono-label text-ink-faint italic"
          title="Upgrade to Pro to use this model"
        >
          locked
        </span>
      )}
    </div>
  );
}

export function useStoredModelKey(defaultKey: string = "gemini-2-5-flash-lite-free") {
  const [key, setKey] = useState<string>(defaultKey);
  useEffect(() => {
    if (typeof window === "undefined") return;
    try {
      const stored = localStorage.getItem(LS_MODEL);
      // Hydrate the picker from localStorage after mount so SSR (which can't
      // read localStorage) and the first client render start identical.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      if (stored) setKey(stored);
    } catch {
      /* ignore */
    }
  }, []);
  return [key, setKey] as const;
}
