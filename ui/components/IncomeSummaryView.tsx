// Renders an IncomeVerificationSummary: verdict, verified income, red flags, citations.

import type { IncomeSummary } from "../lib/types";
import { CitationList, Panel, StatusBadge } from "./ui";

export function IncomeSummaryView({ income }: { income: IncomeSummary }) {
  const vi = income.verified_income;
  return (
    <Panel title="Income verification" right={<StatusBadge status={income.verdict} />}>
      <p className="text-2xl font-semibold text-ink-900">
        {vi.amount.toLocaleString()} {vi.currency}
        <span className="text-sm font-normal text-ink-400"> / {vi.period}</span>
      </p>
      {income.stability ? (
        <p className="mt-1 text-sm text-ink-600">{income.stability}</p>
      ) : null}

      {income.red_flags.length > 0 ? (
        <div className="mt-3">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-rose-700">
            Red flags
          </h3>
          <ul className="mt-1 list-disc space-y-0.5 pl-5 text-xs text-rose-800">
            {income.red_flags.map((flag, i) => (
              <li key={i}>{flag}</li>
            ))}
          </ul>
        </div>
      ) : null}

      <div className="mt-3">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-ink-500">
          Evidence
        </h3>
        <div className="mt-1">
          <CitationList citations={income.citations} />
        </div>
      </div>
    </Panel>
  );
}
