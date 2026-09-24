// Small shared presentational primitives for the B5 demo console.

import type { ReactNode } from "react";

import type { ReviewRouting } from "../lib/types";

export function Panel({
  title,
  children,
  right,
}: {
  title: string;
  children: ReactNode;
  right?: ReactNode;
}) {
  return (
    <section className="rounded-xl border border-ink-200 bg-white shadow-panel">
      <header className="flex items-center justify-between border-b border-ink-100 px-4 py-3">
        <h2 className="text-sm font-semibold text-ink-800">{title}</h2>
        {right}
      </header>
      <div className="p-4">{children}</div>
    </section>
  );
}

export function StatusBadge({ status }: { status: string }) {
  const color: Record<string, string> = {
    pass: "bg-emerald-100 text-emerald-800",
    warn: "bg-amber-100 text-amber-800",
    fail: "bg-rose-100 text-rose-800",
    verified: "bg-emerald-100 text-emerald-800",
    needs_review: "bg-amber-100 text-amber-800",
    inconsistent: "bg-rose-100 text-rose-800",
  };
  return (
    <span
      className={`rounded-full px-2 py-0.5 text-xs font-semibold uppercase tracking-wide ${
        color[status] ?? "bg-ink-100 text-ink-700"
      }`}
    >
      {status.replace(/_/g, " ")}
    </span>
  );
}

export function ReviewBanner({ routing }: { routing?: ReviewRouting }) {
  return (
    <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs font-medium text-amber-900">
      Human review required (maker-checker, P-06). The agent verifies; the underwriter
      decides. This is not a lending decision.
      {routing && routing !== "not_required" ? (
        <p
          data-review-routing={routing}
          className={`mt-1 ${routing === "routed" ? "text-emerald-800" : "text-rose-800"}`}
        >
          {REVIEW_ROUTING_TEXT[routing]}
        </p>
      ) : null}
    </div>
  );
}

// What happened to the human-review hand-off, in the words the underwriter needs. A case that
// requires review but is not queued must say so rather than read as reviewed.
const REVIEW_ROUTING_TEXT: Record<Exclude<ReviewRouting, "not_required">, string> = {
  routed: "Sent to the review console.",
  failed: "Could not reach the review console; this case is not queued for review.",
  off: "Review routing is off in this deployment; this case is not queued for review.",
};

/** Shown when redaction changed what the user submitted before the model saw it. */
export function RedactionNotice() {
  return (
    <div
      role="note"
      data-input-redacted="true"
      className="rounded-lg border border-sky-200 bg-sky-50 px-3 py-2 text-xs text-sky-900"
    >
      Personal data in your input was masked before the model saw it.
    </div>
  );
}

export function CitationList({
  citations,
}: {
  citations: { source_id: string; field: string; page: number | null; title: string }[];
}) {
  if (citations.length === 0) {
    return <p className="text-xs text-ink-400">(no citations)</p>;
  }
  return (
    <ul className="space-y-0.5">
      {citations.map((c, i) => (
        <li key={i} className="font-mono text-xs text-ink-500">
          [{c.source_id}
          {c.field ? `.${c.field}` : ""}
          {c.page != null ? ` p.${c.page}` : ""}] {c.title}
        </li>
      ))}
    </ul>
  );
}
