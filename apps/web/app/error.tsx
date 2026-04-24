"use client";

import { useEffect } from "react";
import Link from "next/link";

export default function Error({
  error,
  unstable_retry,
}: {
  error: Error & { digest?: string };
  unstable_retry: () => void;
}) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <main className="min-h-screen flex items-center justify-center px-8 py-20 bg-[var(--paper)] text-[var(--ink)]">
      <div className="max-w-xl w-full border border-[var(--line)] bg-[var(--surface)] rounded-lg px-10 py-12">
        <p className="text-xs uppercase tracking-[0.2em] text-[var(--ink-faint)] mb-4">
          Unexpected error
        </p>
        <h1 className="font-serif text-3xl leading-tight text-[var(--ink)] mb-3">
          Something went wrong.
        </h1>
        <p className="text-[var(--ink-slate)] mb-8">
          A rendering error interrupted this view. The council records a digest
          below so engineering can match it against server logs.
        </p>
        {error.digest ? (
          <p className="font-mono text-xs text-[var(--ink-faint)] mb-8 break-all">
            digest: {error.digest}
          </p>
        ) : null}
        <div className="flex gap-3">
          <button
            type="button"
            onClick={() => unstable_retry()}
            className="px-5 py-2.5 bg-[var(--indigo)] hover:bg-[var(--indigo-hover)] text-white rounded-md text-sm font-medium transition-colors"
          >
            Try again
          </button>
          <Link
            href="/"
            className="px-5 py-2.5 border border-[var(--line-strong)] text-[var(--ink-muted)] rounded-md text-sm font-medium hover:bg-[var(--paper-deep)] transition-colors"
          >
            Return home
          </Link>
        </div>
      </div>
    </main>
  );
}
