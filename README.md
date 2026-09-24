# SignalPost — Hackathon V1 Submission

This repository is being prepared for a clean Version 1 submission that focuses on the core registry-to-output pipeline and excludes the crawler-heavy workflow.

## Why this V1 is cleaner

The actual execution flow in [run.py](run.py) calls [scripts/run_agent.py](scripts/run_agent.py), which explicitly treats the deep crawl and annual-report workforce OCR steps as optional. The pipeline supports:

- `--skip-deep-crawl`
- `--skip-workforce-ocr`

This means the crawler and OCR stages are not required for the baseline V1 run and should not be part of the initial hackathon submission.

## Keep for V1

The following files are required for the core run path and should remain in the main submission:

- [run.py](run.py)
- [scripts/run_agent.py](scripts/run_agent.py)
- [scripts/run_competition_batch.py](scripts/run_competition_batch.py)
- [scripts/build_output_contract.py](scripts/build_output_contract.py)
- [src/norway_company_agent/batch.py](src/norway_company_agent/batch.py)
- [src/norway_company_agent/discovery.py](src/norway_company_agent/discovery.py)
- [src/norway_company_agent/evidence.py](src/norway_company_agent/evidence.py)
- [src/norway_company_agent/identity.py](src/norway_company_agent/identity.py)
- [src/norway_company_agent/official.py](src/norway_company_agent/official.py)
- [src/norway_company_agent/operations.py](src/norway_company_agent/operations.py)
- [src/norway_company_agent/research.py](src/norway_company_agent/research.py)
- [src/norway_company_agent/telemetry.py](src/norway_company_agent/telemetry.py)
- [src/norway_company_agent/website.py](src/norway_company_agent/website.py)
- [src/norway_company_agent/workspace.py](src/norway_company_agent/workspace.py)
- [src/norway_company_agent/http.py](src/norway_company_agent/http.py)

## Move to V2 / defer from V1

These files belong to the crawler and high-depth harvesting path and should be excluded from the current hackathon V1 submission.

### Deep crawler / Scrapy

- [scripts/run_scrapy_websites.py](scripts/run_scrapy_websites.py)
- [src/norway_company_agent/scrapy_crawler.py](src/norway_company_agent/scrapy_crawler.py)
- [src/norway_company_agent/crawl_events.py](src/norway_company_agent/crawl_events.py)

### Site extraction from crawled pages

- [scripts/extract_company_site_activity.py](scripts/extract_company_site_activity.py)
- [scripts/extract_company_site_news.py](scripts/extract_company_site_news.py)
- [scripts/extract_company_site_careers.py](scripts/extract_company_site_careers.py)

### OCR / workforce extraction

- [scripts/run_annual_report_workforce_connector.py](scripts/run_annual_report_workforce_connector.py)
- [scripts/extract_prior_year_financials.py](scripts/extract_prior_year_financials.py)

### Secondary discovery / connector experiments

- [scripts/run_brave_discovery.py](scripts/run_brave_discovery.py)
- [scripts/run_tavily_discovery.py](scripts/run_tavily_discovery.py)
- [scripts/run_exa_discovery.py](scripts/run_exa_discovery.py)
- [scripts/run_google_news_rss_connector.py](scripts/run_google_news_rss_connector.py)
- [scripts/run_linkedin_guest_experiment.py](scripts/run_linkedin_guest_experiment.py)
- [scripts/run_linkedin_guest_jobs_connector.py](scripts/run_linkedin_guest_jobs_connector.py)
- [scripts/run_youtube_search_connector.py](scripts/run_youtube_search_connector.py)
- [scripts/discover_linkedin_company_profiles.py](scripts/discover_linkedin_company_profiles.py)

## Recommended cleanup approach

1. Keep only the registry + evidence + output-contract flow for the V1 demo.
2. Archive crawler-heavy files under a V2 folder or tag them as `v2-crawler`.
3. Keep the main submission focused on a single reproducible pipeline.
4. Mention clearly in the hackathon notes that the crawler is deferred to V2, not part of this release.

## Submission note

For the hackathon, V1 should be presented as:

- fast
- reproducible
- registry-first
- no deep crawl dependency
- crawler is intentionally deferred to Version 2
