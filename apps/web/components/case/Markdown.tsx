"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

/**
 * Strip common ways a model can accidentally defeat markdown rendering:
 *   1. The whole output wrapped in a ```markdown fenced block.
 *   2. The whole output JSON-encoded (a single quoted string with \n escapes).
 *   3. Leading/trailing whitespace that pushes headings off the first column.
 * Normal prose passes through untouched.
 */
function normalize(src: string): string {
  let s = (src ?? "").trim();
  if (!s) return s;

  // 1. Whole-document fenced block: ```md / ```markdown / plain ``` — unwrap once.
  const fenced = s.match(/^```(?:md|markdown|)\s*\n?([\s\S]*?)\n?```$/);
  if (fenced) s = fenced[1].trim();

  // 2. JSON-encoded string: "…\n…\n…". Decode if the whole thing round-trips.
  if (s.length >= 2 && s.startsWith('"') && s.endsWith('"')) {
    try {
      const parsed = JSON.parse(s);
      if (typeof parsed === "string") s = parsed.trim();
    } catch {
      /* not valid JSON — leave as is */
    }
  }

  return s;
}

export function Markdown({ children }: { children: string }) {
  return (
    <div className="prose-atlas">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>
        {normalize(children)}
      </ReactMarkdown>
    </div>
  );
}
