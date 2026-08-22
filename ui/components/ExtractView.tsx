// Renders the per-document DocumentExtract list (fields + line items).

import type { DocumentExtract } from "../lib/types";
import { Panel } from "./ui";

export function ExtractView({ extracts }: { extracts: DocumentExtract[] }) {
  if (extracts.length === 0) {
    return null;
  }
  return (
    <Panel title="Document extracts">
      <ul className="space-y-3">
        {extracts.map((e) => (
          <li key={e.document_id} className="rounded-lg border border-ink-100 p-3">
            <div className="flex items-center justify-between">
              <span className="font-mono text-xs text-ink-700">{e.document_id}</span>
              <span className="text-xs text-ink-400">
                {e.doc_type} · {(e.confidence * 100).toFixed(0)}% confidence
              </span>
            </div>
            <dl className="mt-2 grid grid-cols-[8rem_1fr] gap-x-3 gap-y-0.5 text-xs text-ink-600">
              {Object.entries(e.fields).map(([k, v]) => (
                <div key={k} className="contents">
                  <dt className="text-ink-400">{k}</dt>
                  <dd className="font-mono">{v}</dd>
                </div>
              ))}
            </dl>
            {e.line_items.length > 0 ? (
              <ul className="mt-2 space-y-0.5 text-xs text-ink-600">
                {e.line_items.map((li, i) => (
                  <li key={i} className="font-mono">
                    {li.label}: {li.amount.toLocaleString()} {li.currency}
                    {li.date ? ` (${li.date})` : ""}
                  </li>
                ))}
              </ul>
            ) : null}
          </li>
        ))}
      </ul>
    </Panel>
  );
}
