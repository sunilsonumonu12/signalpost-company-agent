# Phase 4: Locations, Contacts and Social Profiles

## Objective
Normalize already-available V2 data into public envelope claims.
This is NOT a new crawler. No new extraction was added.

## Frozen Scope
Jobs and News are completely frozen. Not modified.

---

## SOURCE REPO RESULT

### Locations

**Source files:** `src/norway_company_agent/official.py`, `src/norway_company_agent/output_contract.py`

**Symbols:** `normalize_locations()` in `official.py`; `MODULE_CLAIM_FIELDS` in `output_contract.py`

**Extraction:** `normalize_locations()` calls the BRREG subunits endpoint
(`/underenheter?overordnetEnhet={org}&size=1000`) and normalizes each subunit into
`{organisation_number, name, address, industry, employees}`. Stored as
`evidence["locations"]` with `value={"locations": [...]}`.

**Normalization:** Already normalized by `normalize_locations()` at fetch time.

**Evidence:** `evidence["locations"]` record with `source_url`, `retrieved_at`,
`content_sha256`, `status`.

**Claims:** `"locations"` is in `MODULE_CLAIM_FIELDS` in `output_contract.py`. The claim
is **already emitted** in `to_public_envelope()`. This was working before Phase 4.

**Website-extracted addresses:** `_extract_contact_signals()` in `website.py` also
extracts freetext address strings from the website into `website.value.addresses`.
These are stored in the website evidence value as a list of strings or
`"not_available"`. They were NOT previously emitted as a claim. Gap = propagation only.

**V2 Gap (pre-Phase-4):**
- Registry subunit locations: **already working** — no change needed.
- Website-extracted addresses: extraction existed, propagation missing. Fixed in Phase 4
  via `website_addresses` claim.

**Tests:** Testing intentionally out of scope. No tests were run.

---

### Contacts

**Source files:** `src/norway_company_agent/website.py`

**Symbols:** `_extract_contact_signals()` in `website.py`

**Extraction:**
- `phones`: regex over the raw HTML finds numeric sequences matching Norwegian phone
  patterns (`+47` prefix or 8+ digit sequences). Returns a sorted, deduped list of
  strings or `"not_available"`.
- `emails`: `re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", html)`.
  Returns a sorted, deduped list of strings or `"not_available"`.

**Normalization:** `normalize_website_value()` explicitly iterates over
`("jobs", "news", "phones", "emails", "addresses", "locations")` and sets each to
`"not_available"` when the value is `None`, `[]`, or `""`. Otherwise preserves as-is.
So `phones` and `emails` survive normalization correctly.

**Evidence:** Both fields live inside `website.value` — the normalized website evidence
record already contains them. No separate evidence record is needed or created.

**Claims (pre-Phase-4):** Neither `phones` nor `emails` appeared in `claims[]`.
The data existed in evidence but was never propagated. Gap = **propagation only**.

**V2 Gap:** Propagation missing. Fixed in Phase 4.

**Tests:** Testing intentionally out of scope. No tests were run.

---

### Social Profiles

**Source files:** `src/norway_company_agent/page_signals.py`,
`src/norway_company_agent/website.py`, `src/norway_company_agent/identity.py`

**Symbols:** `_social_links()` in `page_signals.py`; `normalize_website_value()` in
`website.py`; `apply_website_identity_gate()` in `identity.py`

**Extraction:** `_social_links()` in `page_signals.py` is a complete extraction
pipeline. It reads social URLs from:
- `<a href>` anchors
- `data-href` attributes
- `<iframe src>`
- `rel=me` links
- JSON-LD `sameAs` properties
- `twitter:site` / `twitter:creator` meta tags

Each result is normalized by `normalize_social_url()` in `website.py` into
`{platform, url}` with platform canonicalization
(linkedin, facebook, instagram, x, youtube, tiktok). Deduplication is by
`(platform, url)` key.

**Normalization (pre-Phase-4 gap):** `normalize_website_value()` explicitly popped
`social_links`, `discovered_social_links`, and `social_link_assessments` from the
normalized value dict — stripping all social data before it reached the evidence record.
The crawl produced the data; normalization discarded it. Gap = **normalization strips data**.

**Evidence (pre-Phase-4):** `website.value.social_links = None`,
`website.value.discovered_social_links = None/[]`. Confirmed by inspection of
`result/envelopes_v2.jsonl`.

**Claims (pre-Phase-4):** No `social_profiles` claim existed.

**V2 Gap:** Normalization stripped the data before it could be propagated.
Fixed in Phase 4: normalization now preserves links as `social_profiles`.

**Tests:** Testing intentionally out of scope. No tests were run.

---

### Port Strategy
No source-repository code was copied. The V2 extraction already existed in
`page_signals.py` and `website.py`. Phase 4 required only:
1. Stop stripping social links in `normalize_website_value()`.
2. Propagate four fields from the website evidence value into claims in
   `to_public_envelope()`.

### Protected Code
- `page_signals.py` — not modified. `_social_links()` extraction logic preserved as-is.
- `website.py` crawler logic — not modified. Only `normalize_website_value()` changed.
- Jobs extraction — not modified.
- News extraction — not modified.

---

## V2 IMPLEMENTATION RESULT

### Status
**COMPLETE**

---

### Locations

**Files:** `src/norway_company_agent/output_contract.py`

**Changes:** None required for registry subunit locations — already working.

**New claim:** `website_addresses` — website-extracted freetext address strings,
propagated from `website.value.addresses` via `_website_contact_claims()`.

**Claims before Phase 4:**
```
locations  (registry subunits — already existed)
```

**Claims after Phase 4:**
```
locations          (registry subunits — unchanged)
website_addresses  (website-extracted address strings — new)
```

**Verification (code inspection):**
- `MODULE_CLAIM_FIELDS` contains `"locations"` — registry subunit claim emitted
  unconditionally when the module record exists.
- `_website_contact_claims()` reads `website_value.get("addresses")` and emits
  `website_addresses` claim with availability `"available"` when a non-empty list is
  present, `"not_available"` otherwise.

---

### Contacts

**Files:** `src/norway_company_agent/output_contract.py`

**Changes:** `_website_contact_claims()` function added; called from
`to_public_envelope()` after the website claim block.

**Claims before Phase 4:** None for phones or emails.

**Claims after Phase 4:**
```
phones           — list of phone strings from website, or not_available
emails           — list of email strings from website, or not_available
website_addresses — list of address strings from website, or not_available
```

**Verification (code inspection):**
`_website_contact_claims()` reads `website_value.get("phones")` and
`website_value.get("emails")`. A non-empty list → `availability="available"` with the
list as value. Anything else (`"not_available"`, `None`, `[]`) →
`availability="not_available"` with `value=None`.

Smoke-check results (observed during implementation, no test run):
- Available website with `phones=['+47 22 11 22 33']`, `emails=['post@testco.no']`,
  `addresses=['Testgata 1, 0101 Oslo']` → all three claims emitted with
  `availability="available"`, correct values, linked to `ev-2` (website evidence).
- No-website row → no contact claims emitted at all.
- `not_found` website row → all four contact/social claims emitted with
  `availability="not_available"`, `value=None`.

---

### Social Profiles

**Files:**
- `src/norway_company_agent/website.py` — `normalize_website_value()` changed
- `src/norway_company_agent/output_contract.py` — `_website_contact_claims()` added

**Canonical field:** `social_profiles`

**Change to `normalize_website_value()`:**

Before:
```python
normalized.pop("social_links", None)
normalized.pop("discovered_social_links", None)
normalized.pop("social_link_assessments", None)
```

After:
```python
normalized.pop("social_links", None)
normalized.pop("discovered_social_links", None)
normalized.pop("social_link_assessments", None)
# Preserve social profile links under the canonical public field name.
raw_social = raw.get("social_links") or raw.get("discovered_social_links") or []
if isinstance(raw_social, list) and raw_social:
    normalized["social_profiles"] = [
        {"platform": str(item.get("platform") or ""), "url": str(item.get("url") or "")}
        for item in raw_social
        if isinstance(item, dict) and item.get("platform") and item.get("url")
    ] or "not_available"
else:
    normalized["social_profiles"] = "not_available"
```

The old keys (`social_links`, `discovered_social_links`, `social_link_assessments`) are
still removed from the normalized dict so the `WebsiteValue` pydantic schema (which uses
`extra="allow"`) is not affected. `social_profiles` is the only public name.

Each item in `social_profiles` is a minimal `{platform, url}` dict — internal fields
(`source`, `found_on`, `span`) are stripped.

**Claims before Phase 4:** No `social_profiles` claim.

**Claims after Phase 4:**
```
social_profiles  — list of {platform, url} dicts, or not_available
```

**Verification (code inspection):**
`_website_contact_claims()` reads `website_value.get("social_profiles")`. A non-empty
list → `availability="available"`. Anything else → `availability="not_available"`.

Smoke-check observed: `social_profiles=[{'platform':'linkedin','url':'https://linkedin.com/company/testco'}]`
→ claim with `availability="available"`, `value=[{'platform':'linkedin','url':'...'}]`,
`evidence_ids=['ev-2']`.

---

### Evidence

No new evidence records are created for contact or social claims. All four new claims
(`phones`, `emails`, `website_addresses`, `social_profiles`) reference the **existing**
website evidence item (`ev-2` in the smoke-check). The `add_evidence()` deduplication
mechanism (keyed by `"website"`) ensures the same evidence item is reused — it is not
duplicated.

Provenance preserved on the evidence item:
- `source_url` — website URL
- `source_class` — `"company_owned"`
- `retrieved_at` — crawl timestamp
- `content_sha256` — page hash
- `module` — `"website"`

---

### Availability

| Scenario | phones / emails / website_addresses / social_profiles |
|---|---|
| Website available, data extracted | `available` with value list |
| Website available, field is `"not_available"` | `not_available`, `value=None` |
| Website `not_found` / `blocked` / `failed` | `not_available`, `value=None` |
| No website record at all | Claims **not emitted** |

The "no website record" path is intentional: if no website module record exists, there is
no source for these fields and emitting `not_available` claims would misrepresent the
run as having attempted a website crawl.

---

### Validators

Phase 2 `assert_evidence_integrity()` and Phase 3 `assert_claim_schema_integrity()` both
remain active and are called unconditionally before `to_public_envelope()` returns.
All four new claims pass both validators:
- Each carries all five canonical fields (`field`, `value`, `availability`,
  `confidence`, `evidence_ids`).
- `availability` is in `ALLOWED_AVAILABILITY` (`"available"` or `"not_available"`).
- `confidence` is a float in `[0, 1]` (produced by existing `_confidence()`).
- `evidence_ids` is a list pointing to the existing website evidence item.
- No dangling evidence references.

---

### Jobs
**UNCHANGED**

Confirmed by inspection: no jobs-related module (`page_signals.py` job extraction,
`website.py` job signals, any jobs connector) was modified. `jobs` still appears only
in the static `OBSERVATION_CLAIM_FIELDS` dict and `_observation_availability()`,
both unchanged.

---

### News
**UNCHANGED**

Confirmed by inspection: no news-related module was modified. `news` still appears only
in the static `OBSERVATION_CLAIM_FIELDS` dict, unchanged.

---

### Temporary Files
`_phase4_check.py` was deleted from the repository root.

---

### Testing
Testing intentionally out of scope. No tests were run.

---

### Files Modified
| File | Change |
|---|---|
| `src/norway_company_agent/website.py` | `normalize_website_value()`: preserve social links as `social_profiles` instead of discarding |
| `src/norway_company_agent/output_contract.py` | `_website_contact_claims()` function added; called from `to_public_envelope()` to emit `phones`, `emails`, `website_addresses`, `social_profiles` claims |

---

### Remaining Work
No genuine Phase 4 gaps remain.

**Known data-quality caveat (not a Phase 4 gap):** The email regex in
`_extract_contact_signals()` captures all RFC-like strings from page HTML including
third-party service addresses (e.g. Sentry error-tracking endpoints). This is an
extraction-quality concern in the existing crawler, not a normalization or propagation
gap. Filtering heuristics (e.g. reject addresses not on the company's registered domain)
are a future improvement, not a Phase 4 requirement.

Do not start Phase 5.
