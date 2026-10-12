# SignalPost Crawler — Trust-First Implementation Plan

## Core Principle

**False-positive publication is worse than missing data.**

Validation and identity trust must therefore be established before expanding crawler coverage with Playwright.

## Phase 1 — Pydantic Validation + Quarantine

### Objective
Introduce structured validation without silently dropping invalid data.

### Implementation
- Define Pydantic models for `CrawlPageEvent`, `WebsiteValue`, and normalized website/page output.
- Validate parser output before profile merge/publication.
- Add quarantine/dead-letter output for rejected records.
- Add rejection reason codes such as `invalid_org_number`, `invalid_url`, `invalid_status`, `missing_required_field`, and `schema_validation_error`.
- Report accepted, rejected, and quarantined counts.

### Validation
Feed deliberately malformed fixtures and verify valid records pass, invalid records are quarantined, every rejection has a reason code, and the pipeline does not silently lose rejected data.

### Acceptance
- No silent validation drops.
- Invalid data never reaches publishable evidence.
- Rejection counts are visible in run metrics.

## Phase 2 — Identity Gate Hardening

### Objective
Make company identity verification robust before allowing website evidence into final output.

### Implementation
- Normalize organisation-number formatting.
- Require exactly 9 digits after normalization.
- Implement Norwegian organisation-number MOD-11 checksum validation.
- Do not treat an arbitrary 9-digit substring as a valid identity match.
- Normalize legal names for casing, whitespace, punctuation, diacritics, and legal suffixes such as `AS` and `ASA`.
- Check whether a page-found organisation number is associated with the target company context.
- Distinguish target company, parent, subsidiary, partner, vendor, and unrelated company.

### Decision Model

```text
ACCEPT
REVIEW
REJECT
```

Example:
- Valid exact organisation number + matching company context → ACCEPT
- Strong name/domain evidence but incomplete identity → REVIEW
- Conflicting identity signals → REJECT

Fuzzy similarity alone must never publish identity.

### Validation
Build a test matrix containing exact matches, invalid checksums, wrong numbers, similar names, parent/subsidiary cases, partner/vendor cases, and name variations.

### Acceptance
- Identity decisions are deterministic.
- Invalid organisation numbers cannot independently establish identity.
- Related-company numbers do not incorrectly establish target identity.
- False-positive identity publication is zero in the acceptance set.

## Phase 3 — RapidFuzz + Corroborating Evidence

### Objective
Add fuzzy legal-name matching without allowing fuzzy similarity alone to publish evidence.

### Implementation
- Use RapidFuzz only as a supplement to the hardened identity gate.
- Define similarity score, review threshold, and acceptance eligibility.
- Do not choose arbitrary thresholds.

### Threshold Calibration
Create representative true-match and false-match datasets, including near-name companies, parent/subsidiary pairs, unrelated similar names, and spelling/diacritic variations.

Select thresholds from observed scores and document them.

```text
score < X → REJECT / insufficient match
X–Y       → REVIEW
> Y       → eligible for corroboration
```

`X` and `Y` must come from the test data.

### Corroborating Evidence
Explicitly test combinations such as:
- fuzzy name + matching domain
- fuzzy name + matching address
- fuzzy name + organisation number
- fuzzy name + domain + address
- name only
- domain only

Do not assume address or domain alone is sufficient.

### Validation
Record expected decision, actual decision, similarity score, corroborating evidence, false positives, and false negatives.

### Acceptance
- Thresholds are evidence-based.
- Fuzzy score alone never publishes identity.
- Corroboration rules are deterministic.
- False-positive identity publication remains zero.

## Phase 4 — Controlled Playwright JS Fallback

### Objective
Increase coverage for JavaScript-rendered sites only after validation and identity safeguards are established.

### Architecture

Use `scrapy-playwright` as the preferred integration so Playwright stays inside the Scrapy architecture.

```text
Scrapy static request
        ↓
Static extraction
        ↓
JS fallback candidate?
   ┌────┴────┐
   No       Yes
   ↓          ↓
continue   Playwright render
              ↓
        existing extraction
```

### Operational Limits
Define before implementation:
- page/navigation timeout
- render timeout
- per-domain concurrency
- global browser/page concurrency
- browser/context lifecycle
- memory limits
- per-domain rate limit
- maximum rendered pages per company
- maximum render time per company
- bounded retries
- browser failure handling

### Robots / Safety
- Preserve robots.txt behavior.
- Do not render disallowed pages.
- Keep public-network restrictions.
- Respect domain-level concurrency controls.

### Failure States

```text
js_fallback_candidate
js_render_success
js_render_timeout
js_render_failed
js_render_budget_exceeded
```

Browser failures must not crash the company batch.

### Validation
Test static pages, JS pages, timeouts, browser failures, robots restrictions, concurrent companies, render limits, and regression of existing static behavior.

### Acceptance
- Playwright is fallback-only.
- Browser resources remain bounded.
- JS failures do not crash the batch.
- Rendered evidence passes through the same validation and identity controls.

## Phase 5 — End-to-End Regression + Acceptance

### Objective
Verify the complete crawler remains safe after all changes.

### Test Categories

**Identity**
- true target
- wrong company
- parent
- subsidiary
- partner/vendor
- invalid organisation number
- fuzzy-name collision

**Extraction**
- static HTML
- JSON-LD
- microdata
- OpenGraph
- JS-rendered content

**Validation**
- valid event
- malformed event
- rejected event
- quarantine output
- reason-code reporting

**Operational**
- timeout
- retry
- robots restriction
- browser failure
- Scrapy failure
- batch continuation

### Metrics

```text
identity_accept
identity_review
identity_reject
validation_accept
validation_reject
quarantined_records
js_fallback_used
js_fallback_success
js_fallback_failed
false_positive
false_negative
```

### Acceptance
- No silent validation loss.
- No known false-positive identity publication.
- Playwright fallback is bounded.
- Existing crawler behavior does not regress.
- One failed page/connector does not kill the batch.

## Phase 6 — Documentation + README Cleanup

### Tasks
- Update README to reflect the active Scrapy deep crawler.
- Document validation and quarantine behavior.
- Document organisation-number checksum validation.
- Document identity and fuzzy-matching policy.
- Document corroborating evidence rules.
- Document Playwright fallback and operational limits.
- Document failure states and publishability boundaries.
- Remove outdated claims that the deep crawler is excluded.

### Acceptance
README and implementation agree on the active crawler path, identity behavior, validation behavior, JS fallback behavior, and evidence boundaries.

## Final Implementation Order

```text
Phase 1 — Pydantic + quarantine
        ↓
Phase 2 — Identity hardening
        ↓
Phase 3 — RapidFuzz + corroboration
        ↓
Phase 4 — Playwright JS fallback
        ↓
Phase 5 — End-to-end regression
        ↓
Phase 6 — Documentation cleanup
```

## Non-Negotiable Rule

**Do not add Playwright coverage until validation and identity gates have been hardened and tested.**

The goal is not simply to crawl more pages. The goal is to **increase coverage without increasing false-positive publication**.
