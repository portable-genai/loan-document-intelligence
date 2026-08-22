import type { Metadata } from "next";
import type { ReactNode } from "react";
import "./globals.css";

// Required by the nonce CSP, not a performance preference. `proxy.ts` mints a per-request
// script nonce, and Next can only stamp it onto the script tags of a DYNAMICALLY rendered
// route. Statically prerendered HTML was built before the nonce existed, so every script tag
// would ship bare while the header advertises a nonce, and `'strict-dynamic'` switches off the
// `'self'` fallback that was at least loading the chunks: the page would hydrate LESS than it
// did before the CSP was hardened. `next.config.mjs` refuses to build without this line.
export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "B5 Loan Document Intelligence",
  description:
    "Demo console for the B5 Loan / Mortgage Document Intelligence service: Document AI extraction + deterministic cross-validation of applicant income.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  // EMBED mode: the host page owns the chrome, so drop our header/branding and the outer
  // max-width wrapper when NEXT_PUBLIC_EMBED === "1".
  const embed = process.env.NEXT_PUBLIC_EMBED === "1";
  return (
    <html lang="en">
      <body>
        {embed ? (
          <main className="p-4">{children}</main>
        ) : (
          <div className="mx-auto max-w-5xl px-4 py-6">
            <header className="mb-6 flex items-center justify-between">
              <div>
                <h1 className="text-xl font-bold text-ink-900">
                  B5 · Loan / Mortgage Document Intelligence
                </h1>
                <p className="text-sm text-ink-500">
                  Document AI extraction + deterministic cross-validation. Decision-support
                  for underwriting, not a lending decision.
                </p>
              </div>
              <span className="rounded-full bg-regblue-100 px-3 py-1 text-xs font-semibold text-regblue-800">
                asia-southeast1
              </span>
            </header>
            {children}
          </div>
        )}
      </body>
    </html>
  );
}
