"""Assignment types: which are available now and which are planned."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

AVAILABLE = "available"
PLANNED = "planned"

#: The URL answers a type can take, keyed by answer name, with the CLI flag that sets each.
URL_FLAGS = {"target_url": "--target-url", "api_base_url": "--api-base-url"}


@dataclass(frozen=True)
class AssignmentType:
    name: str
    status: str
    description: str
    needs: tuple[str, ...] = ()
    #: ``(answer, default)`` for each URL the type renders: ``target_url`` / ``api_base_url``.
    #: A default of ``None`` makes the option required.
    urls: tuple[tuple[str, str | None], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["needs"] = list(self.needs)
        data["urls"] = dict(self.urls)
        return data


SAUCEDEMO = "https://www.saucedemo.com"
RESTFUL_BOOKER = "https://restful-booker.herokuapp.com"
QUICKPIZZA = "https://quickpizza.grafana.com"

TYPES: tuple[AssignmentType, ...] = (
    AssignmentType(
        "web-ui",
        AVAILABLE,
        "Browser UI tests with Playwright: page objects, components, offline tier",
        ("browser",),
        (("target_url", SAUCEDEMO),),
    ),
    AssignmentType(
        "api-rest",
        AVAILABLE,
        "REST API tests with httpx and pydantic: client, services, schema checks",
        (),
        (("api_base_url", RESTFUL_BOOKER),),
    ),
    AssignmentType(
        "api-graphql",
        AVAILABLE,
        "GraphQL API tests with gql: operations, schema snapshot, offline tier",
        (),
        (("api_base_url", "https://countries.trevorblades.com"),),
    ),
    AssignmentType(
        "a11y",
        AVAILABLE,
        "Accessibility checks with axe and Playwright: baseline, planted-violation controls",
        ("browser",),
        (("target_url", "https://www.w3.org/WAI/demos/bad"),),
    ),
    AssignmentType(
        "strategy",
        AVAILABLE,
        "A test plan and strategy document set, no code",
    ),
    AssignmentType(
        "perf-k6",
        AVAILABLE,
        "Load tests with k6: smoke, load and stress profiles, SLO thresholds, target guard",
        ("k6",),
        (("target_url", QUICKPIZZA),),
    ),
    AssignmentType(
        "perf-locust",
        AVAILABLE,
        "Load tests with Locust: user classes, load shapes, SLO checks, target guard",
        (),
        (("target_url", QUICKPIZZA),),
    ),
    AssignmentType(
        "mobile",
        AVAILABLE,
        "Android app tests with Appium (UiAutomator2): screen objects, offline tier",
        ("android-sdk",),
    ),
    AssignmentType(
        "fullstack",
        AVAILABLE,
        "web-ui and api-rest in one project: UI tests seeded through the API",
        ("browser",),
        # No public demo serves a UI and its API together, so both URLs are required.
        (("target_url", None), ("api_base_url", None)),
    ),
)
BY_NAME = {t.name: t for t in TYPES}
