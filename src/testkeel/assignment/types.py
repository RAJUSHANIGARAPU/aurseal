"""Assignment types: which are available now and which are planned."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

AVAILABLE = "available"
PLANNED = "planned"


@dataclass(frozen=True)
class AssignmentType:
    name: str
    status: str
    description: str
    needs: tuple[str, ...] = ()
    url_option: str | None = None  # target_url | api_base_url
    default_target: str | None = None

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["needs"] = list(self.needs)
        return data


TYPES: tuple[AssignmentType, ...] = (
    AssignmentType(
        "web-ui",
        AVAILABLE,
        "Browser UI tests with Playwright: page objects, components, offline tier",
        ("browser",),
        "target_url",
        "https://www.saucedemo.com",
    ),
    AssignmentType(
        "api-rest",
        AVAILABLE,
        "REST API tests with httpx and pydantic: client, services, schema checks",
        (),
        "api_base_url",
        "https://restful-booker.herokuapp.com",
    ),
    AssignmentType(
        "strategy",
        AVAILABLE,
        "A test plan and strategy document set, no code",
    ),
    AssignmentType("api-graphql", PLANNED, "GraphQL API tests", (), "api_base_url"),
    AssignmentType("perf-k6", PLANNED, "Load tests with k6", ("node",), "target_url"),
    AssignmentType("perf-locust", PLANNED, "Load tests with Locust", (), "target_url"),
    AssignmentType("a11y", PLANNED, "Accessibility checks with axe", ("browser",), "target_url"),
    AssignmentType("mobile", PLANNED, "Mobile app tests with Appium", ("android-sdk",)),
    AssignmentType("fullstack", PLANNED, "web-ui and api-rest combined", ("browser",)),
)
BY_NAME = {t.name: t for t in TYPES}
