"""Indeed GraphQL API scraper. Adapted from JobSpy (MIT license)."""

from __future__ import annotations

import logging
import json
from dataclasses import dataclass
from datetime import datetime

import httpx

from dev_job_radar.models import (
    COUNTRIES,
    Compensation,
    Job,
    JobType,
    Location,
    ScrapeParams,
    Site,
)
from dev_job_radar.scrapers import BaseScraper
from dev_job_radar.scrapers.constants import (
    INDEED_API_URL,
    INDEED_HEADERS,
    INDEED_SEARCH_QUERY,
)
from dev_job_radar.util import html_to_text, is_remote, parse_compensation_interval

log = logging.getLogger("dev_job_radar.scrapers.indeed")

INDEED_REQUEST_TIMEOUT_SECONDS = 10


@dataclass(frozen=True)
class IndeedMarket:
    country_code: str
    locale: str
    accept_language: str
    base_url: str


INDEED_MARKETS: dict[str, IndeedMarket] = {
    "US": IndeedMarket("US", "en-US", "en-US,en;q=0.9", "https://www.indeed.com"),
    "MA": IndeedMarket("MA", "fr-MA", "en-US,en;q=0.9", "https://ma.indeed.com"),
}


def _graphql_string(value: str) -> str:
    """Return a GraphQL string literal while preserving non-ASCII text."""
    return json.dumps(value, ensure_ascii=False)


def _country_code_from_location(location: str | None) -> str:
    if not location:
        return "US"

    parts = [part.strip().lower() for part in location.split(",") if part.strip()]
    for part in reversed(parts or [location.strip().lower()]):
        code = COUNTRIES.get(part)
        if code:
            return code
        upper = part.upper()
        if upper in INDEED_MARKETS:
            return upper
    return "US"


def _market_for_location(location: str | None) -> IndeedMarket:
    return INDEED_MARKETS.get(_country_code_from_location(location), INDEED_MARKETS["US"])


def _response_context(params: ScrapeParams) -> str:
    return f'term="{params.search_term}", location="{params.location}"'


def _safe_keys(value) -> list[str] | str:
    if isinstance(value, dict):
        return sorted(str(key) for key in value.keys())
    return type(value).__name__


def _summarize_graphql_errors(errors) -> str:
    if not isinstance(errors, list):
        return f"non-list errors payload ({type(errors).__name__})"

    summaries = []
    for error in errors[:3]:
        if not isinstance(error, dict):
            summaries.append(type(error).__name__)
            continue

        message = error.get("message", "<no message>")
        path = error.get("path")
        extensions = error.get("extensions") or {}
        code = extensions.get("code") if isinstance(extensions, dict) else None
        parts = [f"message={message!r}"]
        if path:
            parts.append(f"path={path!r}")
        if code:
            parts.append(f"code={code!r}")
        summaries.append(", ".join(parts))

    if len(errors) > 3:
        summaries.append(f"... {len(errors) - 3} more")

    return "; ".join(summaries)


class IndeedScraper(BaseScraper):
    site = Site.INDEED

    def scrape(self, params: ScrapeParams) -> list[Job]:
        jobs: list[Job] = []
        cursor = None
        seen_urls: set[str] = set()
        pages = 0

        with self._make_client() as client:
            while len(jobs) < params.results_wanted and pages < self.config.max_pages:
                log.info(f"Indeed search page, {len(jobs)} jobs so far")
                page_jobs, cursor = self._scrape_page(client, params, cursor, seen_urls)
                pages += 1
                if not page_jobs:
                    break
                jobs.extend(page_jobs)
                if not cursor:
                    break

        return jobs[: params.results_wanted]

    def _scrape_page(
        self,
        client,
        params: ScrapeParams,
        cursor: str | None,
        seen_urls: set,
    ) -> tuple[list[Job], str | None]:
        search_term = params.search_term or ""
        market = _market_for_location(params.location)

        query = INDEED_SEARCH_QUERY.format(
            what=f"what: {_graphql_string(search_term)}" if search_term else "",
            location=(
                f"location: {{where: {_graphql_string(params.location)}, radius: {params.distance_miles}, radiusUnit: MILES}}"
                if params.location
                else ""
            ),
            cursor=f'cursor: "{cursor}"' if cursor else "",
            filters="",
        )

        headers = INDEED_HEADERS.copy()
        headers["indeed-co"] = market.country_code
        headers["indeed-locale"] = market.locale
        headers["accept-language"] = market.accept_language

        resp = self._post_graphql(
            client,
            query=query,
            headers=headers,
            params=params,
        )
        if resp is None or not resp.is_success:
            log.warning(f"Indeed API returned {resp.status_code if resp else 'None'}")
            return [], None

        context = _response_context(params)
        try:
            data = resp.json()
        except ValueError as e:
            body_preview = resp.text[:200].replace("\n", "\\n")
            log.error(
                "Indeed response JSON parse error: "
                f"{context}, status={resp.status_code}, "
                f"content_type={resp.headers.get('content-type')!r}, "
                f"body_preview={body_preview!r}: {e}"
            )
            return [], None

        errors = data.get("errors") if isinstance(data, dict) else None
        if errors:
            log.error(
                f"Indeed GraphQL errors: {context}, "
                f"errors={_summarize_graphql_errors(errors)}"
            )
            return [], None

        if not isinstance(data, dict):
            log.error(
                "Indeed response structure unexpected: "
                f"{context}, expected object at response root, got {type(data).__name__}"
            )
            return [], None

        data_node = data.get("data")
        if not isinstance(data_node, dict):
            log.error(
                "Indeed response structure unexpected: "
                f"{context}, expected object at data, "
                f"root_keys={_safe_keys(data)}, data_type={type(data_node).__name__}"
            )
            return [], None

        job_search = data_node.get("jobSearch")
        if not isinstance(job_search, dict):
            log.error(
                "Indeed response structure unexpected: "
                f"{context}, expected object at data.jobSearch, "
                f"root_keys={_safe_keys(data)}, data_keys={_safe_keys(data_node)}"
            )
            return [], None

        results = job_search.get("results")
        if not isinstance(results, list):
            log.error(
                "Indeed response structure unexpected: "
                f"{context}, expected list at data.jobSearch.results, "
                f"jobSearch_keys={_safe_keys(job_search)}, "
                f"results_type={type(results).__name__}"
            )
            return [], None

        page_info = job_search.get("pageInfo")
        if not isinstance(page_info, dict):
            log.error(
                "Indeed response structure unexpected: "
                f"{context}, expected object at data.jobSearch.pageInfo, "
                f"jobSearch_keys={_safe_keys(job_search)}, "
                f"pageInfo_type={type(page_info).__name__}"
            )
            return [], None

        next_cursor = page_info.get("nextCursor")

        jobs = []
        for result in results:
            job_data = result.get("job") or result
            job = self._parse_job(job_data, seen_urls)
            if job:
                jobs.append(job)

        return jobs, next_cursor

    def _post_graphql(self, client, query: str, headers: dict, params: ScrapeParams):
        try:
            return client.post(
                INDEED_API_URL,
                json={"query": query},
                headers=headers,
                timeout=INDEED_REQUEST_TIMEOUT_SECONDS,
            )
        except httpx.TimeoutException:
            log.error(
                f'Indeed request timed out: term="{params.search_term}", location="{params.location}"'
            )
            return None
        except httpx.HTTPError as e:
            log.error(
                f'Indeed request failed: term="{params.search_term}", location="{params.location}": {e}'
            )
            return None

    def _parse_job(self, job: dict, seen_urls: set) -> Job | None:
        key = job.get("key", "")
        market = _market_for_location((job.get("location") or {}).get("countryCode"))
        job_url = f"{market.base_url}/viewjob?jk={key}"
        if job_url in seen_urls:
            return None
        seen_urls.add(job_url)

        # Description
        desc_html = (job.get("description") or {}).get("html", "")
        description = html_to_text(desc_html)

        # Location
        loc = job.get("location") or {}
        location = Location(
            city=loc.get("city"),
            state=loc.get("admin1Code"),
            country=loc.get("countryCode", "US"),
        )

        # Compensation
        compensation = self._parse_compensation(job.get("compensation") or {})

        # Job type from attributes
        job_types = self._parse_job_types(job.get("attributes") or [])

        # Date posted
        date_posted = None
        ts = job.get("datePublished")
        if ts:
            try:
                date_posted = datetime.fromtimestamp(ts / 1000).date()
            except (ValueError, OSError):
                pass

        # Remote check
        remote = is_remote(
            job.get("title", ""),
            description,
            (loc.get("formatted") or {}).get("long", ""),
        )
        location.is_remote = remote

        # Company
        employer = job.get("employer") or {}
        company = employer.get("name") or "Unknown"

        return Job(
            source=Site.INDEED,
            source_id=key,
            url=job_url,
            title=job.get("title", ""),
            company=company,
            location=location,
            description=description,
            compensation=compensation,
            job_type=job_types,
            date_posted=date_posted,
        )

    @staticmethod
    def _parse_compensation(comp: dict) -> Compensation | None:
        base = comp.get("baseSalary")
        if not base:
            estimated = comp.get("estimated") or {}
            base = estimated.get("baseSalary")
        if not base:
            return None

        interval = parse_compensation_interval(base.get("unitOfWork", "YEAR"))
        if not interval:
            return None

        salary_range = base.get("range") or {}
        min_amt = salary_range.get("min")
        max_amt = salary_range.get("max")
        if min_amt is None and max_amt is None:
            return None

        currency = comp.get("currencyCode") or "USD"
        if not currency:
            estimated = comp.get("estimated") or {}
            currency = estimated.get("currencyCode", "USD")

        return Compensation(
            min_amount=int(min_amt) if min_amt is not None else None,
            max_amount=int(max_amt) if max_amt is not None else None,
            currency=currency,
            interval=interval,
        )

    @staticmethod
    def _parse_job_types(attributes: list) -> list[JobType]:
        type_map = {
            "fulltime": JobType.FULL_TIME,
            "parttime": JobType.PART_TIME,
            "contract": JobType.CONTRACT,
            "internship": JobType.INTERNSHIP,
            "temporary": JobType.TEMPORARY,
        }
        types = []
        for attr in attributes:
            label = attr.get("label", "").replace("-", "").replace(" ", "").lower()
            jt = type_map.get(label)
            if jt:
                types.append(jt)
        return types
