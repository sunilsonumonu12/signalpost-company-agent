# V3 claims and evidence implementation

## What changed

- `scripts/build_output_contract.py`: `attach_claims()` projects each completed fat envelope through `to_public_envelope()` and merges only `claims` and `evidence`. Both `build_envelope()` branches call it after observations are attached. Per-row projection errors now preserve the fat envelope, emit empty arrays plus `claims_error`, and are counted in `contract-report.json` as `claims_failed` and `claims_failed_orgs`.
- `src/norway_company_agent/output_contract.py`: `OBSERVATION_CLAIM_FIELDS` now maps `workforce_snapshot` to `workforce` and `profile_metrics` to `activity`.
- `src/norway_company_agent/output_contract.py`: observation evidence now uses `observation.get("source_class") or "unknown"` rather than the previous hard-coded `"company_owned"`.

## Offline verification

Syntax check used:

```powershell
python -B -c "from pathlib import Path; [compile(Path(p).read_text(encoding='utf-8'),p,'exec') for p in ['scripts/build_output_contract.py','src/norway_company_agent/output_contract.py']]; print('syntax: ok')"
```

Exact replay command used (it only reads the shipped files and prints results):

```powershell
python -B -c "import json,sys,types; from pathlib import Path; stub=types.ModuleType('norway_company_agent.website'); stub.normalize_website_value=lambda value:value; sys.modules['norway_company_agent.website']=stub; sys.path.insert(0,'scripts'); from build_output_contract import build_envelope, observations_by_organisation; base=Path('out/latest-run'); rows=[json.loads(x) for x in (base/'envelopes.jsonl').read_text(encoding='utf-8').splitlines() if x.strip()]; paths=['activity-observations.jsonl','news-observations.jsonl','careers-observations.jsonl','workforce-observations.jsonl','prior-year-financials-observations.jsonl']; obs=observations_by_organisation([str(base/p) for p in paths]); envs=[build_envelope(r,run_id='',started_at='',completed_at='',observations=obs.get(str(r.get('organisation_number') or (r.get('profile') or {}).get('organisation_number')),[])) for r in rows]; assert len(envs)==20; assert all(e.get('claims') and e.get('evidence') for e in envs); assert all(all(i in {x['id'] for x in e['evidence']} for c in e['claims'] for i in c['evidence_ids']) for e in envs); workforce_obs=sum(1 for xs in obs.values() for o in xs if o.get('signal_type')=='workforce_snapshot'); profile_metrics_obs=sum(1 for xs in obs.values() for o in xs if o.get('signal_type')=='profile_metrics'); workforce=[e for e in envs if any(c['field']=='workforce' for c in e['claims'])]; activity=[e for e in envs if any(c['field']=='activity' for c in e['claims'])]; assert workforce_obs==15 and len(workforce)==15, (workforce_obs,len(workforce)); assert profile_metrics_obs==1 and len(activity)>=1, (profile_metrics_obs,len(activity)); official=[x for e in envs for x in e['evidence'] if x.get('module') in {'workforce','prior_year_financials'}]; assert official and all(x['source_class']=='official_annual_account_copy' for x in official), official; assert all(isinstance(c['confidence'],(int,float)) and not isinstance(c['confidence'],bool) for e in envs for c in e['claims']); print(json.dumps({'rows':len(envs),'claims_failed':0,'workforce_snapshot_observations':workforce_obs,'workforce_claim_rows':len(workforce),'profile_metrics_observations':profile_metrics_obs,'activity_claim_rows':len(activity),'official_observation_evidence':len(official)}))"
```

The in-memory `norway_company_agent.website` stub is necessary because this environment lacks optional website dependencies. The result was:

```text
{"rows": 20, "claims_failed": 0, "workforce_snapshot_observations": 15,
 "workforce_claim_rows": 15, "profile_metrics_observations": 1,
 "activity_claim_rows": 1, "official_observation_evidence": 30}
```

The replay also asserted that every claim evidence ID resolves, all claim confidences are numeric, and workforce/prior-year evidence has `source_class` `official_annual_account_copy`.

`python -B -m pytest -q tests` could not run because `pytest` is not installed; the normal import path also lacks `bs4`.

## Files

Modified by this implementation: `scripts/build_output_contract.py`, `src/norway_company_agent/output_contract.py`, and this record. No file under `result/` or `out/` was modified by this implementation.

Claims were not added now or manually. They will appear automatically on the user's next normal run.

## Remaining gaps

Website paths with populated jobs, news, or many crawled pages remain lightly exercised by the shipped 20-row replay. The added claims/evidence roughly double output size.

Verified by offline replay on the 20 shipped rows. No collection run, no network calls.
