from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


class CrawlPageEvent(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)

    organisation_number: str
    requested_url: str
    final_url: str
    status_code: int = Field(ge=0, le=599)
    content_type: str
    page_kind: str
    retrieved_at: str
    bytes: int = Field(ge=0)
    content_sha256: str
    status: Literal["available", "not_found", "source_error"]

    @field_validator("organisation_number")
    @classmethod
    def validate_organisation_number(cls, value: str) -> str:
        if len(value) != 9 or not value.isdigit():
            raise ValueError("organisation_number must contain exactly 9 digits")
        return value

    @field_validator("requested_url", "final_url")
    @classmethod
    def validate_http_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("URL must be an absolute HTTP(S) URL")
        return value


class WebsitePageOutput(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)

    url: str
    final_url: str
    title: str
    status: int | Literal["not_available"]
    duration_seconds: float | int | Literal["not_available"]
    page_kind: str
    extraction_state: str
    text_excerpt: str
    errors: list[Any]

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("URL must be an absolute HTTP(S) URL")
        return value

    @field_validator("final_url")
    @classmethod
    def validate_final_url(cls, value: str) -> str:
        if value == "not_available":
            return value
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("URL must be an absolute HTTP(S) URL")
        return value

    @field_validator("duration_seconds")
    @classmethod
    def validate_duration(cls, value: float | int | str) -> float | int | str:
        if value != "not_available" and value < 0:
            raise ValueError("duration_seconds cannot be negative")
        return value


class WebsiteDiscovery(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)

    method: str
    providers_used: list[Any]
    candidates: list[Any]
    cost_usd: float


class WebsiteValue(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)

    requested_url: str
    final_url: str
    registered_domain: str
    title: str
    description: str
    main_text_excerpt: str
    structured_organisations: list[dict[str, Any]]
    pages: list[WebsitePageOutput]
    identity_assessment: dict[str, Any]
    org_number_found: bool | Literal["not_available"]
    legal_name_match: bool | Literal["not_available"]
    address_match: bool | Literal["not_available"]
    extraction_state: str
    content_sha256: str
    crawl_errors: list[Any]
    discovery: WebsiteDiscovery
    cost_usd: float
    jobs: list[Any] | Literal["not_available"]
    news: list[Any] | Literal["not_available"]
    phones: list[Any] | Literal["not_available"]
    emails: list[Any] | Literal["not_available"]
    addresses: list[Any] | Literal["not_available"]
    locations: list[Any] | Literal["not_available"]

    @field_validator("requested_url", "final_url")
    @classmethod
    def validate_http_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("URL must be an absolute HTTP(S) URL")
        return value


_quarantine_lock = threading.Lock()


def validation_reason_code(error: ValidationError) -> str:
    issues = error.errors(include_url=False)
    fields = {str(part) for issue in issues for part in issue.get("loc", ())}
    if any(issue.get("type") == "missing" for issue in issues):
        return "missing_required_field"
    if "organisation_number" in fields:
        return "invalid_org_number"
    if fields & {"requested_url", "final_url", "url"}:
        return "invalid_url"
    if "status" in fields or "status_code" in fields:
        return "invalid_status"
    return "schema_validation_error"


def validation_error_details(error: ValidationError) -> list[dict[str, Any]]:
    return [
        {"field": ".".join(str(part) for part in issue.get("loc", ())), "type": issue.get("type"), "message": issue.get("msg")}
        for issue in error.errors(include_url=False)
    ]


def append_quarantine_record(
    *,
    path: str | Path | None,
    record_type: str,
    record: Any,
    reason_code: str,
    errors: list[dict[str, Any]],
) -> None:
    if path is None:
        configured_path = os.environ.get("SIGNALPOST_QUARANTINE_PATH")
        path = configured_path or str(Path(os.environ.get("SIGNALPOST_OUTPUT_DIR", "out/latest-run")) / "validation-quarantine.jsonl")
    quarantine_path = Path(path)
    quarantine_path.parent.mkdir(parents=True, exist_ok=True)
    item = {
        "record_type": record_type,
        "reason_code": reason_code,
        "errors": errors,
        "record": record,
    }
    line = json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n"
    with _quarantine_lock:
        with quarantine_path.open("a", encoding="utf-8") as handle:
            handle.write(line)


def validate_crawl_page_event(record: Any) -> dict[str, Any]:
    return CrawlPageEvent.model_validate(record).model_dump(mode="python")


def validate_website_value(record: Any) -> dict[str, Any]:
    return WebsiteValue.model_validate(record).model_dump(mode="python")