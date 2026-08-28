// What a STRING can decide about the console's CSP.
//
// These tests are NOT sufficient, and the reason is the whole point of `scripts/assert-hydratable.mjs`.
// The response header is byte-identical in the working case and in the broken one: a statically
// prerendered route serves bare script tags under a header advertising a nonce, and no assertion
// about the policy string can tell the two apart. Only fetching the built server's document and
// comparing its script tags against the served nonce can. So these cover the decisions the module
// really does make on its own (which directives exist, that none is ever empty, the three-state
// framing read, the nonce shape), and the hydration check covers the rest.

import assert from "node:assert/strict";
import test from "node:test";

import {
  ConfiguredEmptyError,
  UnhydratableCspError,
  WildcardOriginError,
  assertEmbedPolicyConfigured,
  assertHydratableCsp,
  contentSecurityPolicy,
  frameAncestors,
  frameOptions,
  generateNonce,
} from "../lib/csp.mjs";

/** Parse a policy string into a directive map, the way a browser does. */
function directives(csp) {
  return new Map(
    csp
      .split(";")
      .map((piece) => piece.trim())
      .filter(Boolean)
      .map((piece) => {
        const [name, ...value] = piece.split(/\s+/);
        return [name.toLowerCase(), value.join(" ")];
      }),
  );
}

test("the policy carries every directive the fleet standard requires", () => {
  const map = directives(contentSecurityPolicy({}, "n0nce"));
  for (const name of [
    "default-src",
    "base-uri",
    "form-action",
    "object-src",
    "script-src",
    "style-src",
    "img-src",
    "font-src",
    "connect-src",
    "frame-ancestors",
  ]) {
    assert.ok(map.has(name), `the policy has no \`${name}\` directive`);
  }
  assert.equal(map.get("object-src"), "'none'");
  assert.equal(map.get("base-uri"), "'self'");
});

test("no directive is ever emitted empty, in any resolvable env state", () => {
  // An empty directive is a CSP parse error: the browser DISCARDS it, so the restriction the
  // operator asked for silently disappears. The env states that once produced one are pinned here.
  for (const env of [{}, { NEXT_PUBLIC_API_BASE: "" }, { NEXT_PUBLIC_API_BASE: "   " }]) {
    for (const [name, value] of directives(contentSecurityPolicy(env, "n0nce"))) {
      assert.notEqual(value, "", `\`${name}\` is empty for env ${JSON.stringify(env)}`);
    }
  }
});

test("script-src takes the nonce and strict-dynamic only when a nonce is passed", () => {
  assert.equal(
    directives(contentSecurityPolicy({}, "abc123")).get("script-src"),
    "'self' 'nonce-abc123' 'strict-dynamic'",
  );
  // No nonce means no Next-rendered document, so the strict form with no inline allowance.
  assert.equal(directives(contentSecurityPolicy({})).get("script-src"), "'self'");
});

test("frame-ancestors is read in three states, and the middle one refuses", () => {
  assert.equal(frameAncestors({}), "'self'");
  assert.equal(frameAncestors({ NEXT_PUBLIC_FRAME_ANCESTORS: "'none'" }), "'none'");
  assert.equal(
    frameAncestors({ NEXT_PUBLIC_FRAME_ANCESTORS: "https://portal.client.example" }),
    "https://portal.client.example",
  );
  for (const blank of ["", "   ", "\t"]) {
    assert.throws(
      () => frameAncestors({ NEXT_PUBLIC_FRAME_ANCESTORS: blank }),
      ConfiguredEmptyError,
      `an emptied allowlist (${JSON.stringify(blank)}) was read as consent to the default`,
    );
  }
});

test("X-Frame-Options accompanies only the two policies it can express", () => {
  assert.equal(frameOptions("'self'"), "SAMEORIGIN");
  assert.equal(frameOptions("'none'"), "DENY");
  // A named allowlist has no X-Frame-Options spelling; sending DENY would break the very embed
  // the operator configured, in exactly the agents that cannot read frame-ancestors.
  assert.equal(frameOptions("https://portal.client.example"), "");
});

test("connect-src widens to the API ORIGIN, never the full URL", () => {
  const map = directives(
    contentSecurityPolicy({ NEXT_PUBLIC_API_BASE: "https://api.client.example/v1/loans" }, "n"),
  );
  assert.equal(map.get("connect-src"), "'self' https://api.client.example");
});

test("a rooted relative API base stays same-origin rather than being refused", () => {
  // The documented reverse-proxy layout is NEXT_PUBLIC_API_BASE=/agent/api. Same-origin is
  // already covered by 'self', so it widens nothing; refusing it would break that deployment.
  const map = directives(contentSecurityPolicy({ NEXT_PUBLIC_API_BASE: "/agent/api" }, "n"));
  assert.equal(map.get("connect-src"), "'self'");
});

test("an API base that is neither absolute nor rooted is refused", () => {
  assert.throws(
    () => contentSecurityPolicy({ NEXT_PUBLIC_API_BASE: "api.client.example" }, "n"),
    /NEXT_PUBLIC_API_BASE/,
  );
});

test("a protocol-relative API base is refused rather than read as same-origin", () => {
  // It looks rooted and names another host. Reading it as same-origin would drop a genuinely
  // cross-origin API out of connect-src, which is the silent narrowing above in disguise.
  assert.throws(
    () => contentSecurityPolicy({ NEXT_PUBLIC_API_BASE: "//api.client.example/v1" }, "n"),
    /must name its scheme/,
  );
});

test("nonces are unique and base64", () => {
  const seen = new Set();
  for (let i = 0; i < 50; i += 1) {
    const nonce = generateNonce();
    assert.match(nonce, /^[A-Za-z0-9+/]+={0,2}$/);
    assert.ok(!seen.has(nonce), "a nonce repeated; a predictable nonce is not a nonce");
    seen.add(nonce);
  }
});

test("the build refuses a layout that cannot carry the nonce", () => {
  assert.throws(
    () => assertHydratableCsp("export const metadata = {};"),
    UnhydratableCspError,
    "a statically prerendered layout was accepted under a nonce CSP",
  );
  assert.doesNotThrow(() => assertHydratableCsp('export const dynamic = "force-dynamic";'));
});

// The FOURTH framing state: a value that names EVERYBODY. It is not the emptied state, because it
// resolves to a directive a browser will happily honour, and the header this module emits is the
// one a browser enforces for the DOCUMENT. So a console that accepted a wildcard while the API
// refused it would be the permissive half of the posture, and the permissive half is the one that
// governs what a page can actually be framed by.

test("a wildcard framing allowlist refuses, bare and partial alike", () => {
  // A partial wildcard is no safer than a bare one: `https://*.example` trusts every subdomain,
  // including one an attacker obtains by takeover and one that serves user content. Refusing any
  // asterisk turns away nothing a deployment could correctly hold, because a real origin never
  // contains the character.
  for (const value of ["*", "'*'", "*.*", "https://*.example", "*.example", "https://*"]) {
    assert.throws(
      () => frameAncestors({ NEXT_PUBLIC_FRAME_ANCESTORS: value }),
      WildcardOriginError,
      `frameAncestors accepted ${JSON.stringify(value)}`,
    );
    // A mixed list is the dangerous shape: one valid origin makes the value look configured,
    // while the entry beside it is the one that actually widens the policy.
    const mixed = `https://portal.client.example ${value}`;
    assert.throws(
      () => frameAncestors({ NEXT_PUBLIC_FRAME_ANCESTORS: mixed }),
      WildcardOriginError,
      `frameAncestors accepted ${JSON.stringify(mixed)}`,
    );
    assert.throws(
      () => contentSecurityPolicy({ NEXT_PUBLIC_FRAME_ANCESTORS: value }, "n"),
      WildcardOriginError,
      `contentSecurityPolicy emitted ${JSON.stringify(value)}`,
    );
    // The boot refusal has to catch exactly what the per-request read catches, or a deployment
    // carrying the wildcard comes up and only fails somewhere later.
    assert.throws(
      () => assertEmbedPolicyConfigured({ NEXT_PUBLIC_FRAME_ANCESTORS: value }),
      WildcardOriginError,
      `${JSON.stringify(value)} would have booted`,
    );
  }
});

test("the literal null is refused, though it carries no asterisk", () => {
  // The refusal tested `token.includes("*")`, which catches every wildcard that is SPELLED as one
  // and cannot see this one. A sandboxed iframe presents a null origin, so `frame-ancestors null`
  // admits framing from a document whose own origin the browser has already discarded, which is
  // exactly the framing the directive exists to refuse. A wildcard by behaviour, not by spelling.
  for (const value of ["null", "https://portal.client.example null", "null https://a.example"]) {
    assert.throws(
      () => frameAncestors({ NEXT_PUBLIC_FRAME_ANCESTORS: value }),
      WildcardOriginError,
      `frameAncestors accepted ${JSON.stringify(value)}`,
    );
    assert.throws(
      () => contentSecurityPolicy({ NEXT_PUBLIC_FRAME_ANCESTORS: value }, "n"),
      WildcardOriginError,
      `contentSecurityPolicy emitted ${JSON.stringify(value)}`,
    );
    assert.throws(
      () => assertEmbedPolicyConfigured({ NEXT_PUBLIC_FRAME_ANCESTORS: value }),
      WildcardOriginError,
      `${JSON.stringify(value)} would have booted`,
    );
  }
});

test("the wildcard refusal leaves every legitimate value resolving", () => {
  // A refusal that also refuses valid input is an outage rather than a control. Matching is
  // exact-token, so an origin whose hostname merely contains one of the words is untouched, and
  // the unset, emptied and named states keep the answers they already gave.
  assert.equal(frameAncestors({}), "'self'");
  assert.throws(() => frameAncestors({ NEXT_PUBLIC_FRAME_ANCESTORS: "" }), ConfiguredEmptyError);
  assert.equal(frameAncestors({ NEXT_PUBLIC_FRAME_ANCESTORS: "'none'" }), "'none'");
  assert.equal(
    frameAncestors({ NEXT_PUBLIC_FRAME_ANCESTORS: "https://nullify.example https://a.example" }),
    "https://nullify.example https://a.example",
  );
  assert.doesNotThrow(() =>
    assertEmbedPolicyConfigured({ NEXT_PUBLIC_FRAME_ANCESTORS: "https://portal.client.example" }),
  );
});
