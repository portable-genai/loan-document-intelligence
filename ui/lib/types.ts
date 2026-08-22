// Types mirroring the B5 FastAPI response schemas (src/loan_doc_intel/api/schemas.py).
// Field names and enum strings match the domain dataclasses exactly.

export interface Citation {
  source_id: string;
  source_type: string;
  title: string;
  page: number | null;
  field: string;
  snippet: string;
}

export interface IncomeFigure {
  source_doc_id: string;
  amount: number;
  currency: string;
  period: "monthly" | "annual";
  kind: "salary" | "bonus" | "rental" | "other";
}

export interface LineItem {
  label: string;
  amount: number;
  currency: string;
  date: string | null;
}

export interface DocumentExtract {
  document_id: string;
  doc_type: string;
  fields: Record<string, string>;
  line_items: LineItem[];
  period: string;
  confidence: number;
  citations: Citation[];
}

export type CheckStatus = "pass" | "warn" | "fail";

export interface CrossValidationCheck {
  kind: string;
  status: CheckStatus;
  expected: string;
  observed: string;
  detail: string;
  severity: string;
  citations: Citation[];
}

export interface CrossValidationResult {
  application_id: string;
  checks: CrossValidationCheck[];
  passed: boolean;
  requires_human_review: boolean;
}

export type Verdict = "verified" | "needs_review" | "inconsistent";

export interface IncomeSummary {
  application_id: string;
  verified_income: IncomeFigure;
  income_figures: IncomeFigure[];
  stability: string;
  red_flags: string[];
  verdict: Verdict;
  citations: Citation[];
  requires_human_review: boolean;
}

export interface Applicant {
  id: string;
  name: string;
  address: string;
  declared_income: IncomeFigure | null;
}

export interface LoanApplicationCase {
  id: string;
  applicant: Applicant;
  extracts: DocumentExtract[];
  validation: CrossValidationResult | null;
  income: IncomeSummary | null;
  requires_human_review: boolean;
  generated_at: string;
}

export interface ApplicantDocumentInput {
  id: string;
  doc_type: string;
  uri: string;
}

export interface ProcessRequest {
  application: {
    id: string;
    name: string;
    address?: string;
    declared_income?: IncomeFigure | null;
  };
  documents: ApplicantDocumentInput[];
}

// A seeded local dev persona (GET /v1/personas), used by the demo persona picker in
// local mode only. Secure profiles return an empty list.
export interface Persona {
  id: string;
  subject: string;
  tenant: string;
  principals: string;
}

export interface AgentSkill {
  id: string;
  name: string;
  description: string;
}

export interface AgentCard {
  name: string;
  description: string;
  url: string;
  version: string;
  provider: string;
  skills: AgentSkill[];
}

export interface Health {
  status: string;
  profile: string;
  region: string;
}
