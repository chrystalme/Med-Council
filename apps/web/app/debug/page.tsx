import Link from "next/link";
import { DebugMe } from "@/components/case/DebugMe";

export default function DebugPage() {
  return (
    <div className="min-h-screen px-6 md:px-14 py-14 max-w-4xl mx-auto">
      <div className="flex items-center gap-3 mb-6">
        <Link href="/case" className="mono-label hover:text-indigo transition-colors">
          ← Workspace
        </Link>
      </div>
      <h1 className="font-display text-[2.25rem] leading-tight text-ink mb-3">
        Billing debug.
      </h1>
      <p className="text-[15px] text-ink-slate max-w-[56ch] leading-relaxed mb-8">
        Hits <code className="font-mono text-[13px]">/api/me?debug=1</code>{" "}
        with your Clerk session token attached, and dumps the result below.
        Unlike typing the URL in the browser bar, this request is
        authenticated — so the server sees your actual user and can decode
        the real JWT claims.
      </p>
      <DebugMe />
    </div>
  );
}
