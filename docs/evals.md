# How the loan document intelligence service is evaluated

Read this page if you decide what this service is allowed to verify without a human. The metrics,
the bars and the corpus below are generated from the artifacts that actually gate the build, so
they cannot drift from what runs: `make evals-doc-check` fails the build when this page and those
artifacts disagree.

## How to run it

```sh
make eval              # offline, no credentials
make evals-doc-check   # this page is still true
```

`make check` runs both on every change.

## Extraction is now measured per FIELD, not per document

`extraction_accuracy` is the fraction of expected documents that produced a non-empty extract.
Two documents, one of which came back with a single field out of four, score a perfect 1.000. It
is the number the straight-through-processing claim was resting on, and it cannot see the case
that actually sends a file to a human.

`field_extraction_f1` scores the FIELDS a reviewer says each document type must yield: exactly
what the cross-validator consumes, which is `name` and `address` on every document and `net_pay`
on a payslip. The oracle is written from the document TYPE rather than from the fixture, because
an oracle taken from the fixture agrees with it by construction. F1 rather than recall, because a
parser that emitted every field name it could think of would score perfect recall.

**What it does not measure**, because the metric name invites the other reading: the offline
profile has no OCR, so this is not reading accuracy. It measures whether every field the decision
needs survives the pipeline to the point the decision is made. A field dropped or renamed on the
way through is a file that goes to a human, which is what the claim is about.

## A check with nothing to check, found by writing the oracle down

**Fixed.** `CrossValidator._field_match` built its observed set from whatever the documents
yielded and then added the applicant's own value. The four `pii_in_inputs` cases carried no `name`
or `address` on their extracts, so on those cases NAME_MATCH and ADDRESS_MATCH compared the
applicant with itself and passed on a single value: four of the ten cases passed two checks that
had nothing to check.

It was not a fixture edit away from fixed. The pipeline masks every document extract before
validation and held the applicant record raw, so restoring the fields made both checks compare a
masked document value against an applicant value still carrying the planted identifier, and FAIL
on cases a reviewer marked consistent. The fix was in the validator, which now compares like with
like: `LoanDocService` binds the redactor it masks extracts with onto the validator, and both sides
of each comparison go through it. With that in place the four cases carry `name` and `address` on
every extract, the runner plants each identifier on both sides, and the fields are in their
`expected_fields`, so `field_extraction_f1` scores those cases too.

Two things keep it fixed rather than merely unexercised:

- **A field no document carries is a `WARN`, not a `PASS` on the applicant alone.** It is LOW
  severity, so it neither fails the application nor moves the verdict, but it no longer reports a
  comparison that never happened.
- **The four cases are the regression test for the comparison.** Switch like-with-like off and
  both checks FAIL on all four consistent cases, which takes `validation_precision` below its bar
  and the gate red rather than passing quietly.

What masked comparison gives up: two different identifiers mask to the same token, so NAME_MATCH
cannot tell a name carrying one NRIC from the same name carrying another. No check could before
either, because the documents' copies were already masked before validation ran.

## What is measured, and against what bar

Every bar below lives in `eval/rubrics/*.yaml` next to the argument for it, and the
runner reads it from there. There is no dict of thresholds in the runner any more: a
metric scored with no reviewed bar fails the build, and so does a bar that names no
metric, which is the direction that rots quietly because it rots toward looking well
governed.

The third column is the denominator rule, and it applies only where a score is a
FRACTION over scored positives: such a threshold `t` tolerates a single miss only over
at least `1/(1-t)` of them. `all or nothing` marks a bar that already asks for no
headroom, so a bigger corpus would not change what it means. Each rubric declares which
it is rather than the rule being guessed from the number.

| Metric | Bar | Denominator | What it measures |
|---|---|---|---|
| `extraction_accuracy` | 0.8 | a rate; needs 5 positives | Fraction of an application's documents that produced a non-empty structured extract. Averaged over the dataset. |
| `field_extraction_f1` | 0.85 | a rate; needs 7 positives | Per-field F1 across the fields a reviewer says each document type must yield, averaged per document and then per case. |
| `pii_safety` | 1 | a rate; needs 0 positives | No unredacted applicant PII (NRIC, email, bank account number) survives into any audit record or output. A single leak drops the whole metric below 0.99. |
| `validation_precision` | 0.9 | a rate; needs 10 positives | On a consistent case (no planted inconsistency), no check falsely FAILs. A single false flag on a clean case drops this metric. |
| `validation_recall` | 0.9 | a rate; needs 10 positives | Of the inconsistencies planted in a case (salary-credit mismatch, name mismatch, balance decline, income inconsistency), the fraction the deterministic validator flagged as FAIL. Averaged over the inconsistent cases. |

Scored over 10 golden applications.

## What is exercised

- **10 golden applications** in `eval/datasets/golden_cases.jsonl`, of which
  **4 carry planted inconsistencies** for the validator to catch and the
  rest are consistent, which is what a false positive can fire on.
- **50 reviewer-named fields across 10 cases**, which is what
  `field_extraction_f1` is measured over. That is the denominator, not the case count,
  and the difference is what makes it a different measurement from the per-document
  metric beside it.
- **4 cases plant a raw identifier** on BOTH sides of NAME_MATCH and
  ADDRESS_MATCH, the applicant and the documents' `name` and `address`, and on the bank
  statement's `account_holder`, which no check reads. The cases stay consistent only
  while the validator compares like with like, masked against masked. Compare a masked
  document value against the raw applicant value and both checks FAIL, so a regression
  shows up as `validation_precision`, not as a quiet pass.

## How a metric is prevented from being decoration

1. **The bars are read from the rubrics, in both directions.** There is no `THRESHOLDS` dict any
   more, and `assert_covers` fails the build when a metric has no reviewed bar AND when a bar
   names no metric.
2. **The denominator rule is asserted against what actually divides each rate.**
   `field_extraction_f1` over the reviewer-named fields, not the case count.
3. **Recall and precision are measured on different case shapes by design**, and each returns
   `None` on the shape it does not apply to rather than a vacuous 1.0: recall needs planted
   inconsistencies, precision needs a consistent case.

## What is NOT measured here

- **Reading accuracy.** There is no OCR in the offline profile, as above.
- **A real model's words.** Every metric scores a deterministic core against a deterministic fake
  LLM adapter.
- **Production traffic.** Everything here is a golden set. Nothing samples live requests.
