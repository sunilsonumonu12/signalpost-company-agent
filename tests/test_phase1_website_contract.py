from norway_company_agent.website import normalize_website_value
from norway_company_agent.output_contract import to_public_envelope
from norway_company_agent.website_forensics import build_page_forensics


def test_normalize_website_value_uses_phase1_contract():
    value = {
        "requested_url": "https://example.no",
        "final_url": "https://www.example.no/",
        "registered_domain": "example.no",
        "title": "Example",
        "description": "Demo",
        "main_text_excerpt": "Example text",
        "pages": [
            {
                "url": "https://www.example.no/om-oss",
                "final_url": "https://www.example.no/om-oss",
                "title": "About",
                "status": 200,
                "duration_seconds": 1.1,
                "page_kind": "about",
                "extraction_state": "success",
                "text_excerpt": "We are Example.",
                "errors": [],
            }
        ],
        "identity_assessment": {"publishable": True, "score": 0.95},
    }

    normalized = normalize_website_value(value)

    assert normalized["requested_url"] == "https://example.no"
    assert normalized["pages"][0]["page_kind"] == "about"
    assert normalized["jobs"] == "not_available"
    assert normalized["news"] == "not_available"
    assert normalized["phones"] == "not_available"
    assert normalized["emails"] == "not_available"
    assert normalized["addresses"] == "not_available"
    assert normalized["locations"] == "not_available"
    assert "social_links" not in normalized
    assert normalized["discovery"]["method"] == "direct"
    assert normalized["discovery"]["cost_usd"] == 0.0


def test_public_envelope_preserves_normalized_website_evidence():
    profile = {
        "organisation_number": "123456789",
        "name": "Example AS",
        "evidence": {
            "website": {
                "field": "website",
                "status": "available",
                "source_type": "registry_linked_company_website",
                "source_url": "https://example.no",
                "retrieved_at": "2026-09-30T00:00:00Z",
                "value": {
                    "final_url": "https://example.no/",
                    "pages": [{"url": "https://example.no/about", "page_kind": "about"}],
                    "jobs": "not_available",
                    "discovery": {"method": "direct", "providers_used": [], "candidates": [], "cost_usd": 0.0},
                },
            }
        },
    }

    envelope = to_public_envelope({"profile": profile})

    website_item = next(item for item in envelope["evidence"] if item.get("module") == "website")
    assert website_item["status"] == "available"
    assert website_item["value"]["pages"][0]["page_kind"] == "about"
    assert website_item["value"]["jobs"] == "not_available"
    assert "social_links" not in website_item["value"]


def test_page_forensics_keeps_unormalized_job_and_news_evidence():
        html = """
        <html><head>
            <title>Careers and news</title>
            <meta property="article:published_time" content="2025-03-01">
            <script type="application/ld+json">
                {"@context":"https://schema.org","@graph":[
                    {"@type":"JobPosting","title":"Mechanic","datePosted":"2025-02-01","url":"https://example.no/careers/mechanic"},
                    {"@type":"NewsArticle","headline":"New workshop opened","datePublished":"2025-03-01","url":"https://example.no/news/workshop"}
                ]}
            </script>
        </head><body>
            <h1>Open positions</h1>
            <a href="/careers/mechanic">Mechanic</a>
            <a href="/careers">Careers</a>
            <h2>Company news</h2>
            <a href="/news/workshop">New workshop opened</a>
            <time datetime="2025-03-01">March 1, 2025</time>
        </body></html>
        """

        forensic = build_page_forensics(
                organisation_number="123456789",
                requested_url="https://example.no/careers",
                final_url="https://example.no/careers",
                http_status=200,
                page_kind="careers",
                html=html,
                extraction_state="static_complete",
        )

        assert forensic["careers_page_detected"] is True
        assert forensic["full_cleaned_page_text"]
        assert forensic["job_links_found"] >= 1
        assert forensic["job_candidates_found"] >= 1
        assert forensic["article_links_found"] >= 1
        assert forensic["article_candidates_found"] >= 1
        assert forensic["jsonld_structured_data"]
        assert forensic["dates_found"]
        assert forensic["normalized_jobs"] == "not_available"
        assert forensic["normalized_news"] == "not_available"
        assert forensic["all_discovered_links"]
