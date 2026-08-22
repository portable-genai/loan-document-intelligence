// Renders a CrossValidationResult: the deterministic checks, the heart of B5.

import type { CrossValidationResult } from "../lib/types";
import { CitationList, Panel, StatusBadge } from "./ui";

export function CrossValidationView({ result }: { result: CrossValidationResult }) {
  return (
    <Panel
      title="Cross-validation checks"
      right={<StatusBadge status={result.passed ? "pass" : "fail"} />}
    >
      <ul className="space-y-3">
        {result.checks.map((check, i) => (
          <li key={i} className="rounded-lg border border-ink-100 p-3">
            <div className="flex items-center justify-between">
              <span className="text-sm font-semibold text-ink-800">
                {check.kind.replace(/_/g, " ")}
              </span>
              <div className="flex items-center gap-2">
                <span className="text-xs text-ink-400">{check.severity}</span>
                <StatusBadge status={check.status} />
              </div>
            </div>
            <dl className="mt-2 grid grid-cols-[5rem_1fr] gap-x-3 gap-y-1 text-xs text-ink-600">
              <dt className="text-ink-400">expected</dt>
              <dd>{check.expected}</dd>
              <dt className="text-ink-400">observed</dt>
              <dd>{check.observed}</dd>
              {check.detail ? (
                <>
                  <dt className="text-ink-400">detail</dt>
                  <dd>{check.detail}</dd>
                </>
              ) : null}
            </dl>
            <div className="mt-2">
              <CitationList citations={check.citations} />
            </div>
          </li>
        ))}
      </ul>
    </Panel>
  );
}
