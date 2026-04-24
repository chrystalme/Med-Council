import Link from "next/link";

export default function NotFound() {
  return (
    <main className="min-h-screen flex items-center justify-center px-8 py-20 bg-[var(--paper)] text-[var(--ink)]">
      <div className="max-w-xl w-full border border-[var(--line)] bg-[var(--surface)] rounded-lg px-10 py-12">
        <p className="text-xs uppercase tracking-[0.2em] text-[var(--ink-faint)] mb-4">
          404
        </p>
        <h1 className="font-serif text-3xl leading-tight text-[var(--ink)] mb-3">
          That page is not in the atlas.
        </h1>
        <p className="text-[var(--ink-slate)] mb-8">
          The URL you followed does not match any route the council serves.
        </p>
        <Link
          href="/"
          className="inline-block px-5 py-2.5 bg-[var(--indigo)] hover:bg-[var(--indigo-hover)] text-white rounded-md text-sm font-medium transition-colors"
        >
          Return home
        </Link>
      </div>
    </main>
  );
}
