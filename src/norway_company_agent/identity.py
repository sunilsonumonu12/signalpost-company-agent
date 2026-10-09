from __future__ import annotations

import json
import os
import re
import unicodedata
import urllib.parse
from pathlib import Path
from typing import Any

from rapidfuzz import fuzz


ORG_NUMBER_PATTERN = re.compile(r"(?<!\d)(?:\d[\s.-]?){8}\d(?!\d)")
ORG_NUMBER_LABEL_PATTERN = re.compile(
    r"\b(?:organisasjonsnummer|organisasjonsnr\.?|organisation\s+number|organization\s+number|"
    r"org\.\s*nr\.?|org(?:anisation|anization)?\.?\s*(?:no\.?|nr\.?|number)|orgnr\.?)\b",
    re.IGNORECASE,
)
ORG_NUMBER_KEYS = {
    "organisationnumber", "organizationnumber", "organisasjonsnummer", "orgnr",
    "companynumber", "businessnumber",
}
PARENT_TERMS = re.compile(r"\b(?:parent|parent company|morselskap|konsern|holding company|owner)\b", re.I)
SUBSIDIARY_TERMS = re.compile(r"\b(?:subsidiary|subcompany|datterselskap|owned by)\b", re.I)
PARTNER_TERMS = re.compile(
    r"\b(?:partner|supplier|vendor|customer|client|leverandør|leverandor|kunde|samarbeidspartner)\b",
    re.I,
)

LEGAL_AND_GENERIC = {
    "as", "asa", "ans", "da", "enk", "iks", "sa", "sam", "sti", "stiftelsen",
    "nuf", "ab", "b", "v", "limited", "ltd", "inc", "plc", "the", "og", "and",
}


def _tokens(value: Any) -> list[str]:
    text = str(value or "").translate(str.maketrans({"ø": "o", "Ø": "O", "å": "a", "Å": "A", "æ": "ae", "Æ": "AE"}))
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().casefold()
    return [token for token in re.findall(r"[a-z0-9]+", text) if token not in LEGAL_AND_GENERIC and len(token) > 1]


def normalize_organisation_number(value: Any) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    raw = re.sub(r"^no\.?\s*", "", raw, flags=re.IGNORECASE)
    if not re.fullmatch(r"[\d\s.-]+", raw):
        return None
    digits = re.sub(r"\D", "", raw)
    return digits if len(digits) == 9 else None


def is_valid_norwegian_organisation_number(value: Any) -> bool:
    number = normalize_organisation_number(value)
    if number is None:
        return False
    weights = (3, 2, 7, 6, 5, 4, 3, 2)
    remainder = 11 - sum(int(digit) * weight for digit, weight in zip(number[:8], weights)) % 11
    if remainder == 11:
        remainder = 0
    return remainder != 10 and remainder == int(number[-1])


def _name_matches(name_tokens: list[str], text: Any) -> bool:
    return bool(name_tokens and set(name_tokens).issubset(set(_tokens(text))))


def _name_similarity(name_tokens: list[str], candidate: Any) -> float:
    candidate_tokens = _tokens(candidate)
    if not name_tokens or not candidate_tokens:
        return 0.0
    return float(fuzz.token_set_ratio(" ".join(name_tokens), " ".join(candidate_tokens)))


def _load_identity_calibration() -> dict[str, Any]:
    default_path = Path(__file__).resolve().parents[2] / "identity-calibration.json"
    configured_path = Path(os.environ.get("SIGNALPOST_IDENTITY_CALIBRATION_PATH") or default_path)
    if not configured_path.is_file():
        return {"status": "uncalibrated", "path": str(configured_path)}
    try:
        config = json.loads(configured_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"status": "invalid_config", "path": str(configured_path)}
    if not isinstance(config, dict) or config.get("schema_version") != 1:
        return {"status": "invalid_config", "path": str(configured_path)}
    thresholds = config.get("thresholds") if isinstance(config.get("thresholds"), dict) else {}
    review_score = thresholds.get("review_score")
    accept_score = thresholds.get("accept_score")
    for score in (review_score, accept_score):
        if score is not None and (not isinstance(score, (int, float)) or not 0 <= score <= 100):
            return {"status": "invalid_config", "path": str(configured_path)}
    return {
        "status": str(config.get("calibration_status") or "uncalibrated"),
        "path": str(configured_path),
        "review_score": review_score,
        "accept_score": accept_score if config.get("calibration_status") == "calibrated" else None,
        "dataset_sha256": config.get("dataset_sha256"),
        "examples": config.get("examples"),
    }


def _string_leaves(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for child in value.values() for text in _string_leaves(child)]
    if isinstance(value, list):
        return [text for child in value for text in _string_leaves(child)]
    return []


def _address_match(registry_value: dict[str, Any], documents: list[dict[str, str]]) -> bool:
    page_tokens = set(_tokens(" ".join(document.get("text") or "" for document in documents)))
    address_values = _string_leaves(registry_value.get("business_address"))
    address_values.extend(_string_leaves(registry_value.get("postal_address")))
    return any(
        len(tokens := _tokens(address)) >= 2 and set(tokens).issubset(page_tokens)
        for address in address_values
    )


def _relation_for_context(value: Any, inherited: str = "unrelated") -> str:
    key = re.sub(r"[^a-z]", "", str(value or "").casefold())
    if "parent" in key or "morselskap" in key:
        return "parent"
    if "subsidiary" in key or "suborganization" in key or "datterselskap" in key:
        return "subsidiary"
    if any(term in key for term in ("partner", "supplier", "vendor", "customer", "client", "leverandor", "kunde")):
        return "partner_vendor"
    return inherited


def _numbers_in_identifier(value: Any) -> list[str]:
    candidates: list[str] = []
    if isinstance(value, str):
        normalized = normalize_organisation_number(value)
        if normalized and is_valid_norwegian_organisation_number(normalized):
            candidates.append(normalized)
    elif isinstance(value, dict):
        property_name = " ".join(str(value.get(key) or "") for key in ("propertyID", "name", "@type"))
        for key in ("value", "@value", "identifier"):
            child = value.get(key)
            if isinstance(child, str):
                normalized = normalize_organisation_number(child)
                if normalized and is_valid_norwegian_organisation_number(normalized):
                    if not property_name or re.search(r"org|company|business|brønnøysund|bronnoysund", property_name, re.I):
                        candidates.append(normalized)
            elif isinstance(child, (dict, list)):
                candidates.extend(_numbers_in_identifier(child))
    elif isinstance(value, list):
        for child in value:
            candidates.extend(_numbers_in_identifier(child))
    return list(dict.fromkeys(candidates))


def _structured_organisation_records(value: Any, name_tokens: list[str]) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []

    def walk(node: Any, relation: str = "unrelated") -> None:
        if isinstance(node, dict):
            names = [
                child for key, child in node.items()
                if key in {"name", "legalName", "alternateName"} and isinstance(child, str)
            ]
            numbers: list[str] = []
            for key, child in node.items():
                normalized_key = re.sub(r"[^a-z]", "", str(key).casefold())
                if normalized_key in ORG_NUMBER_KEYS or normalized_key == "identifier":
                    numbers.extend(_numbers_in_identifier(child))
            name_matches_target = any(_name_matches(name_tokens, name) for name in names)
            entity_relation = (
                relation if relation in {"parent", "subsidiary", "partner_vendor"}
                else "target_company" if name_matches_target else relation
            )
            for number in dict.fromkeys(numbers):
                records.append({
                    "organisation_number": number,
                    "name": " | ".join(names),
                    "relationship": entity_relation,
                    "source": "structured_data",
                })
            for key, child in node.items():
                child_relation = _relation_for_context(key, relation)
                if isinstance(child, (dict, list)):
                    walk(child, child_relation)
        elif isinstance(node, list):
            for child in node:
                walk(child, relation)

    walk(value)
    unique = {}
    for record in records:
        key = (record["organisation_number"], record["name"], record["relationship"])
        unique[key] = record
    return list(unique.values())


def _text_organisation_records(documents: list[dict[str, str]], name_tokens: list[str]) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    for document in documents:
        text = document.get("text") or ""
        for match in ORG_NUMBER_PATTERN.finditer(text):
            number = normalize_organisation_number(match.group())
            if not number or not is_valid_norwegian_organisation_number(number):
                continue
            label_start = max(0, match.start() - 100)
            label_end = min(len(text), match.end() + 40)
            if not ORG_NUMBER_LABEL_PATTERN.search(text[label_start:label_end]):
                continue
            context = text[max(0, match.start() - 220):min(len(text), match.end() + 220)]
            if PARTNER_TERMS.search(context):
                relationship = "partner_vendor"
            elif PARENT_TERMS.search(context):
                relationship = "parent"
            elif SUBSIDIARY_TERMS.search(context):
                relationship = "subsidiary"
            elif _name_matches(name_tokens, context):
                relationship = "target_company"
            else:
                relationship = "unrelated"
            records.append({
                "organisation_number": number,
                "name": context[:500],
                "relationship": relationship,
                "source": document.get("source") or "page_text",
            })
    unique = {}
    for record in records:
        key = (record["organisation_number"], record["relationship"], record["source"])
        unique[key] = record
    return list(unique.values())


def _strong_relationship(records: list[dict[str, str]]) -> str | None:
    for relationship in ("parent", "subsidiary", "partner_vendor"):
        if any(record.get("relationship") == relationship for record in records):
            return relationship
    return None


def _structured_names(value: Any) -> list[str]:
    names: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"name", "legalName", "alternateName"} and isinstance(child, str):
                names.append(child)
            else:
                names.extend(_structured_names(child))
    elif isinstance(value, list):
        for child in value:
            names.extend(_structured_names(child))
    return names


def assess_website_identity(profile: dict[str, Any]) -> dict[str, Any]:
    website = profile.get("evidence", {}).get("website", {})
    value = website.get("value") or {}
    registry = (profile.get("evidence") or {}).get("registry_live") or {}
    registry_value = registry.get("value") if isinstance(registry.get("value"), dict) else {}
    legal_name = profile.get("name") or registry_value.get("name")
    core = _tokens(legal_name)
    target_number = normalize_organisation_number(profile.get("organisation_number"))
    target_number_valid = is_valid_norwegian_organisation_number(target_number)
    hostname = urllib.parse.urlparse(value.get("final_url") or website.get("source_url") or "").hostname or ""
    structured_names = _structured_names(value.get("structured_organisations") or [])
    rendered = value.get("js_fallback") or {}
    homepage_parts = [
        value.get("title"), value.get("description"), value.get("identity_text_excerpt"),
        value.get("main_text_excerpt"), rendered.get("title"), rendered.get("main_text_excerpt"), hostname,
    ]
    documents = [{"source": "homepage", "text": " ".join(str(part or "") for part in homepage_parts)}]
    for page in value.get("pages", []):
        if not isinstance(page, dict):
            continue
        documents.append({
            "source": str(page.get("url") or page.get("final_url") or "page"),
            "text": " ".join(str(page.get(field) or "") for field in ("title", "main_text_excerpt", "text_excerpt", "identity_text_excerpt")),
        })
    candidate_text = " ".join(document["text"] for document in documents)
    normalized_candidate_text = " ".join(_tokens(candidate_text))
    candidate_tokens = set(_tokens(candidate_text))
    overlap = sorted(set(core) & candidate_tokens)
    ratio = len(overlap) / len(set(core)) if core else 0.0
    reasons = []
    parked_markers = (
        "domain is for sale", "domain for sale", "hugedomains", "parked at", "miss hosting",
        "her flytter snart en ny gjest", "has been informing visitors",
        "find the best information and most relevant links on all topics related to",
    )
    normalized_raw = unicodedata.normalize("NFKD", candidate_text).encode("ascii", "ignore").decode().casefold()
    homepage_name_match = any(_name_matches(core, part) for part in homepage_parts if part)
    structured_name_match = any(_name_matches(core, name) for name in structured_names)
    domain_name_match = _name_matches(core, hostname)
    exact_homepage_name = homepage_name_match or structured_name_match
    substantive_homepage = len(str(value.get("main_text_excerpt") or "").strip()) >= 100
    is_business_sports_club = bool(re.search(r"(?:^|\s)B\.?\s*I\.?\s*L\.?(?:\s|$)", str(legal_name or ""), re.I))
    structured_records = _structured_organisation_records(value.get("structured_organisations") or [], core)
    text_records = _text_organisation_records(documents, core)
    organisation_records = structured_records + text_records
    name_candidates = [
        (str(source), str(candidate))
        for source, candidate in (
            [("homepage_title", value.get("title")), ("homepage_description", value.get("description")),
             ("homepage_identity", value.get("identity_text_excerpt")), ("homepage_text", value.get("main_text_excerpt")),
             ("hostname", hostname)]
            + [("structured_data", name) for name in structured_names]
            + [(document["source"], document["text"][:1200]) for document in documents[1:]]
            + [(record["source"], record["name"]) for record in organisation_records]
        )
        if candidate
    ]
    best_name_candidate = max(
        (( _name_similarity(core, candidate), source) for source, candidate in name_candidates),
        default=(0.0, ""),
    )
    fuzzy_score, fuzzy_candidate_source = best_name_candidate
    calibration = _load_identity_calibration()
    calibrated_review_score = calibration.get("review_score")
    calibrated_accept_score = calibration.get("accept_score")
    calibrated_fuzzy_match = calibrated_review_score is not None and fuzzy_score >= calibrated_review_score
    target_records = [item for item in organisation_records if item["organisation_number"] == target_number]
    target_associations = [item for item in target_records if item["relationship"] == "target_company"]
    conflicting_records = [item for item in organisation_records if item["organisation_number"] != target_number]
    related_relationship = _strong_relationship(conflicting_records + target_records)
    unrelated_conflict = any(item["relationship"] == "unrelated" for item in conflicting_records)
    corroborating_evidence = []
    target_number_context_score = max(
        (
            _name_similarity(core, item.get("name"))
            for item in target_records
            if item["relationship"] not in {"parent", "subsidiary", "partner_vendor"}
        ),
        default=0.0,
    )
    organisation_number_context_match = bool(
        target_associations
        or (
            calibrated_review_score is not None
            and target_number_context_score >= calibrated_review_score
        )
    )
    if organisation_number_context_match:
        corroborating_evidence.append("organisation_number")
    if domain_name_match:
        corroborating_evidence.append("domain")
    if _address_match(registry_value, documents):
        corroborating_evidence.append("address")
    fuzzy_acceptance_eligible = bool(
        calibrated_accept_score is not None
        and fuzzy_score >= calibrated_accept_score
        and corroborating_evidence
        and not unrelated_conflict
        and not any(item["relationship"] in {"parent", "subsidiary", "partner_vendor"} for item in target_records)
    )
    if any(marker in normalized_raw for marker in parked_markers):
        decision = "REJECT"
        reasons.append("captured page is a parked, for-sale, or generic hosting placeholder")
    elif not target_number_valid:
        decision = "REJECT"
        reasons.append("target organisation number is not a valid 9-digit Norwegian MOD-11 number")
    elif is_business_sports_club and "bedriftsidrett" not in normalized_candidate_text and "b i l" not in normalized_candidate_text:
        decision = "REJECT"
        reasons.append("website has no business sports-club evidence for the target entity")
    elif conflicting_records and unrelated_conflict:
        decision = "REJECT"
        reasons.append("a different valid organisation number is associated with unrelated page context")
    elif target_associations:
        decision = "ACCEPT"
        reasons.append("valid exact organisation number is explicitly associated with the normalized target legal name")
    elif fuzzy_acceptance_eligible:
        decision = "ACCEPT"
        reasons.append("calibrated fuzzy legal-name match is corroborated by " + ", ".join(corroborating_evidence))
    elif target_records:
        decision = "REVIEW"
        reasons.append("target organisation number appears, but the nearby company context does not establish the target")
    elif related_relationship:
        decision = "REVIEW"
        reasons.append(f"page evidence associates an organisation number with a {related_relationship.replace('_', '/')} relationship")
    elif calibrated_fuzzy_match:
        decision = "REVIEW"
        reasons.append("calibrated name similarity is not sufficient without corroborating identity evidence")
    elif exact_homepage_name or domain_name_match or (ratio >= 0.5 and len(overlap) >= 2):
        decision = "REVIEW"
        reasons.append("name or domain evidence is strong, but no exact target organisation-number association was found")
    else:
        decision = "REJECT"
        reasons.append("page lacks corroborating identity evidence for the target company")

    relationship = (
        "target_company" if target_associations or fuzzy_acceptance_eligible or (decision == "REVIEW" and (exact_homepage_name or calibrated_fuzzy_match))
        else related_relationship or ("unrelated" if conflicting_records else "target_company")
    )
    score = 1.0 if decision == "ACCEPT" else 0.6 if decision == "REVIEW" else 0.1
    status = {"ACCEPT": "exact", "REVIEW": "review", "REJECT": "rejected"}[decision]
    return {
        "decision": decision,
        "status": status,
        "score": score,
        "publishable": decision == "ACCEPT",
        "relationship": relationship,
        "target_organisation_number": target_number or "invalid",
        "target_organisation_number_valid": target_number_valid,
        "candidate_organisations": organisation_records,
        "name_similarity": {
            "score": round(fuzzy_score, 3),
            "best_candidate_source": fuzzy_candidate_source,
            "calibration_status": calibration.get("status"),
            "calibration_examples": calibration.get("examples"),
            "calibration_dataset_sha256": calibration.get("dataset_sha256"),
            "review_threshold": calibrated_review_score,
            "accept_threshold": calibrated_accept_score,
            "organisation_number_context_similarity": round(target_number_context_score, 3),
            "acceptance_eligible": fuzzy_acceptance_eligible,
        },
        "corroborating_evidence": corroborating_evidence,
        "legal_name_tokens": core,
        "matched_tokens": overlap,
        "reasons": reasons,
        "method": "norwegian_org_number_context_mod11_v3",
    }


def assess_social_identity(profile: dict[str, Any], link: dict[str, str]) -> dict[str, Any]:
    core = _tokens(profile.get("name"))
    parsed = urllib.parse.urlparse(link.get("url") or "")
    handle_text = urllib.parse.unquote(parsed.path)
    handle_compact = "".join(_tokens(handle_text))
    matched = [token for token in core if token in handle_compact]
    core_compact = "".join(core)
    ratio = len(set(matched)) / len(set(core)) if core else 0.0
    if core_compact and core_compact in handle_compact:
        score = 0.98
        reason = "normalized legal-name sequence appears in the social handle"
    elif len(core) == 1 and matched:
        score = 0.95
        reason = "single distinctive legal-name token appears in the social handle"
    elif ratio >= 0.75 and len(set(matched)) >= 2:
        score = 0.9
        reason = "most legal-name tokens appear in the social handle"
    else:
        score = 0.3
        reason = "social handle lacks strong exact-entity name evidence"
    return {
        **link,
        "identity_score": score,
        "publishable": score >= 0.9,
        "matched_tokens": matched,
        "reason": reason,
        "method": "deterministic_social_handle_identity_v1",
    }


def apply_website_identity_gate(profile: dict[str, Any], website: dict[str, Any]) -> dict[str, Any]:
    if website.get("status") != "available":
        return {"website": website, "assessment": None, "quarantined_social_links": 0}
    temporary_profile = {**profile, "evidence": {**profile.get("evidence", {}), "website": website}}
    value = website.get("value") or {}
    value = dict(value)
    assessment = assess_website_identity(temporary_profile)
    value["identity_assessment"] = assessment
    original = list(value.get("discovered_social_links") or value.get("social_links") or [])
    value["discovered_social_links"] = original
    value.pop("social_links", None)
    value.pop("social_link_assessments", None)
    website["value"] = value
    if assessment["decision"] == "REJECT":
        website = {
            **website,
            "status": "source_error",
            "note": "; ".join(assessment["reasons"]),
            "value": {"identity_assessment": assessment},
        }
    return {
        "website": website,
        "assessment": assessment,
        "quarantined_social_links": 0,
    }
