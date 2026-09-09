"""Artificial Analysis capture adapters.

`GET https://artificialanalysis.ai/api/v2/language/models/free` is read server side with
an `x-api-key` header that never leaves this process, and paged with the official `page`
query parameter until `has_more` is false. `page_size` is a response field, not a
documented query parameter, so it is validated on the way back but never sent. Responses
are parsed with `parse_float=Decimal`, so the published decimal literals survive into
pricing untouched.

Both sources return the received page bodies untouched, so the snapshot hash and the
preserved `rawPages` describe the bytes themselves rather than a re-serialization.

The fixture source reads the same page shape from checked-in files. It reports
`AaSourceMode.FIXTURE`, which is what stops fixture numbers from ever being stored as a
verified live AA capture.
"""

from __future__ import annotations

from pathlib import Path

import httpx

from buyer_audit_api.core.errors import ConfigurationError, ExternalEvidenceError
from buyer_audit_api.domains.ai_inference.aa_catalog import (
    MAX_PAGES,
    AaFieldPaths,
    parse_aa_page,
)
from buyer_audit_api.domains.ai_inference.aa_models import AA_SOURCE_URL, AaSourceMode


class HttpArtificialAnalysisSource:
    """The live adapter. It is only constructed when a server API key is configured."""

    def __init__(
        self,
        *,
        api_key: str,
        source_url: str = AA_SOURCE_URL,
        field_paths: AaFieldPaths | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key.strip():
            raise ConfigurationError("a live AA source requires a server API key")
        self._api_key = api_key
        self._source_url = source_url
        self._field_paths = field_paths or AaFieldPaths()
        self._client = client or httpx.AsyncClient(timeout=20)

    @property
    def mode(self) -> AaSourceMode:
        return AaSourceMode.LIVE

    @property
    def source_url(self) -> str:
        return self._source_url

    @property
    def field_paths(self) -> AaFieldPaths:
        return self._field_paths

    async def fetch_pages(self) -> tuple[str, ...]:
        pages: list[str] = []
        page = 1
        while page <= MAX_PAGES:
            response = await self._client.get(
                self._source_url,
                headers={"x-api-key": self._api_key},
                params={"page": page},
            )
            if response.status_code != httpx.codes.OK:
                # The key is never echoed into an error message or a log line.
                raise ExternalEvidenceError(
                    f"AA request for page {page} failed with HTTP {response.status_code}"
                )
            pages.append(response.text)
            payload = parse_aa_page(response.text)
            pagination = payload.get("pagination")
            if not isinstance(pagination, dict):
                raise ExternalEvidenceError("AA page has no pagination object")
            if pagination.get("has_more") is not True:
                return tuple(pages)
            page += 1
        raise ExternalEvidenceError("AA pagination exceeded the bounded page limit")


class FixtureArtificialAnalysisSource:
    """Reads captured pages from files. Never presented as a live AA verification."""

    def __init__(
        self,
        *,
        page_paths: tuple[Path, ...],
        source_url: str = AA_SOURCE_URL,
        field_paths: AaFieldPaths | None = None,
    ) -> None:
        if not page_paths:
            raise ConfigurationError("a fixture AA source requires at least one page file")
        self._page_paths = page_paths
        self._source_url = source_url
        self._field_paths = field_paths or AaFieldPaths()

    @property
    def mode(self) -> AaSourceMode:
        return AaSourceMode.FIXTURE

    @property
    def source_url(self) -> str:
        return self._source_url

    @property
    def field_paths(self) -> AaFieldPaths:
        return self._field_paths

    async def fetch_pages(self) -> tuple[str, ...]:
        pages: list[str] = []
        for path in self._page_paths:
            if not path.is_file():
                raise ConfigurationError(f"AA fixture page is missing: {path}")
            pages.append(path.read_text(encoding="utf-8"))
        return tuple(pages)
