"""IngestionAgent: parse Postman Collection v2.1 JSON into PostmanRequest dataclasses."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class PostmanRequest:
    """Flattened representation of one Postman request item."""

    name: str
    method: str
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    query_params: dict[str, str] = field(default_factory=dict)
    body: dict | str | None = None
    body_mode: str = "none"
    folder_path: list[str] = field(default_factory=list)
    pre_request_script: str = ""
    test_script: str = ""


class IngestionAgent:
    """Parses a Postman Collection v2.1 JSON file into PostmanRequest objects.

    Handles nested folders recursively. No LLM needed.
    """

    def parse_file(self, path: str | Path) -> list[PostmanRequest]:
        """Load a Postman collection from a file path."""
        collection_path = Path(path)
        logger.info("Parsing Postman collection: %s", collection_path)
        with open(collection_path, encoding="utf-8") as fh:
            raw = json.load(fh)
        return self.parse(raw)

    def parse(self, collection: dict) -> list[PostmanRequest]:
        """Parse a Postman collection dict into a flat list of PostmanRequest objects."""
        items = collection.get("item", [])
        requests_list: list[PostmanRequest] = []
        self._traverse(items, folder_path=[], result=requests_list)
        logger.info("Ingested %d requests from collection", len(requests_list))
        return requests_list

    def _traverse(
        self,
        items: list[dict],
        folder_path: list[str],
        result: list[PostmanRequest],
    ) -> None:
        for item in items:
            if "item" in item:
                # This is a folder — recurse
                folder_name = item.get("name", "unknown")
                self._traverse(item["item"], folder_path + [folder_name], result)
            elif "request" in item:
                try:
                    req = self._parse_item(item, folder_path)
                    result.append(req)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Skipping malformed item '%s': %s", item.get("name"), exc)

    def _parse_item(self, item: dict, folder_path: list[str]) -> PostmanRequest:
        name = item.get("name", "unnamed")
        raw = item.get("request", {})

        method = raw.get("method", "GET").upper()
        url = self._extract_url(raw.get("url", ""))
        headers = self._extract_headers(raw.get("header", []))
        query_params = self._extract_query_params(raw.get("url", {}))
        body, body_mode = self._extract_body(raw.get("body", {}))

        events: dict[str, str] = {}
        for event in item.get("event", []):
            listen = event.get("listen", "")
            script_lines = event.get("script", {}).get("exec", [])
            events[listen] = "\n".join(script_lines)

        return PostmanRequest(
            name=name,
            method=method,
            url=url,
            headers=headers,
            query_params=query_params,
            body=body,
            body_mode=body_mode,
            folder_path=list(folder_path),
            pre_request_script=events.get("prerequest", ""),
            test_script=events.get("test", ""),
        )

    @staticmethod
    def _extract_url(url_field: Any) -> str:
        if isinstance(url_field, str):
            return url_field
        if isinstance(url_field, dict):
            raw_url = url_field.get("raw", "")
            if raw_url:
                return raw_url
            protocol = url_field.get("protocol", "https")
            host = ".".join(url_field.get("host", []))
            path = "/".join(url_field.get("path", []))
            return f"{protocol}://{host}/{path}"
        return ""

    @staticmethod
    def _extract_headers(header_list: list[dict]) -> dict[str, str]:
        result: dict[str, str] = {}
        for h in header_list:
            if not h.get("disabled", False):
                key = h.get("key", "")
                value = h.get("value", "")
                if key:
                    result[key] = value
        return result

    @staticmethod
    def _extract_query_params(url_field: Any) -> dict[str, str]:
        if not isinstance(url_field, dict):
            return {}
        result: dict[str, str] = {}
        for q in url_field.get("query", []):
            if not q.get("disabled", False):
                key = q.get("key", "")
                value = q.get("value", "")
                if key:
                    result[key] = value
        return result

    @staticmethod
    def _extract_body(body_field: Any) -> tuple[dict | str | None, str]:
        if not body_field:
            return None, "none"
        mode = body_field.get("mode", "none")
        if mode == "raw":
            raw_content = body_field.get("raw", "")
            try:
                return json.loads(raw_content), "raw_json"
            except (json.JSONDecodeError, TypeError):
                return raw_content, "raw"
        if mode == "urlencoded":
            data = {
                item["key"]: item.get("value", "")
                for item in body_field.get("urlencoded", [])
                if not item.get("disabled", False)
            }
            return data, "urlencoded"
        if mode == "formdata":
            data = {
                item["key"]: item.get("value", "")
                for item in body_field.get("formdata", [])
                if not item.get("disabled", False)
            }
            return data, "formdata"
        return None, mode
