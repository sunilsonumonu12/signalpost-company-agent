# Hackathon V1 Cleanup and Removal List

This file records what should stay in the main submission and what should be removed or archived for the future crawler-based Version 2.

## Source of truth

The runtime path is:

- [run.py](../run.py)
- [scripts/run_agent.py](../scripts/run_agent.py)

The important fact is that [scripts/run_agent.py](../scripts/run_agent.py) has explicit optional stages for:

- deep crawl via Scrapy
- workforce OCR via annual-report extraction

These are not required for the baseline run and are therefore not part of a clean V1 hackathon scope.

## Keep in V1

Core runtime and business logic that supports the registry batch and result generation:

- [run.py](../run.py)
- [scripts/run_agent.py](../scripts/run_agent.py)
- [scripts/run_competition_batch.py](../scripts/run_competition_batch.py)
- [scripts/build_output_contract.py](../scripts/build_output_contract.py)
- [src/norway_company_agent/batch.py](../src/norway_company_agent/batch.py)
- [src/norway_company_agent/discovery.py](../src/norway_company_agent/discovery.py)
- [src/norway_company_agent/evidence.py](../src/norway_company_agent/evidence.py)
- [src/norway_company_agent/identity.py](../src/norway_company_agent/identity.py)
- [src/norway_company_agent/official.py](../src/norway_company_agent/official.py)
- [src/norway_company_agent/operations.py](../src/norway_company_agent/operations.py)
- [src/norway_company_agent/research.py](../src/norway_company_agent/research.py)
- [src/norway_company_agent/telemetry.py](../src/norway_company_agent/telemetry.py)
- [src/norway_company_agent/website.py](../src/norway_company_agent/website.py)
- [src/norway_company_agent/http.py](../src/norway_company_agent/http.py)
- [src/norway_company_agent/workspace.py](../src/norway_company_agent/workspace.py)

## Remove or archive for V1

These can be removed from the main submission or moved into a `v2-crawler` archive folder:

### 1. Scrapy crawler system

- [scripts/run_scrapy_websites.py](../scripts/run_scrapy_websites.py)
- [src/norway_company_agent/scrapy_crawler.py](../src/norway_company_agent/scrapy_crawler.py)
- [src/norway_company_agent/crawl_events.py](../src/norway_company_agent/crawl_events.py)

### 2. Site-content extraction

- [scripts/extract_company_site_activity.py](../scripts/extract_company_site_activity.py)
- [scripts/extract_company_site_news.py](../scripts/extract_company_site_news.py)
- [scripts/extract_company_site_careers.py](../scripts/extract_company_site_careers.py)

### 3. OCR workforce extraction

- [scripts/run_annual_report_workforce_connector.py](../scripts/run_annual_report_workforce_connector.py)
- [scripts/extract_prior_year_financials.py](../scripts/extract_prior_year_financials.py)

### 4. External experiments and exploratory connectors

- [scripts/run_brave_discovery.py](../scripts/run_brave_discovery.py)
- [scripts/run_tavily_discovery.py](../scripts/run_tavily_discovery.py)
- [scripts/run_exa_discovery.py](../scripts/run_exa_discovery.py)
- [scripts/run_google_news_rss_connector.py](../scripts/run_google_news_rss_connector.py)
- [scripts/run_linkedin_guest_experiment.py](../scripts/run_linkedin_guest_experiment.py)
- [scripts/run_linkedin_guest_jobs_connector.py](../scripts/run_linkedin_guest_jobs_connector.py)
- [scripts/run_youtube_search_connector.py](../scripts/run_youtube_search_connector.py)
- [scripts/discover_linkedin_company_profiles.py](../scripts/discover_linkedin_company_profiles.py)

## Recommended order

1. Keep only the V1 runtime path.
2. Move crawler files into a `v2-crawler` folder or archive.
3. Remove optional experimental connectors from the main submission.
4. Ensure the README clearly states: V1 is registry-first and crawler-free; V2 is crawler-enabled.

## Final positioning

For the hackathon, the clean story is:

- V1 = reproducible registry + evidence + output pipeline
- V2 = deep crawl + site intelligence + OCR + richer external discovery

This avoids presenting the crawler as part of the current release while still keeping it ready for the next version.
