# Phase 7: Final Validation and Regression

## Objective
Prove V2 gained the required capabilities without breaking existing behavior.

## Do Not Touch
Jobs and News remain frozen.

## Final Audit Prompt
```text
Perform a READ-ONLY final audit of V2.

Do not modify code.

Validate:
1. envelope top-level schema
2. claim schema
3. evidence schema
4. evidence reference integrity
5. confidence consistency
6. availability consistency
7. module state validity
8. module/claim synchronization
9. locations normalization
10. contact normalization
11. social profile normalization
12. operations metrics
13. error representation
14. changes generation
15. registry behavior
16. financial behavior
17. financial-history behavior
18. roles behavior
19. website behavior

Jobs and News must be explicitly reported as:
OUT OF SCOPE / UNCHANGED.

Return:
CHECK | PASS/FAIL | EVIDENCE | REQUIRED ACTION
```

## Regression Checklist
- [ ] 1-company smoke test
- [ ] Claims test
- [ ] Evidence linkage test
- [ ] Schema validator
- [ ] Locations test
- [ ] Contact test
- [ ] Social profile test
- [ ] Operations test
- [ ] Changes test
- [ ] 20-company regression
- [ ] No existing module regression
- [ ] Jobs unchanged
- [ ] News unchanged

## Final Acceptance
Every required check passes and no protected module regresses.

## Mandatory Result Artifact

This file is the final audit record.

### Final Audit Result

```text
## FINAL RESULT

### Status
BLOCKED — runtime validation was explicitly deferred by the user.

### Claims
STATIC PASS
Evidence: `assert_claim_schema_integrity()` requires field, value, availability, confidence, and evidence_ids for every claim.

### Evidence Linkage
STATIC PASS
Dangling IDs: `assert_evidence_integrity()` checks every claim evidence ID against envelope evidence IDs at envelope construction.

### Confidence
STATIC PASS
Missing confidence: the schema validator permits only `None` or a numeric value in `[0, 1]`.

### Availability
STATIC PASS
Invalid states: rejected by `ALLOWED_AVAILABILITY` validation.

### Locations
STATIC PASS
`normalize_locations()` emits organisation number, name, address, industry, and employees.

### Contacts
STATIC PASS
Website contact claims are normalized into phones, emails, and website_addresses.

### Social Profiles
STATIC PASS
Social profiles use normalized URLs and Phase 6 uses their URL as the stable diff key.

### Operations
STATIC PASS
`to_public_envelope()` builds operations from supplied metrics or derived row/profile operations.

### Errors
STATIC PASS
Envelope construction collects module error records while claim/evidence integrity remains enforced.

### Changes
STATIC PASS
`changes.py` compares normalized claims; it suppresses reordering and excludes Jobs and News.

### Registry Regression
NOT RUN

### Financial Regression
NOT RUN

### Financial History Regression
NOT RUN

### Roles Regression
NOT RUN

### Website Regression
NOT RUN

### Jobs
UNCHANGED — no implementation edits made to Jobs and it is excluded from change reporting.

### News
UNCHANGED — no implementation edits made to News and it is excluded from change reporting.

### Files Changed
`src/norway_company_agent/changes.py`, `src/norway_company_agent/output_contract.py`, `tests/test_phase6_changes.py`, and the Phase 6/7 result artifacts.

### Tests Run
None after the instruction to avoid testing.

### Final 20-Company Regression
NOT RUN

### Remaining Gaps
Runtime behavior, dependency-backed envelope generation, and regression compatibility have not been executed.

### Recommended Next Action
Review the static audit and request targeted validation later only if it becomes necessary.
```

This section is the final handoff record. It must be filled before declaring the upgrade complete.
