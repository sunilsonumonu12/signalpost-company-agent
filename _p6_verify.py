"""Phase 6 code-inspection verification — no test assertions are test-suite tests."""
import sys, json
sys.path.insert(0, 'src')

from norway_company_agent.changes import diff_claims

ORG = "123456789"

def make_claim(field, value, availability="available"):
    return {"field": field, "value": value, "availability": availability,
            "confidence": 0.99, "evidence_ids": ["ev-1"]}

# ── 1. Scalar added ─────────────────────────────────────────────────────────
prev = []
curr = [make_claim("legal_name", "TestCo AS")]
changes = diff_claims(ORG, prev, curr)
assert len(changes) == 1 and changes[0]["change_type"] == "added", f"1 FAIL: {changes}"
assert changes[0]["previous_value"] is None
assert changes[0]["current_value"] == "TestCo AS"
print("1. scalar added: PASS")

# ── 2. Scalar removed ────────────────────────────────────────────────────────
prev = [make_claim("legal_name", "TestCo AS")]
curr = []
changes = diff_claims(ORG, prev, curr)
assert len(changes) == 1 and changes[0]["change_type"] == "removed", f"2 FAIL: {changes}"
assert changes[0]["previous_value"] == "TestCo AS"
assert changes[0]["current_value"] is None
print("2. scalar removed: PASS")

# ── 3. Scalar updated ────────────────────────────────────────────────────────
prev = [make_claim("legal_name", "TestCo AS")]
curr = [make_claim("legal_name", "TestCo Norge AS")]
changes = diff_claims(ORG, prev, curr)
assert len(changes) == 1 and changes[0]["change_type"] == "updated", f"3 FAIL: {changes}"
assert changes[0]["previous_value"] == "TestCo AS"
assert changes[0]["current_value"] == "TestCo Norge AS"
print("3. scalar updated: PASS")

# ── 4. Scalar unchanged ──────────────────────────────────────────────────────
prev = [make_claim("legal_name", "TestCo AS")]
curr = [make_claim("legal_name", "TestCo AS")]
changes = diff_claims(ORG, prev, curr)
assert changes == [], f"4 FAIL: {changes}"
print("4. scalar unchanged (no record): PASS")

# ── 5. Availability change only ──────────────────────────────────────────────
prev = [make_claim("official_website", "https://testco.no", "available")]
curr = [make_claim("official_website", "https://testco.no", "ambiguous")]
changes = diff_claims(ORG, prev, curr)
assert len(changes) == 1 and changes[0]["change_type"] == "updated", f"5 FAIL: {changes}"
assert changes[0]["previous_availability"] == "available"
assert changes[0]["current_availability"] == "ambiguous"
print("5. availability change only: PASS")

# ── 6. Array item added (stable ID, not position) ────────────────────────────
prev = [make_claim("social_profiles", [
    {"platform": "linkedin", "url": "https://linkedin.com/company/testco"},
])]
curr = [make_claim("social_profiles", [
    {"platform": "facebook", "url": "https://facebook.com/testco"},   # new
    {"platform": "linkedin", "url": "https://linkedin.com/company/testco"},  # same
])]
changes = diff_claims(ORG, prev, curr)
assert len(changes) == 1, f"6 FAIL (expected 1 change, got {len(changes)}): {changes}"
assert changes[0]["change_type"] == "added"
assert changes[0]["array_item_key"] == "https://facebook.com/testco"
print("6. array item added (stable ID): PASS")

# ── 7. Array item removed ────────────────────────────────────────────────────
prev = [make_claim("social_profiles", [
    {"platform": "linkedin", "url": "https://linkedin.com/company/testco"},
    {"platform": "facebook", "url": "https://facebook.com/testco"},
])]
curr = [make_claim("social_profiles", [
    {"platform": "linkedin", "url": "https://linkedin.com/company/testco"},
])]
changes = diff_claims(ORG, prev, curr)
assert len(changes) == 1 and changes[0]["change_type"] == "removed", f"7 FAIL: {changes}"
assert changes[0]["array_item_key"] == "https://facebook.com/testco"
print("7. array item removed: PASS")

# ── 8. Array reorder produces NO change ──────────────────────────────────────
items = [
    {"platform": "linkedin", "url": "https://linkedin.com/company/testco"},
    {"platform": "facebook", "url": "https://facebook.com/testco"},
]
prev = [make_claim("social_profiles", items)]
curr = [make_claim("social_profiles", list(reversed(items)))]
changes = diff_claims(ORG, prev, curr)
assert changes == [], f"8 FAIL (reorder produced changes): {changes}"
print("8. array reorder no false change: PASS")

# ── 9. Jobs and News are frozen (never in changes) ───────────────────────────
prev = [make_claim("jobs", {"has_careers_page": True})]
curr = [make_claim("jobs", {"has_careers_page": False})]
changes = diff_claims(ORG, prev, curr)
assert changes == [], f"9 FAIL (jobs appeared in changes): {changes}"

prev = [make_claim("news", [{"title": "Old"}])]
curr = [make_claim("news", [{"title": "New"}])]
changes = diff_claims(ORG, prev, curr)
assert changes == [], f"9b FAIL (news appeared in changes): {changes}"
print("9. jobs/news frozen: PASS")

# ── 10. End-to-end via to_public_envelope ────────────────────────────────────
import json
with open('result/envelopes_v2.jsonl') as f:
    rows = [json.loads(l) for l in f if l.strip()]

from norway_company_agent.output_contract import to_public_envelope

# First run — no previous_envelope, changes should be empty
env_a = to_public_envelope(rows[0])
assert env_a["changes"] == [], f"10a FAIL: {env_a['changes']}"

# Second run with same data as previous — all values unchanged, changes empty
env_b = to_public_envelope(rows[0], previous_envelope=env_a)
assert env_b["changes"] == [], f"10b FAIL (same data produced changes): {env_b['changes'][:3]}"

# Third run: use different company as "previous" to simulate an org-number mismatch
# Actually just mutate a claim value to simulate an update
import copy
env_prev = copy.deepcopy(env_a)
for c in env_prev["claims"]:
    if c["field"] == "legal_name":
        c["value"] = "OLD NAME AS"
        break

env_c = to_public_envelope(rows[0], previous_envelope=env_prev)
legal_name_change = [ch for ch in env_c["changes"] if ch["field"] == "legal_name"]
assert len(legal_name_change) == 1, f"10c FAIL: {legal_name_change}"
assert legal_name_change[0]["change_type"] == "updated"
assert legal_name_change[0]["previous_value"] == "OLD NAME AS"
assert env_c["changes"][0]["organisation_number"] == env_a["organisation_number"]

print("10. end-to-end via to_public_envelope: PASS")
print()
print("All verification scenarios PASS")
print(f"Phase 2 + Phase 3 validators still run (would have raised if broken).")
