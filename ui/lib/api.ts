// Thin client for the B5 FastAPI backend. The base URL comes from NEXT_PUBLIC_API_BASE
// (defaults to the service's port 8092). Every shape mirrors the domain dataclasses.

import type {
  AgentCard,
  CrossValidationResult,
  DocumentExtract,
  Health,
  LoanApplicationCase,
  Persona,
  ProcessRequest,
} from "./types";
import { ConfiguredEmptyError, readEnvValue } from "./env-setting.mjs";

// The API base is resolved in THREE states, not two.
//
// Reading `process.env.NEXT_PUBLIC_API_BASE?.replace(...) || "<loopback default>"`
// which hands a variable an operator DELIBERATELY EMPTIED the loopback default. That is a
// widening: the console then talks to a local API instead of the configured one, and
// `connect-src` is built from the same value, so the emptied deployment is byte-identical to one
// that never configured the variable. Next inlines NEXT_PUBLIC_* AT BUILD TIME, so the wrong
// value is frozen into the bundle and cannot be corrected at start-up.
const DEFAULT_BASE = "http://localhost:8092";
// The literal member expression is required: a bundler substitutes the public value
// only where it sees exactly this, and handing it `process.env` leaves the browser
// reading {} and silently taking the hard-coded loopback default.
const BASE_SETTING = readEnvValue(
  "NEXT_PUBLIC_API_BASE",
  process.env.NEXT_PUBLIC_API_BASE,
);
if (BASE_SETTING.isConfiguredEmpty) {
  throw new ConfiguredEmptyError(
    "NEXT_PUBLIC_API_BASE is set to an empty value. An emptied variable names nothing, " +
      "so it cannot inherit the unset default (" + DEFAULT_BASE + "), which points this " +
      "console at a loopback API and widens connect-src to match. Unset it to take that " +
      "default deliberately, or give it the API origin this deployment should call.",
  );
}
const BASE = (BASE_SETTING.hasValue ? BASE_SETTING.value : DEFAULT_BASE).replace(
  /\/+$/,
  "",
);

// Dev-only identity selection. In LOCAL mode the backend resolves identity from the
// X-Dev-Persona header; in secure profiles this header is ignored entirely (identity is
// the IAP-verified assertion). The client never asserts an `actor` in a request body.
let devPersona = "";

export function setDevPersona(id: string): void {
  devPersona = id;
}

export function getDevPersona(): string {
  return devPersona;
}

function requestHeaders(): Record<string, string> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (devPersona) headers["X-Dev-Persona"] = devPersona;
  return headers;
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: requestHeaders(),
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    throw new Error(`${path} returned ${res.status}: ${await res.text()}`);
  }
  return (await res.json()) as T;
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { headers: requestHeaders() });
  if (!res.ok) {
    throw new Error(`${path} returned ${res.status}`);
  }
  return (await res.json()) as T;
}

export const api = {
  process: (req: ProcessRequest) =>
    post<LoanApplicationCase>("/v1/process", req),

  extract: (document: { id: string; doc_type: string; uri: string }) =>
    post<DocumentExtract>("/v1/extract", { document }),

  validate: (req: {
    application_id: string;
    applicant: ProcessRequest["application"];
    extracts: DocumentExtract[];
  }) => post<CrossValidationResult>("/v1/validate", req),

  health: () => get<Health>("/healthz"),

  listPersonas: () => get<Persona[]>("/v1/personas"),

  agentCard: () => get<AgentCard>("/.well-known/agent-card.json"),
};
