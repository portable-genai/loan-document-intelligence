"use client";

import { useEffect, useState } from "react";
import { CaseView } from "../components/CaseView";
import { Panel } from "../components/ui";
import { api, setDevPersona } from "../lib/api";
import type { LoanApplicationCase, Persona, ProcessRequest } from "../lib/types";

// A clearly-fictional sample application for the demo console. No `actor`: the audit actor
// is the server-verified identity (a seeded persona in local mode, an IAP assertion in
// secure mode), never a value the client asserts.
const SAMPLE: ProcessRequest = {
  application: {
    id: "app-fictional-0001",
    name: "Jordan Tester Fictional",
    address: "123 Imaginary Road, Singapore 000000",
    declared_income: {
      source_doc_id: "declared",
      amount: 6500,
      currency: "SGD",
      period: "monthly",
      kind: "salary",
    },
  },
  documents: [
    { id: "doc-payslip-2026-04", doc_type: "payslip", uri: "gs://fictional/payslip.pdf" },
    {
      id: "doc-bank-2026-04",
      doc_type: "bank_statement",
      uri: "gs://fictional/bank.pdf",
    },
  ],
};

export default function Page() {
  const [result, setResult] = useState<LoanApplicationCase | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [personas, setPersonas] = useState<Persona[]>([]);
  const [selectedPersona, setSelectedPersona] = useState("");
  const [payslipUri, setPayslipUri] = useState(SAMPLE.documents[0].uri);
  const [bankUri, setBankUri] = useState(SAMPLE.documents[1].uri);

  // Demo identity picker: local profile only (secure profiles resolve identity from the
  // IAP assertion and return an empty persona list).
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const status = await api.health();
        if (status.profile !== "local") return;
        const list = await api.listPersonas();
        if (cancelled || list.length === 0) return;
        setPersonas(list);
        setSelectedPersona(list[0].id);
        setDevPersona(list[0].id);
      } catch {
        // The persona picker is dev-only convenience; ignore lookup failures.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  function onPersonaChange(id: string) {
    setSelectedPersona(id);
    setDevPersona(id);
  }

  async function run() {
    setLoading(true);
    setError(null);
    try {
      setResult(
        await api.process({
          ...SAMPLE,
          documents: [
            { ...SAMPLE.documents[0], uri: payslipUri },
            { ...SAMPLE.documents[1], uri: bankUri },
          ],
        }),
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="space-y-4">
      {personas.length > 0 ? (
        <Panel title="Demo identity">
          <label className="text-sm">
            <span className="text-ink-600">Persona (sent as X-Dev-Persona, local mode only)</span>
            <select
              className="mt-1 w-full rounded-lg border border-ink-200 px-2 py-1.5 text-sm sm:w-96"
              value={selectedPersona}
              onChange={(e) => onPersonaChange(e.target.value)}
            >
              {personas.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.subject} · {p.tenant}
                </option>
              ))}
            </select>
          </label>
        </Panel>
      ) : null}

      <Panel title="Process an application">
        <p className="mb-3 text-sm text-ink-600">
          Sends a synthetic, clearly-fictional application to{" "}
          <code className="font-mono text-xs">POST /v1/process</code>. The pipeline
          redacts applicant PII, extracts each document, normalises income figures, and
          runs the deterministic cross-validation before returning a cited verification.
          Local mode resolves the bundled fictional IDs. On GCP, enter the reviewed synthetic
          objects prepared for this installation.
        </p>
        <div className="mb-3 grid gap-3 sm:grid-cols-2">
          <label className="text-sm">
            <span className="text-ink-600">Payslip URI</span>
            <input
              aria-label="Payslip URI"
              className="mt-1 w-full rounded-lg border border-ink-200 px-2 py-1.5 font-mono text-xs"
              value={payslipUri}
              onChange={(event) => setPayslipUri(event.target.value)}
            />
          </label>
          <label className="text-sm">
            <span className="text-ink-600">Bank statement URI</span>
            <input
              aria-label="Bank statement URI"
              className="mt-1 w-full rounded-lg border border-ink-200 px-2 py-1.5 font-mono text-xs"
              value={bankUri}
              onChange={(event) => setBankUri(event.target.value)}
            />
          </label>
        </div>
        <button
          onClick={run}
          disabled={loading}
          className="rounded-lg bg-regblue-600 px-4 py-2 text-sm font-semibold text-white hover:bg-regblue-700 disabled:opacity-50"
        >
          {loading ? "Processing..." : "Process sample application"}
        </button>
        {error ? (
          <p className="mt-3 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-800">
            {error}
          </p>
        ) : null}
      </Panel>

      {result ? <CaseView result={result} /> : null}
    </main>
  );
}
