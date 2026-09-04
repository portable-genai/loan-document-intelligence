"use client";

import { useEffect, useState } from "react";

import { API_BASE } from "../lib/api";

/**
 * The provenance every served UI states at the top of every page: WHERE the runtime sits
 * and WHICH model answers (org decision, 2026-08-30).
 *
 * The decision is not cosmetic. These systems are demonstrated on a laptop and on a
 * deployment, sometimes in the same hour, and a screenshot of one is indistinguishable from
 * the other. A viewer who cannot tell which they are looking at cannot tell whether a figure
 * came from a real managed service or a deterministic offline stub, which is precisely the
 * confusion an audit-first pitch cannot afford. So the page states it, always, rather than
 * the presenter stating it sometimes.
 *
 * **Both values come from the service**, on `/healthz`, and nothing here infers them. A UI
 * that read its own runtime from `window.location` would be right until the day the
 * deployment served through a proxy, and wrong silently after that.
 *
 * The service side is the other half of the contract and it lives in `config.py` and
 * `api/schemas.py`: `runtime` (`"gcp"` or `"local"`, derived from the profile) and
 * `generator_model`. `generator_model` is a free string rather than an enum on purpose --
 * the honest answer for a service with no LLM port is that the engine is deterministic, and
 * naming the stub renders truer than a blank field or an invented model id.
 */

/** The wording, spelled once so no two consoles phrase the same fact differently. */
export function provenance(runtime: string, model: string): string {
  const where = runtime === "gcp" ? "running on GCP" : "running locally";
  return `${where} · model ${model}`;
}

// Same base as every other call this console makes, resolved once in `lib/api` from
// NEXT_PUBLIC_API_BASE: `<mount>/api` under the portal, the service's own origin standalone.
//
// Sharing the base is what makes health REACHABLE, not merely tidy. The `connect-src` this
// console ships is built from that same value, and a cross-origin standalone run is on the
// service's CORS allowlist because every other call already needs to be. A health check on a
// base of its own would have to earn both of those separately, and would be silently refused
// until it did.
//
// It used to read `/api/agent`, the same-origin route handler the service template ships. This
// console has no such handler -- it talks to its backend directly -- so the health call fetched
// a path nothing serves, took the failure branch, and rendered NOTHING. Silently, by the design
// two paragraphs up, on every page load since the banner landed. The lesson is in the coupling:
// a banner that names a transport its own console does not use cannot fail loudly, because the
// only thing it is allowed to do on failure is disappear.

/**
 * Renders once the service has answered, and nothing before that.
 *
 * The null-until-known state is deliberate. A banner defaulting to "running locally" while
 * the fetch is in flight would state a falsehood on every deployment page load, briefly,
 * which is worse than an empty strip. A failed health call renders nothing for the same
 * reason: the page's own error surface owns the failure, and a chrome that guessed would be
 * asserting provenance it does not have.
 */
export function ProvenanceBanner() {
  const [origin, setOrigin] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    fetch(`${API_BASE}/healthz`, { cache: "no-store" })
      .then((response) => (response.ok ? response.json() : null))
      .then((body) => {
        if (!live || !body) return;
        setOrigin(provenance(String(body.runtime ?? ""), String(body.generator_model ?? "")));
      })
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, []);

  if (!origin) return null;
  return <p className="provenance-banner">{origin}</p>;
}
