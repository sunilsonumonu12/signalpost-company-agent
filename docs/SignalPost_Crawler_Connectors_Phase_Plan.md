# SignalPost — Website Crawler + Connectors Phase Plan

**Goal:** Website crawler (Method 1 + Method 2 with Exa/Tavily/Brave) + safe external connectors, phase-wise.  
**Cost target:** ≤ $0.01 per company for website discovery path.  
**Rule:** Missing fields = `"not_available"`. No invented data. No social-link fields in website evidence.  
**LinkedIn:** experimental only, never publish without rights approval.

---

## Phase Overview

| Phase | Scope | Verify before next |
|-------|--------|-------------------|
| **Phase 1** | Website crawler core (Method 1) + observability + `not_available` | 1 company end-to-end |
| **Phase 2** | Jobs + news + contact/locations from company site | 5–10 companies |
| **Phase 3** | Method 2 discovery: Exa → Tavily → Brave (cost-capped) | 10 companies, cost ≤ $0.01 |
| **Phase 4** | Annual-report workforce connector (official PDF) | Eligible subset, status report |
| **Phase 5** | Google News RSS connector (experimental) | Mentions + exact-title gate |
| **Phase 6** | LinkedIn guest connectors (experimental, non-publishable) | Optional; rights flag only |
| **Phase 7** | Batch integration + 100-company dry run | Metrics + no crash |

---

# PHASE 1 — Website Crawler Core (Method 1)

## What to build

1. Extend existing `website.py` / crawl path (do **not** replace framework).
2. Trace writer (`crawl_events.py`) → `out/latest-run/crawl-trace.jsonl`
3. Terminal logs + 30s heartbeat
4. Homepage + priority pages crawl
5. Page records under `profile["evidence"]["website"]["pages"]`
6. Identity gate unchanged (`apply_website_identity_gate`)
7. Every missing field → `"not_available"`
8. **No social links** written into evidence

## Priority pages

```text
/about, /om-oss
/contact, /kontakt
/management, /ledelse, /team, /people
/locations, /lokasjoner, /kontorer
/news, /press, /aktuelt, /nyheter
/careers, /career, /jobs, /karriere, /ledige-stillinger, /stillinger
```

## Expected output (verify this)

### Company evidence (excerpt)

```json
{
  "evidence": {
    "website": {
      "requested_url": "https://example.no",
      "final_url": "https://www.example.no/",
      "registered_domain": "example.no",
      "title": "...",
      "description": "...",
      "main_text_excerpt": "...",
      "structured_organisations": [],
      "pages": [
        {
          "url": "https://www.example.no/om-oss",
          "final_url": "https://www.example.no/om-oss",
          "title": "...",
          "status": 200,
          "duration_seconds": 1.1,
          "page_kind": "about",
          "extraction_state": "success",
          "text_excerpt": "...",
          "errors": []
        }
      ],
      "jobs": "not_available",
      "news": "not_available",
      "phones": "not_available",
      "emails": "not_available",
      "addresses": "not_available",
      "locations": "not_available",
      "identity_assessment": { "...": "existing gate shape" },
      "org_number_found": false,
      "legal_name_match": false,
      "address_match": false,
      "extraction_state": "partial",
      "content_sha256": "...",
      "crawl_errors": [],
      "discovery": {
        "method": "direct",
        "providers_used": [],
        "candidates": [],
        "cost_usd": 0.0
      },
      "cost_usd": 0.0
    }
  }
}
```

### Trace (at least these events)

```text
crawler_started
homepage_started
homepage_completed
links_discovered
page_started
page_completed
identity_check_started
identity_check_completed
crawler_completed
```

### Acceptance checklist (Phase 1)

- [ ] 1 company runs without crash
- [ ] `evidence.website` present
- [ ] Missing jobs/news/contact fields are `"not_available"` (not null/omitted)
- [ ] No `social_links` fields
- [ ] `crawl-trace.jsonl` has lifecycle events
- [ ] Terminal shows start → homepage → pages → identity → completed
- [ ] Identity gate still decides publishable vs not

**STOP. Verify Phase 1 before Phase 2.**

---

# PHASE 2 — Jobs, News, Contact, Locations from Company Site

## What to build

1. Extract **all** job postings found on careers/jobs pages (title, url, location, description only if present).
2. Extract news/press items when present.
3. Extract phones, emails, addresses, locations from contact/about pages + structured data.
4. Reuse `page_signals.py` where possible.
5. Still never invent fields.

## Expected output (verify this)

```json
{
  "jobs": [
    {
      "title": "Software Engineer",
      "url": "https://example.no/jobs/123",
      "location": "Oslo",
      "description": "not_available",
      "source_url": "https://example.no/karriere"
    }
  ],
  "news": [
    {
      "title": "Company opens new office",
      "url": "https://example.no/nyheter/...",
      "date": "2025-03-01",
      "source_url": "https://example.no/nyheter"
    }
  ],
  "phones": ["+47 ..."],
  "emails": ["post@example.no"],
  "addresses": ["Gate 1, 0123 Oslo"],
  "locations": [{ "label": "Oslo", "address": "..." }]
}
```

If none found for a category → that field is `"not_available"` (or `[]` only if you document list-empty convention consistently).

## Acceptance checklist (Phase 2)

- [ ] 5–10 companies run
- [ ] At least one company with jobs shows full job objects (not just careers URL)
- [ ] Company with no jobs has `"jobs": "not_available"`
- [ ] News/contact filled when present, else `not_available`
- [ ] Trace includes `jobs_extracted` / `news_extracted` when applicable
- [ ] Still no social links
- [ ] No invented salary/dates/requirements

**STOP. Verify Phase 2 before Phase 3.**

---

# PHASE 3 — Method 2 Discovery (Exa → Tavily → Brave)

## What to build

Trigger Method 2 only when Method 1 is weak/failed:

- no usable website
- HTTP failure / empty extraction
- identity weak/fail
- no useful pages

**Order:**

```text
Exa (max 1 call, ≤ $0.005)
  → candidate crawl + identity gate
Tavily (max 1 call, ≤ $0.005) if still weak
  → candidate crawl + identity gate
Brave Search (max 1 call, cheap) if still weak and budget remains
  → candidate crawl + identity gate
```

Reuse patterns from existing `run_brave_discovery.py`:

- Search is **transient** (do not persist titles/snippets/raw search as evidence)
- Only independently crawled + identity-gated page becomes website evidence
- Budget hard stop at **$0.01** total discovery cost per company

## Expected output (verify this)

### When Method 1 succeeds

```json
"discovery": {
  "method": "direct",
  "providers_used": [],
  "candidates": [],
  "cost_usd": 0.0
}
```

### When Method 2 used

```json
"discovery": {
  "method": "exa",
  "providers_used": ["exa"],
  "candidates": [
    { "url": "https://...", "provider": "exa", "rank": 1 }
  ],
  "cost_usd": 0.004
}
```

### Trace events

```text
method1_completed
method2_triggered
exa_search_started / exa_search_completed
tavily_search_started / tavily_search_completed
brave_search_started / brave_search_completed
candidate_selected
budget_check
cost_recorded
```

## Acceptance checklist (Phase 3)

- [ ] Strong Method 1 result → **zero** paid search calls
- [ ] Weak Method 1 → Exa tried first
- [ ] Exa fail/empty → Tavily
- [ ] Still weak + budget left → Brave
- [ ] `cost_usd ≤ 0.01` for every company in test set
- [ ] Search snippets/titles **not** stored as claim evidence
- [ ] Candidate must pass identity gate before promotion
- [ ] Provider failure does not crash the company

**STOP. Verify Phase 3 before Phase 4.**

---

# PHASE 4 — Annual Report Workforce Connector (Official)

## Source

Existing: `run_annual_report_workforce_connector.py`

## What to integrate

- Run only when registry `antallAnsatte` is **not** already available
- Latest official annual-report PDF from `financial_history`
- Extract company-scope employees / årsverk (not group)
- OCR fallback when text layer weak
- Output observation + status report (existing shape)

## Expected output (verify this)

### Observation (when accepted)

```json
{
  "signal_type": "workforce_snapshot",
  "platform": "brreg",
  "source_class": "official_annual_account_copy",
  "exact_entity": true,
  "rights_status": "approved",
  "metrics": {
    "workforce_value": 12,
    "measure": "full_time_equivalents",
    "year": "2024",
    "scope": "company_phrase"
  },
  "evidence_span": "..."
}
```

### Report

```json
{
  "connector": "official_annual_report_workforce_v1",
  "eligible": N,
  "accepted": M,
  "status_counts": {
    "accepted": ...,
    "registry_count_already_available": ...,
    "no_annual_report": ...,
    "no_employee_phrase": ...,
    "conflicting_employee_counts": ...,
    "organisation_number_not_in_pdf": ...,
    "error": ...
  }
}
```

## Acceptance checklist (Phase 4)

- [ ] Companies with registry employee count are skipped
- [ ] Companies without PDF → `no_annual_report`
- [ ] Accepted rows have org number in PDF + company-scope phrase
- [ ] Group/konsern phrases abstain
- [ ] Conflicting counts abstain
- [ ] Report status_counts match company_results
- [ ] Does not overwrite website crawler evidence

**STOP. Verify Phase 4 before Phase 5.**

---

# PHASE 5 — Google News RSS Connector (Experimental)

## Source

Existing: `run_google_news_rss_connector.py`

## What to integrate

- Exact legal-name match in title only
- Bounded per company / years
- `rights_status`: `review_required`
- **Not** used for strict publishable points until rights reviewed

## Expected output (verify this)

```json
{
  "signal_type": "public_mention",
  "platform": "news",
  "source_class": "public_news",
  "exact_entity": true,
  "rights_status": "review_required",
  "acquisition_mode": "rights_review_experiment",
  "evidence_span": "<exact title>",
  "publisher": "...",
  "published_at": "..."
}
```

### Report

```json
{
  "connector": "google_news_rss_exact_title_experiment_v1",
  "companies": N,
  "companies_with_mentions": M,
  "observations": K,
  "claim_boundary": "Discovery-only experimental titles..."
}
```

## Acceptance checklist (Phase 5)

- [ ] Only exact title matches retained
- [ ] No body scrape required for this phase
- [ ] `rights_status` remains `review_required`
- [ ] Errors counted, pipeline does not crash
- [ ] Output separate from website evidence

**STOP. Verify Phase 5 before Phase 6.**

---

# PHASE 6 — LinkedIn Guest Connectors (Experimental, Non-publishable)

## Sources

- `run_linkedin_guest_experiment.py` (profile metrics / posts / workforce labels)
- `run_linkedin_guest_jobs_connector.py` (exact-handle jobs)

## Rules (hard)

- `publishable: false`
- `rights_status: experimental`
- Never promote into strict external score without rights approval
- Exact-entity gates already in scripts must stay
- Optional phase: skip entirely if you do not want experimental surface

## Expected output (verify this)

### Profile experiment report

```json
{
  "connector": "linkedin_guest_structured_company_v2",
  "publishable": false,
  "claim_boundary": "Public logged-out company-page fields only..."
}
```

### Jobs experiment report

```json
{
  "connector": "linkedin_guest_exact_handle_jobs_v1",
  "publishable": false,
  "exact_jobs": N,
  "claim_boundary": "Logged-out LinkedIn job activity only..."
}
```

## Acceptance checklist (Phase 6)

- [ ] `publishable` is always false in reports
- [ ] Exact company URL / identity checks still enforced
- [ ] Duplicates / 404 fallbacks handled without crash
- [ ] Snapshots cached under cache-dir
- [ ] Nothing from LinkedIn written into strict website evidence

**STOP. Verify Phase 6 before Phase 7 (or skip Phase 6).**

---

# PHASE 7 — Batch Integration + Scale Check

## What to build

1. Single orchestration path (or documented commands) for:
   - website crawler (Phases 1–3)
   - annual workforce (Phase 4)
   - google news (Phase 5)
   - optional LinkedIn (Phase 6)
2. Outputs stay in existing envelope / observation style
3. One failed connector must not kill the whole company batch
4. Aggregate metrics report

## Expected metrics (100 companies)

```text
website_crawl_success_%
identity_success_%
jobs_from_website_%
method2_trigger_%
avg_cost_usd_per_company
max_cost_usd_per_company
annual_workforce_accepted
news_mentions_companies
linkedin_runs (if enabled)
batch_crash_count = 0
```

## Acceptance checklist (Phase 7)

- [ ] 100 companies complete
- [ ] max website discovery cost ≤ $0.01
- [ ] no batch-wide crash
- [ ] website evidence still under `profile["evidence"]["website"]`
- [ ] connectors write their own observation files + reports
- [ ] strict vs experimental rights clearly separated

---

# File Map

| Area | Primary files |
|------|----------------|
| Website crawler | `website.py`, `crawl_events.py`, `run_competition_batch.py`, `run_agent.py` |
| Identity | `identity.py` (reuse) |
| Page signals | `page_signals.py` (reuse) |
| Brave discovery pattern | `run_brave_discovery.py` (reuse logic; wire as Method 2 provider) |
| Annual workforce | `run_annual_report_workforce_connector.py` |
| Google News | `run_google_news_rss_connector.py` |
| LinkedIn (optional) | `run_linkedin_guest_experiment.py`, `run_linkedin_guest_jobs_connector.py` |

---

# Cost Rules (Website Path)

| Step | Budget |
|------|--------|
| Method 1 direct crawl | ~$0 |
| Exa | ≤ $0.005, max 1 call |
| Tavily | ≤ $0.005, max 1 call |
| Brave | only if budget remains, max 1 call |
| **Hard cap** | **$0.01 / company** |

Annual PDF / News RSS / LinkedIn costs are tracked **separately** (not mixed into website `cost_usd` unless you explicitly add a total budget later).

---

# Core Principle

> Phase by phase.  
> Verify expected output before the next phase.  
> Method 1 free first.  
> Method 2 paid discovery only when needed, ≤ $0.01.  
> Official workforce from annual reports is approved path.  
> News + LinkedIn stay experimental until rights allow.  
> Missing = `"not_available"`.  
> No social links in website evidence.  
> Never invent data.

---

## Immediate next step

**Start Phase 1 only.**  
After one-company verification against the expected JSON + trace events above, move to Phase 2.
