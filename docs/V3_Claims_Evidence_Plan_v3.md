# V3 Claims + Evidence: Corrected Implementation Plan (v3)

Audience: an AI coding agent that will modify this repository. Read all of it before editing.

## 0. Goal

Add `claims[]` and `evidence[]` to every envelope in `result/envelopes.jsonl`, additively.

Before (live today):

```text
{ run_id, organisation_number, state, started_at, completed_at, modules, profile }
```

After:

```text
{ run_id, organisation_number, state, started_at, completed_at, modules, profile,
  claims[], evidence[] }
```

`profile` and `profile.evidence` stay exactly as they are. Nothing is removed.

### 0.1 Delivery rule: claims appear only on the next normal run

> **HARD RULE (user instruction): Claims and evidence are NOT to be added now, and NOT manually.**
> They must be added automatically by the pipeline when the user does the next new run
> (`python run.py --organisations ...`). The agent only changes code. The agent never produces,
> injects, or backfills claims into any existing output file. If you are tempted to "just run it once
> so claims show up", stop: that is forbidden.

This phase is code only. Claims and evidence must be produced automatically by the pipeline during the next normal run (`python run.py --organisations ...`), through the live path (`run_agent.py` stage 6, `build_output_contract.py`).

Do NOT, as part of this task:

- modify, regenerate, or backfill any existing output: `result/envelopes.jsonl`, `result/envelopes_v2.jsonl`, `result/envelopes_v3.jsonl`, or anything under `out/`,
- write a one-off script that injects claims into existing files,
- run `run.py --source-envelopes` to convert existing envelopes.

Existing output files stay untouched. The first envelopes that contain `claims[]` and `evidence[]` are the ones written by the user's next run.

Summary of the delivery rule:

| Question | Answer |
|---|---|
| When do claims appear? | Only when the user starts a new normal run. |
| Who triggers it? | The user, not the agent. |
| What does the agent do now? | Edit code only (`build_output_contract.py`, `output_contract.py`) and write the implementation record. |
| Is any existing file updated now? | No. `result/` and `out/` stay exactly as they are. |
| Is a manual or one-off injection allowed? | No. |

## 1. Key decision: reuse `to_public_envelope()`, do not write a new normalizer

`src/norway_company_agent/output_contract.py` is OFF-PATH (nothing in `run.py` calls it), but it already contains a complete claims+evidence builder. It takes the live row shape directly (`row["profile"]` if present), reads `profile.evidence` and `profile.observations`, and:

- builds `claims[]` and `evidence[]`, deduplicating evidence by key (one evidence item supports many claims),
- maps evidence status to availability (`available`, `not_available`, `blocked`, `not_applicable`, `ambiguous`, `failed`; `not_fetched` produces no claim),
- assigns numeric confidence (official data 0.99, `failed` 0.0, `ambiguous` uses the identity score),
- runs `assert_evidence_integrity()` and `assert_claim_schema_integrity()` before returning.

Writing a second normalizer would duplicate this and diverge from the 3 existing test files that cover it (`tests/test_phase1_website_contract.py`, `test_phase2_evidence_linkage.py`, `test_phase6_changes.py`).

## 2. Measured baseline (offline replay, 20 shipped rows, no network, no pipeline)

`to_public_envelope()` was executed on all 20 rows of `out/latest-run/envelopes.jsonl`.

| Check | Result |
|---|---|
| Rows that produced an envelope | 20 of 20, 0 exceptions |
| Integrity assertions | passed on all 20 (they run inside the function) |
| Returned top-level keys | `organisation_number, run, claims, evidence, changes, errors, operations` (NOT the fat shape) |
| Claims per row | about 21 distinct fields |
| Availability over all claims | available 300, not_available 113, ambiguous 1, failed 1 |
| Evidence items per row | 8 to 9 |
| Size | fat envelope avg 18,476 bytes; claims+evidence adds avg 18,855 bytes. Output roughly doubles (about 18 MB to about 37 MB for 1000 companies). |

Claim fields observed: `legal_name, legal_form, employees, industry_code, municipality, business_address, bankrupt, liquidating, latest_submitted_accounts, official_website, phones, emails, website_addresses, social_profiles, financials, financial_history, roles, group, locations, accounting_obligation, prior_year_financials`.

The two non-available website cases map correctly: one `ambiguous` (identity REVIEW, confidence 0.6) and one `failed` (confidence 0.0).

## 3. Two real bugs found by that replay (must fix)

### Bug A: workforce and site-activity observations are silently dropped

`OBSERVATION_CLAIM_FIELDS` in `output_contract.py` is keyed by signal type:

```text
careers_page_found, jobs, news, activity, workforce, prior_year_financials
```

The live pipeline emits these signal types (from the archive stages):

```text
workforce_snapshot   (15 in the sample)
prior_year_financials (15)   -> mapped, works
profile_metrics      (1)     -> NOT mapped
```

The loop does `OBSERVATION_CLAIM_FIELDS.get(signal)` and `continue`s on a miss. Result: all 15 `workforce_snapshot` observations and the `profile_metrics` observation produce no claim and no error. Workforce is the entire output of the `--include-workforce-ocr` flag, so this is a visible gap.

Fix: add exactly these entries to `OBSERVATION_CLAIM_FIELDS`:

```text
"workforce_snapshot": "workforce"
"profile_metrics":    "activity"
```

`_observation_availability()` returns `available` by default for these fields, which is correct. Do NOT add a `public_post` (news) entry: Jobs and News are frozen in this phase.

### Bug B: observation provenance is mislabeled

In the observation loop, the evidence record is built with `"source_class": "company_owned"` hardcoded (`output_contract.py` line ~279). Workforce and prior-year observations come from official BRREG annual-report copies (their own `source_class` is `official_annual_account_copy`, `platform` is `brreg`). They would be labeled `company_owned`, which is wrong provenance.

Fix: use the observation's own value:

```text
"source_class": observation.get("source_class") or "unknown",
```

`add_evidence()` already translates `registry_linked_company_website` and `company_site` to `company_owned`, so company-site observations keep the right label without the hardcode.

## 4. Work items

### W1. Wire into the LIVE path (the only integration point)

File: `scripts/build_output_contract.py`, function `build_envelope()`.

Do NOT hook into `batch.terminal_envelope()`. It is also called in stage 1 (`run_competition_batch.py`) for `registry-envelopes.jsonl` on incomplete profiles, which would produce wasteful, stale claims.

`build_envelope()` has two branches. Both must produce claims:

1. Profile row branch (normal run): after `terminal_envelope(...)` returns the envelope, which already has observations attached to `profile`, compute claims.
2. Already-an-envelope branch (`--source-envelopes`, deep copy): after observations are applied to `profile`, recompute claims, overwriting any `claims`/`evidence` already present so they cannot go stale.

Order matters: observations must be attached to `profile` BEFORE claims are computed.

Shared helper (put it in `build_output_contract.py`, small):

```text
def attach_claims(envelope) -> envelope:
    public = to_public_envelope(envelope)      # envelope has profile + run_id etc.
    envelope["claims"] = public["claims"]
    envelope["evidence"] = public["evidence"]
    return envelope
```

Merge ONLY `claims` and `evidence` in this phase. Do not merge `run`, `changes`, `errors`, `operations`. Do not rename or remove any existing key.

Failure handling: stage 6 is required (failure means no output file for the whole batch). So do not let one company abort it:

- wrap the call per row in `try/except Exception`,
- on failure set `envelope["claims"] = []`, `envelope["evidence"] = []`, `envelope["claims_error"] = "<ExceptionName>: <short message>"`,
- count failures and write `claims_failed` (count) and `claims_failed_orgs` (list) into `contract-report.json`.

Import: `from norway_company_agent.output_contract import to_public_envelope` (the script already adds `src` to `sys.path`). The import was verified to work offline in a clean environment.

### W2. Apply Bug A and Bug B fixes in `output_contract.py` (section 3).

### W3. Keep existing availability and confidence semantics. Do not set confidence to null.

Reference mapping already implemented (`_availability`, `_confidence`):

| Evidence status | Claim availability | Value | Confidence |
|---|---|---|---|
| available (non-empty) | available | real value | 0.99 official, identity score or 0.9 otherwise |
| available but empty for a checked field | not_available | null | 0.8 |
| not_found | not_available | null | 0.8 |
| not_applicable | not_applicable | null | 1.0 |
| blocked | blocked | null | 0.5 |
| source_error / unknown | failed | null | 0.0 |
| identity REVIEW (website) | ambiguous | null | identity score |
| not_fetched | no claim emitted | none | none |

This also settles the old plan's contradiction: a missing source DOES produce a claim (`not_available`, null), so consumers can tell "checked, nothing" from "never checked" (no claim).

### W4. Accepted behaviors (do not "improve" these in this phase)

- Evidence ids are `ev-1, ev-2, ...` per envelope, deterministic for the same input. They are only referenced inside one envelope, and change detection compares claims, not evidence ids. Keep this scheme. Do not invent semantic ids.
- Financials: one `financials` claim whose value holds the records list (up to 3 periods, each with its own `period`). No per-period claim fields.
- Roles and locations: one claim each, whose value is the full list.
- Output size doubles. Accepted for this phase. Reducing it (dropping `evidence[].value` where the claim already holds the value) is a separate decision.

### W5. Out of scope (do not touch)

Jobs and News (including website jobs/news extraction and their claim mappings), `changes[]`, `errors[]`, `operations`, the non-uniform `modules` keys, entity `state` semantics, `batch.terminal_envelope()`, `run.py`, `run_agent.py`, `run_competition_batch.py`, anything under `archive/`.

## 5. Verification rules (replaces the old blanket no-testing rule)

Forbidden: `run.py` collection runs, `run_agent.py`, any network or API or crawler call, creating new test files.

Allowed, because they are offline and deterministic:

- `python -m py_compile` on every edited file.
- An inline offline replay (a `python -c` or throwaway script that is not committed) that loads `out/latest-run/envelopes.jsonl`, runs `build_envelope`/`attach_claims` on each row, and checks the criteria in section 6.
- `pytest tests` for the EXISTING tests only (they are offline and cover `output_contract`).
- Do NOT run `python run.py --source-envelopes ...` to "add claims" to existing output. That is the manual backfill path and it overwrites `result/envelopes.jsonl`. See section 0.1.
- The offline replay must be read-only on disk: it may read `out/latest-run/` but must not write anything into `result/` or `out/`. Print results to the console only.

Reason for the change: the offline replay takes seconds on 20 existing rows; the alternative is an 11 minute full run with live API cost just to discover an import or schema error.

## 6. Acceptance criteria (each must be checkable)

1. All 20 sample rows produce `claims` and `evidence`; `claims_failed` is 0.
2. Every `claim.evidence_ids` entry resolves to an `evidence[].id` in the same envelope.
3. `profile`, `profile.evidence`, `modules`, `state`, `run_id` are byte-identical in meaning to the input (only `claims`, `evidence` added, plus `claims_error` on failure).
4. Rows with a `workforce_snapshot` observation (15 in the sample) now contain a `workforce` claim; the row with `profile_metrics` contains an `activity` claim.
5. Those workforce and prior-year evidence items carry `source_class` `official_annual_account_copy`, not `company_owned`.
6. `claims[*].confidence` is numeric except where the schema allows None; none is set to null by the new code.
7. The already-an-envelope branch of `build_envelope()` also calls `attach_claims` (verified by code reading and the in-memory replay only; the agent does not execute `--source-envelopes`).
7b. No file under `result/` or `out/` was created, modified, or deleted by the agent (check with `git status` or file timestamps).
7c. Claims were not added manually or now: no script, command, or run produced `claims[]`/`evidence[]` in any file on disk. They will first appear in the output of the user's next normal run.
8. Existing tests still pass (`pytest tests`, if run).
9. `git diff` touches only: `scripts/build_output_contract.py`, `src/norway_company_agent/output_contract.py`, and the implementation record file.
10. No change to Jobs/News logic or mappings.

## 7. Implementation record (create `V3_CLAIMS_EVIDENCE_IMPLEMENTATION.md`)

Keep it short and concrete. Sections: What changed (file, function, line), the two bug fixes with before/after lines, the exact replay command used and its numeric results, files modified, files not modified, and Remaining gaps. Final line must state: "Verified by offline replay on the 20 shipped rows. No collection run, no network calls."

The record must also include this line: "Claims were not added now or manually. They will appear automatically on the user's next normal run."

## 8. Known unknowns (state them, do not hide them)

- The replay covered 20 companies, 3 of which had a website. Paths for websites with populated jobs, news, or many pages are barely exercised. Expect to find edge cases on the 1000 company run.
- `to_public_envelope()` was read in part (constants, `_availability`, `_confidence`, the envelope assembly, the observation loop). Helpers `_website_claim`, `_website_contact_claims`, `_claim_span`, `_operations` were not read in full.
- Output size doubling may matter for downstream consumers of `result/envelopes.jsonl`.
