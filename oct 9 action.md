remove _p6_verify it is making thing un tidy
how to use testing check kar 
kitne modules hai result mai aur baaki code mai check kar ke bata 

PS C:\Users\sunil.sonu\Downloads\signalpost-company-agent-main\signalpost-company-agent-main> python run.py --organisations entry-companies.jsonl --include-workforce-ocr
[06:37:47] RUN STARTED | total=9
+ C:\Users\sunil.sonu\AppData\Local\Programs\Python\Python314\python.exe C:\Users\sunil.sonu\Downloads\signalpost-company-agent-main\signalpost-company-agent-main\scripts\run_competition_batch.py--organisations C:\Users\sunil.sonu\Downloads\signalpost-company-agent-main\signalpost-company-agent-main\entry-companies.jsonl --profiles-output C:\Users\sunil.sonu\Downloads\signalpost-company-agent-main\signalpost-company-agent-main\out\run-20261014-010747-274033\profiles-stage1.jsonl --output C:\Users\sunil.sonu\Downloads\signalpost-company-agent-main\signalpost-company-agent-main\out\run-20261014-010747-274033\registry-envelopes.jsonl --report C:\Users\sunil.sonu\Downloads\signalpost-company-agent-main\signalpost-company-agent-main\out\run-20261014-010747-274033\registry-report.json --run-id run-20261014-010747-274033 --expected-count 9 --progress-file C:\Users\sunil.sonu\Downloads\signalpost-company-agent-main\signalpost-company-agent-main\out\run-20261014-010747-274033\progress.json --modules registry,accounting_obligation,registry_live,financials,financial_history,roles,group,locations,website
[06:37:50] START company 810034882
[06:37:50] START company 810059672
[06:37:50] START company 810094532
[06:37:50] START company 810098252
[06:37:50] START company 810105372
[06:37:50] START company 810130822
[06:37:50] START company 810182482
[06:37:50] START company 810202572
[06:37:50] START company 810274042
[06:38:17] PROGRESS | companies=0/9 | completed=0 | success=0 | failed=0 | active=9 | rate=0.00 companies/sec | elapsed=30s | last_company=810274042 | since_last_completion=27s
[06:38:20] END company 810130822 | 30.1 sec | SUCCESS
[06:38:20] END company 810274042 | 30.1 sec | SUCCESS
[06:38:20] END company 810202572 | 30.1 sec | SUCCESS
[06:38:20] END company 810182482 | 30.2 sec | SUCCESS
[06:38:32] END company 810098252 | 42.4 sec | SUCCESS
[06:38:32] END company 810094532 | 42.5 sec | SUCCESS
[06:38:32] END company 810034882 | 42.5 sec | SUCCESS
[06:38:32] END company 810105372 | 42.5 sec | SUCCESS
[06:38:34] END company 810059672 | 44.1 sec | SUCCESS
AVERAGE company time: 37.2 sec
FASTEST company: 810274042 | 30.1 sec
SLOWEST company: 810059672 | 44.1 sec

============================================================
BATCH FAILURE SUMMARY
============================================================
  Total companies   : 9/9
  Successful        : 9
  Failed            : 0
  Total retries     : 0
============================================================

+ C:\Users\sunil.sonu\AppData\Local\Programs\Python\Python314\python.exe C:\Users\sunil.sonu\Downloads\signalpost-company-agent-main\signalpost-company-agent-main\archive\v2-crawler\run_tavily_discovery.py --input C:\Users\sunil.sonu\Downloads\signalpost-company-agent-main\signalpost-company-agent-main\out\run-20261014-010747-274033\profiles-stage1.jsonl --output C:\Users\sunil.sonu\Downloads\signalpost-company-agent-main\signalpost-company-agent-main\out\run-20261014-010747-274033\profiles-with-tavily.jsonl --report C:\Users\sunil.sonu\Downloads\signalpost-company-agent-main\signalpost-company-agent-main\out\run-20261014-010747-274033\tavily-discovery-report.json --limit 9 --promote-verified
[06:38:47] PROGRESS | companies=9/9 | completed=9 | success=9 | failed=0 | active=0 | rate=0.15 companies/sec | elapsed=60s | last_company=810059672 | since_last_completion=13s
[06:39:17] PROGRESS | companies=9/9 | completed=9 | success=9 | failed=0 | active=0 | rate=0.10 companies/sec | elapsed=90s | last_company=810059672 | since_last_completion=43s
[06:39:47] PROGRESS | companies=9/9 | completed=9 | success=9 | failed=0 | active=0 | rate=0.07 companies/sec | elapsed=120s | last_company=810059672 | since_last_completion=73s
[06:40:17] PROGRESS | companies=9/9 | completed=9 | success=9 | failed=0 | active=0 | rate=0.06 companies/sec | elapsed=150s | last_company=810059672 | since_last_completion=103s
{
  "generated_at": "2026-10-14T01:10:26.058014Z",
  "started_at": "2026-10-14T01:08:36.672219Z",
  "provider": "Tavily Search API",
  "provider_name": "Tavily",
  "provider_endpoint": "https://api.tavily.com/search",
  "access_mode": "api_key",
  "input_profiles": 9,
  "queried_missing_website_profiles": 9,
  "counts": {
    "provider_requests": 9,
    "provider_bytes": 125348,
    "abstained_before_crawl": 8,
    "independent_crawls": 1,
    "crawl_requests": 6,
    "quarantined_sites": 1
  },
  "provider_latency_ms": {
    "p50": 12391,
    "p95": 13014
  },
  "raw_search_results_persisted": false,
  "promote_verified_enabled": true,
  "qualification": "not_evaluated_on_500_org_external_final_corpus",
  "discovery_metrics": {
    "provider_name": "Tavily",
    "eligible_count": 9,
    "triggered_count": 9,
    "successful_count": 0,
    "failed_count": 0,
    "abstained_count": 8,
    "companies_attempted": [
      "810034882",
      "810059672",
      "810094532",
      "810098252",
      "810105372",
      "810130822",
      "810182482",
      "810202572",
      "810274042"
    ]
  },
  "company_outcomes": [
    {
      "organisation_number": "810034882",
      "outcome": "abstained",
      "provider": "Tavily"
    },
    {
      "organisation_number": "810059672",
      "outcome": "abstained",
      "provider": "Tavily"
    },
    {
      "organisation_number": "810094532",
      "outcome": "abstained",
      "provider": "Tavily"
    },
    {
      "organisation_number": "810098252",
      "outcome": "abstained",
      "provider": "Tavily"
    },
    {
      "organisation_number": "810105372",
      "outcome": "abstained",
      "provider": "Tavily"
    },
    {
      "organisation_number": "810130822",
      "outcome": "quarantined",
      "provider": "Tavily"
    },
    {
      "organisation_number": "810182482",
      "outcome": "abstained",
      "provider": "Tavily"
    },
    {
      "organisation_number": "810202572",
      "outcome": "abstained",
      "provider": "Tavily"
    },
    {
      "organisation_number": "810274042",
      "outcome": "abstained",
      "provider": "Tavily"
    }
  ],
  "eligible_count": 9,
  "triggered_count": 9,
  "successful_count": 0,
  "failed_count": 0,
  "abstained_count": 8,
  "companies_attempted": [
    "810034882",
    "810059672",
    "810094532",
    "810098252",
    "810105372",
    "810130822",
    "810182482",
    "810202572",
    "810274042"
  ]
}
+ C:\Users\sunil.sonu\AppData\Local\Programs\Python\Python314\python.exe C:\Users\sunil.sonu\Downloads\signalpost-company-agent-main\signalpost-company-agent-main\archive\v2-crawler\run_exa_discovery.py --input C:\Users\sunil.sonu\Downloads\signalpost-company-agent-main\signalpost-company-agent-main\out\run-20261014-010747-274033\profiles-with-tavily.jsonl --output C:\Users\sunil.sonu\Downloads\signalpost-company-agent-main\signalpost-company-agent-main\out\run-20261014-010747-274033\profiles-with-exa.jsonl --report C:\Users\sunil.sonu\Downloads\signalpost-company-agent-main\signalpost-company-agent-main\out\run-20261014-010747-274033\exa-discovery-report.json --limit 9 --promote-verified
[06:40:47] PROGRESS | companies=9/9 | completed=9 | success=9 | failed=0 | active=0 | rate=0.05 companies/sec | elapsed=180s | last_company=810059672 | since_last_completion=133s
{
  "generated_at": "2026-10-14T01:11:14.315901Z",
  "started_at": "2026-10-14T01:10:28.357388Z",
  "provider": "Exa Search API",
  "provider_name": "Exa",
  "provider_endpoint": "https://api.exa.ai/search",
  "input_profiles": 9,
  "queried_missing_website_profiles": 9,
  "counts": {
    "provider_requests": 9,
    "provider_bytes": 99800,
    "abstained_before_crawl": 9
  },
  "provider_latency_ms": {
    "p50": 1269,
    "p95": 11835
  },
  "total_cost_usd": 0.063,
  "raw_search_results_persisted": false,
  "promote_verified_enabled": true,
  "qualification": "not_evaluated_on_500_org_external_final_corpus",
  "discovery_metrics": {
    "provider_name": "Exa",
    "eligible_count": 9,
    "triggered_count": 9,
    "successful_count": 0,
    "failed_count": 0,
    "abstained_count": 9,
    "companies_attempted": [
      "810034882",
      "810059672",
      "810094532",
      "810098252",
      "810105372",
      "810130822",
      "810182482",
      "810202572",
      "810274042"
    ]
  },
  "company_outcomes": [
    {
      "organisation_number": "810034882",
      "outcome": "abstained",
      "provider": "Exa"
    },
    {
      "organisation_number": "810059672",
      "outcome": "abstained",
      "provider": "Exa"
    },
    {
      "organisation_number": "810094532",
      "outcome": "abstained",
      "provider": "Exa"
    },
    {
      "organisation_number": "810098252",
      "outcome": "abstained",
      "provider": "Exa"
    },
    {
      "organisation_number": "810105372",
      "outcome": "abstained",
      "provider": "Exa"
    },
    {
      "organisation_number": "810130822",
      "outcome": "abstained",
      "provider": "Exa"
    },
    {
      "organisation_number": "810182482",
      "outcome": "abstained",
      "provider": "Exa"
    },
    {
      "organisation_number": "810202572",
      "outcome": "abstained",
      "provider": "Exa"
    },
    {
      "organisation_number": "810274042",
      "outcome": "abstained",
      "provider": "Exa"
    }
  ],
  "eligible_count": 9,
  "triggered_count": 9,
  "successful_count": 0,
  "failed_count": 0,
  "abstained_count": 9,
  "companies_attempted": [
    "810034882",
    "810059672",
    "810094532",
    "810098252",
    "810105372",
    "810130822",
    "810182482",
    "810202572",
    "810274042"
  ]
}
scrapy not installed -- skipping deep crawl and activity/news extraction.
+ C:\Users\sunil.sonu\AppData\Local\Programs\Python\Python314\python.exe C:\Users\sunil.sonu\Downloads\signalpost-company-agent-main\signalpost-company-agent-main\archive\v2-crawler\run_annual_report_workforce_connector.py --profiles C:\Users\sunil.sonu\Downloads\signalpost-company-agent-main\signalpost-company-agent-main\out\run-20261014-010747-274033\profiles.jsonl --organisations C:\Users\sunil.sonu\Downloads\signalpost-company-agent-main\signalpost-company-agent-main\out\run-20261014-010747-274033\all-orgs.txt --output C:\Users\sunil.sonu\Downloads\signalpost-company-agent-main\signalpost-company-agent-main\out\run-20261014-010747-274033\workforce-observations.jsonl --cache C:\Users\sunil.sonu\Downloads\signalpost-company-agent-main\signalpost-company-agent-main\cache\workforce-cache --report C:\Users\sunil.sonu\Downloads\signalpost-company-agent-main\signalpost-company-agent-main\out\run-20261014-010747-274033\workforce-report.json
[06:41:17] PROGRESS | companies=9/9 | completed=9 | success=9 | failed=0 | active=0 | rate=0.04 companies/sec | elapsed=210s | last_company=810059672 | since_last_completion=163s
[06:41:47] PROGRESS | companies=9/9 | completed=9 | success=9 | failed=0 | active=0 | rate=0.04 companies/sec | elapsed=240s | last_company=810059672 | since_last_completion=193s
{
  "connector": "official_annual_report_workforce_v1",
  "eligible": 9,
  "accepted": 0,
  "status_counts": {
    "registry_count_already_available": 2,
    "error": 7
  },
  "claim_boundary": "Latest official PDF, exact organisation number in document, company-scope employee phrase only; group phrases and conflicts abstain."
}
+ C:\Users\sunil.sonu\AppData\Local\Programs\Python\Python314\python.exe C:\Users\sunil.sonu\Downloads\signalpost-company-agent-main\signalpost-company-agent-main\archive\v2-crawler\extract_prior_year_financials.py --profiles C:\Users\sunil.sonu\Downloads\signalpost-company-agent-main\signalpost-company-agent-main\out\run-20261014-010747-274033\profiles.jsonl --cache C:\Users\sunil.sonu\Downloads\signalpost-company-agent-main\signalpost-company-agent-main\cache\workforce-cache --output C:\Users\sunil.sonu\Downloads\signalpost-company-agent-main\signalpost-company-agent-main\out\run-20261014-010747-274033\prior-year-financials-observations.jsonl --report C:\Users\sunil.sonu\Downloads\signalpost-company-agent-main\signalpost-company-agent-main\out\run-20261014-010747-274033\prior-year-financials-report.json
{
  "connector": "prior_year_financials_v1",
  "profiles": 9,
  "eligible": 7,
  "accepted": 0,
  "observations": 0,
  "claim_boundary": "Only fields where the known current-year value was found as an exact prefix of the OCR'd digit sequence; the remainder is trusted as the prior year. Abstains rather than guesses a split point."
}
+ C:\Users\sunil.sonu\AppData\Local\Programs\Python\Python314\python.exe C:\Users\sunil.sonu\Downloads\signalpost-company-agent-main\signalpost-company-agent-main\scripts\build_output_contract.py--profiles C:\Users\sunil.sonu\Downloads\signalpost-company-agent-main\signalpost-company-agent-main\out\run-20261014-010747-274033\profiles.jsonl --output C:\Users\sunil.sonu\Downloads\signalpost-company-agent-main\signalpost-company-agent-main\out\run-20261014-010747-274033\envelopes.jsonl --report C:\Users\sunil.sonu\Downloads\signalpost-company-agent-main\signalpost-company-agent-main\out\run-20261014-010747-274033\contract-report.json --run-id run-20261014-010747-274033 --started-at 2026-10-14T01:07:47Z --completed-at 2026-10-14T01:11:55Z --observations C:\Users\sunil.sonu\Downloads\signalpost-company-agent-main\signalpost-company-agent-main\out\run-20261014-010747-274033\workforce-observations.jsonl --observations C:\Users\sunil.sonu\Downloads\signalpost-company-agent-main\signalpost-company-agent-main\out\run-20261014-010747-274033\prior-year-financials-observations.jsonl
+ C:\Users\sunil.sonu\AppData\Local\Programs\Python\Python314\python.exe C:\Users\sunil.sonu\Downloads\signalpost-company-agent-main\signalpost-company-agent-main\scripts\validate_envelopes.py --input C:\Users\sunil.sonu\Downloads\signalpost-company-agent-main\signalpost-company-agent-main\out\run-20261014-010747-274033\envelopes.jsonl --report C:\Users\sunil.sonu\Downloads\signalpost-company-agent-main\signalpost-company-agent-main\out\run-20261014-010747-274033\validation-report.json --run-id run-20261014-010747-274033 --expected-count 9
VALIDATION PASSED | 9 envelopes | report: C:\Users\sunil.sonu\Downloads\signalpost-company-agent-main\signalpost-company-agent-main\out\run-20261014-010747-274033\validation-report.json
{
  "schema_version": "1.0",
  "run_id": "run-20261014-010747-274033",
  "started_at": "2026-10-14T01:07:47Z",
  "completed_at": "2026-10-14T01:11:55Z",
  "stages_run": [
    "registry_batch",
    "tavily_discovery",
    "exa_discovery",
    "workforce_ocr",
    "prior_year_financials",
    "claims_conversion",
    "envelope_validation"
  ],
  "profiles": "C:\\Users\\sunil.sonu\\Downloads\\signalpost-company-agent-main\\signalpost-company-agent-main\\out\\run-20261014-010747-274033\\profiles.jsonl",
  "envelopes": "C:\\Users\\sunil.sonu\\Downloads\\signalpost-company-agent-main\\signalpost-company-agent-main\\out\\run-20261014-010747-274033\\envelopes.jsonl",
  "validation_report": "C:\\Users\\sunil.sonu\\Downloads\\signalpost-company-agent-main\\signalpost-company-agent-main\\out\\run-20261014-010747-274033\\validation-report.json",
  "metrics": "C:\\Users\\sunil.sonu\\Downloads\\signalpost-company-agent-main\\signalpost-company-agent-main\\out\\run-20261014-010747-274033\\run-metrics.json"
}
Validation: PASSED | report: C:\Users\sunil.sonu\Downloads\signalpost-company-agent-main\signalpost-company-agent-main\result\validation-report.json
[06:42:00] COMPLETE | companies=9/9 | success=9 | failed=0 | elapsed=253s | rate=0.04 companies/sec
Final output: C:\Users\sunil.sonu\Downloads\signalpost-company-agent-main\signalpost-company-agent-main\result\envelopes.jsonl