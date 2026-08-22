// Composes a full LoanApplicationCase: review banner + income + checks + extracts.

import type { LoanApplicationCase } from "../lib/types";
import { CrossValidationView } from "./CrossValidationView";
import { ExtractView } from "./ExtractView";
import { IncomeSummaryView } from "./IncomeSummaryView";
import { ReviewBanner } from "./ui";

export function CaseView({ result }: { result: LoanApplicationCase }) {
  return (
    <div className="space-y-4">
      {result.requires_human_review ? <ReviewBanner /> : null}
      <div className="flex items-baseline justify-between">
        <h1 className="text-lg font-semibold text-ink-900">
          Application {result.id}
        </h1>
        <span className="text-xs text-ink-400">{result.applicant.name}</span>
      </div>
      {result.income ? <IncomeSummaryView income={result.income} /> : null}
      {result.validation ? <CrossValidationView result={result.validation} /> : null}
      <ExtractView extracts={result.extracts} />
    </div>
  );
}
