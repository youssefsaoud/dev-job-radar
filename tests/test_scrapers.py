"""Tests for scraper implementations with mocked HTTP responses."""

from __future__ import annotations


import httpx
import json
import logging
import pytest
import respx

from dev_job_radar.config import ScrapingConfig
from dev_job_radar.models import ScrapeParams, Site


@pytest.fixture
def scraping_config():
    return ScrapingConfig(
        delay_min_seconds=0,
        delay_max_seconds=0,
        max_retries=0,
        max_pages=2,
    )


@pytest.fixture
def params():
    return ScrapeParams(
        search_term="software engineer",
        location="United States",
        results_wanted=10,
        hours_old=72,
    )


class TestLinkedInScraper:
    def test_parse_empty_response(self, scraping_config, params):
        from dev_job_radar.scrapers.linkedin import LinkedInScraper

        scraper = LinkedInScraper(scraping_config)
        with respx.mock:
            respx.get(
                "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
            ).mock(return_value=httpx.Response(200, text="<html></html>"))
            # Also mock description fetches
            respx.get("https://www.linkedin.com/jobs/view/").mock(
                return_value=httpx.Response(200, text="")
            )
            jobs = scraper.scrape(params)
            assert jobs == []

    def test_parse_cards(self, scraping_config, params):
        from dev_job_radar.scrapers.linkedin import LinkedInScraper

        html = """
        <html>
        <div class="base-search-card">
            <a class="base-card__full-link" href="https://linkedin.com/jobs/view/test-job-12345?refId=abc">Link</a>
            <span class="sr-only">Software Engineer</span>
            <h4 class="base-search-card__subtitle"><a>Acme Corp</a></h4>
            <div class="base-search-card__metadata">
                <span class="job-search-card__location">San Francisco, CA</span>
                <time class="job-search-card__listdate" datetime="2026-04-01"></time>
            </div>
        </div>
        </html>
        """
        scraper = LinkedInScraper(scraping_config)
        with respx.mock:
            respx.get(
                "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
            ).mock(return_value=httpx.Response(200, text=html))
            respx.get(url__startswith="https://www.linkedin.com/jobs/view/").mock(
                return_value=httpx.Response(
                    200,
                    text="<div class='show-more-less-html__markup'>Description</div>",
                )
            )
            jobs = scraper.scrape(params)
            assert len(jobs) == 1
            assert jobs[0].title == "Software Engineer"
            assert jobs[0].company == "Acme Corp"
            assert jobs[0].source == Site.LINKEDIN

    def test_preserves_unicode_search_term_in_request(self, scraping_config):
        from dev_job_radar.scrapers.linkedin import LinkedInScraper

        params = ScrapeParams(
            search_term="Développeur java",
            location="Morocco",
            results_wanted=1,
        )
        scraper = LinkedInScraper(scraping_config)

        def handler(request):
            assert request.url.params["keywords"] == "Développeur java"
            assert request.url.params["location"] == "Morocco"
            return httpx.Response(200, text="<html></html>")

        with respx.mock:
            respx.get(
                "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
            ).mock(side_effect=handler)
            assert scraper.scrape(params) == []


class TestIndeedScraper:
    def test_parse_empty_response(self, scraping_config, params):
        from dev_job_radar.scrapers.indeed import IndeedScraper

        scraper = IndeedScraper(scraping_config)
        with respx.mock:
            respx.post("https://apis.indeed.com/graphql").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "data": {
                            "jobSearch": {
                                "results": [],
                                "pageInfo": {"nextCursor": None},
                            }
                        }
                    },
                )
            )
            jobs = scraper.scrape(params)
            assert jobs == []

    def test_parse_jobs(self, scraping_config, params):
        from dev_job_radar.scrapers.indeed import IndeedScraper

        scraper = IndeedScraper(scraping_config)
        with respx.mock:
            respx.post("https://apis.indeed.com/graphql").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "data": {
                            "jobSearch": {
                                "results": [
                                    {
                                        "job": {
                                            "key": "abc123",
                                            "title": "Backend Engineer",
                                            "description": {"html": "<p>Great job</p>"},
                                            "location": {
                                                "city": "NYC",
                                                "admin1Code": "NY",
                                                "countryCode": "US",
                                                "formatted": {
                                                    "short": "NYC",
                                                    "long": "NYC, NY",
                                                },
                                            },
                                            "compensation": {},
                                            "attributes": [],
                                            "employer": {"name": "TechCo"},
                                            "datePublished": 1711929600000,
                                        }
                                    }
                                ],
                                "pageInfo": {"nextCursor": None},
                            }
                        }
                    },
                )
            )
            jobs = scraper.scrape(params)
            assert len(jobs) == 1
            assert jobs[0].title == "Backend Engineer"
            assert jobs[0].company == "TechCo"
            assert jobs[0].source == Site.INDEED

    def test_duplicate_jobs_in_one_response_are_deduped(self, scraping_config, params):
        from dev_job_radar.scrapers.indeed import IndeedScraper

        duplicate_job = {
            "job": {
                "key": "dup123",
                "title": "Backend Engineer",
                "description": {"html": "<p>Great job</p>"},
                "location": {
                    "city": "NYC",
                    "admin1Code": "NY",
                    "countryCode": "US",
                    "formatted": {
                        "short": "NYC",
                        "long": "NYC, NY",
                    },
                },
                "compensation": {},
                "attributes": [],
                "employer": {"name": "TechCo"},
                "datePublished": 1711929600000,
            }
        }
        scraper = IndeedScraper(scraping_config)
        with respx.mock:
            respx.post("https://apis.indeed.com/graphql").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "data": {
                            "jobSearch": {
                                "results": [duplicate_job, duplicate_job],
                                "pageInfo": {"nextCursor": None},
                            }
                        }
                    },
                )
            )
            jobs = scraper.scrape(params)

        assert len(jobs) == 1
        assert jobs[0].source_id == "dup123"

    def test_morocco_search_uses_morocco_market_and_preserves_unicode(
        self, scraping_config
    ):
        from dev_job_radar.scrapers.indeed import IndeedScraper

        params = ScrapeParams(
            search_term="Développeur java",
            location="Morocco",
            results_wanted=10,
            hours_old=24,
        )
        scraper = IndeedScraper(scraping_config)

        def handler(request):
            payload = json.loads(request.read().decode("utf-8"))
            query = payload["query"]
            assert request.headers["indeed-co"] == "MA"
            assert request.headers["indeed-locale"] == "fr-MA"
            assert request.headers["accept-language"] == "en-US,en;q=0.9"
            assert "Développeur java" in query
            assert 'where: "Morocco"' in query
            assert "radiusUnit: MILES" in query
            assert "dateOnIndeed" not in query
            assert "filters:" not in query
            return httpx.Response(
                200,
                json={
                    "data": {
                        "jobSearch": {
                            "results": [
                                {
                                    "job": {
                                        "key": "ma123",
                                        "title": "Développeur java",
                                        "description": {"html": "<p>Java</p>"},
                                        "location": {
                                            "city": "Casablanca",
                                            "admin1Code": None,
                                            "countryCode": "MA",
                                            "formatted": {
                                                "short": "Casablanca",
                                                "long": "Casablanca, Morocco",
                                            },
                                        },
                                        "compensation": {},
                                        "attributes": [],
                                        "employer": {"name": "Tech Maroc"},
                                        "datePublished": 1711929600000,
                                    }
                                }
                            ],
                            "pageInfo": {"nextCursor": None},
                        }
                    }
                },
            )

        with respx.mock:
            respx.post("https://apis.indeed.com/graphql").mock(side_effect=handler)
            jobs = scraper.scrape(params)

        assert len(jobs) == 1
        assert jobs[0].title == "Développeur java"
        assert jobs[0].url == "https://ma.indeed.com/viewjob?jk=ma123"

    def test_indeed_omits_date_filter_regardless_of_hours_old(
        self, scraping_config
    ):
        from dev_job_radar.scrapers.indeed import IndeedScraper

        params = ScrapeParams(
            search_term="java developer",
            location="Morocco",
            results_wanted=10,
            hours_old=168,
        )
        scraper = IndeedScraper(scraping_config)

        def handler(request):
            payload = json.loads(request.read().decode("utf-8"))
            query = payload["query"]
            assert "dateOnIndeed" not in query
            assert "filters:" not in query
            return httpx.Response(
                200,
                json={
                    "data": {
                        "jobSearch": {
                            "results": [],
                            "pageInfo": {"nextCursor": None},
                        }
                    }
                },
            )

        with respx.mock:
            respx.post("https://apis.indeed.com/graphql").mock(side_effect=handler)
            assert scraper.scrape(params) == []

    def test_indeed_post_uses_10_second_timeout_without_shared_retry(
        self, scraping_config, monkeypatch
    ):
        from dev_job_radar.scrapers.indeed import IndeedScraper

        params = ScrapeParams(
            search_term="java developer",
            location="Morocco",
            results_wanted=10,
            hours_old=24,
        )
        scraper = IndeedScraper(scraping_config)

        def fail_if_shared_retry_used(*args, **kwargs):
            raise AssertionError("Indeed should not use shared retry helper")

        monkeypatch.setattr(scraper, "_post_with_retry", fail_if_shared_retry_used)

        def handler(request):
            assert request.extensions["timeout"] == {
                "connect": 10,
                "read": 10,
                "write": 10,
                "pool": 10,
            }
            return httpx.Response(
                200,
                json={
                    "data": {
                        "jobSearch": {
                            "results": [],
                            "pageInfo": {"nextCursor": None},
                        }
                    }
                },
            )

        with respx.mock:
            route = respx.post("https://apis.indeed.com/graphql").mock(
                side_effect=handler
            )
            assert scraper.scrape(params) == []

        assert route.call_count == 1

    def test_indeed_timeout_logs_term_and_location_without_retry(
        self, scraping_config, caplog
    ):
        from dev_job_radar.scrapers.indeed import IndeedScraper

        params = ScrapeParams(
            search_term="java developer",
            location="Morocco",
            results_wanted=10,
            hours_old=24,
        )
        scraper = IndeedScraper(scraping_config)

        def raise_timeout(request):
            raise httpx.ReadTimeout("timed out", request=request)

        with respx.mock, caplog.at_level(
            logging.ERROR, logger="dev_job_radar.scrapers.indeed"
        ):
            route = respx.post("https://apis.indeed.com/graphql").mock(
                side_effect=raise_timeout
            )
            assert scraper.scrape(params) == []

        assert route.call_count == 1
        assert (
            'Indeed request timed out: term="java developer", location="Morocco"'
            in caplog.text
        )

    def test_indeed_graphql_errors_are_logged(self, scraping_config, caplog):
        from dev_job_radar.scrapers.indeed import IndeedScraper

        params = ScrapeParams(
            search_term="java developer",
            location="Morocco",
            results_wanted=10,
            hours_old=24,
        )
        scraper = IndeedScraper(scraping_config)

        with respx.mock, caplog.at_level(
            logging.ERROR, logger="dev_job_radar.scrapers.indeed"
        ):
            respx.post("https://apis.indeed.com/graphql").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "errors": [
                            {
                                "message": "Invalid country",
                                "path": ["jobSearch"],
                                "extensions": {"code": "BAD_USER_INPUT"},
                            }
                        ],
                        "data": None,
                    },
                )
            )
            assert scraper.scrape(params) == []

        assert "Indeed GraphQL errors" in caplog.text
        assert 'term="java developer", location="Morocco"' in caplog.text
        assert "Invalid country" in caplog.text
        assert "BAD_USER_INPUT" in caplog.text

    def test_indeed_empty_results_are_not_logged_as_diagnostics(
        self, scraping_config, caplog
    ):
        from dev_job_radar.scrapers.indeed import IndeedScraper

        params = ScrapeParams(
            search_term="java developer",
            location="Morocco",
            results_wanted=10,
            hours_old=24,
        )
        scraper = IndeedScraper(scraping_config)

        with respx.mock, caplog.at_level(
            logging.INFO, logger="dev_job_radar.scrapers.indeed"
        ):
            respx.post("https://apis.indeed.com/graphql").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "data": {
                            "jobSearch": {
                                "results": [],
                                "pageInfo": {"nextCursor": None},
                            }
                        }
                    },
                )
            )
            assert scraper.scrape(params) == []

        assert "Indeed response contained 0 results" not in caplog.text
        assert "Indeed response parsed" not in caplog.text

    def test_indeed_unexpected_structure_is_logged(self, scraping_config, caplog):
        from dev_job_radar.scrapers.indeed import IndeedScraper

        params = ScrapeParams(
            search_term="java developer",
            location="Morocco",
            results_wanted=10,
            hours_old=24,
        )
        scraper = IndeedScraper(scraping_config)

        with respx.mock, caplog.at_level(
            logging.ERROR, logger="dev_job_radar.scrapers.indeed"
        ):
            respx.post("https://apis.indeed.com/graphql").mock(
                return_value=httpx.Response(
                    200,
                    json={"data": {"jobSearch": {"edges": []}}},
                )
            )
            assert scraper.scrape(params) == []

        assert "Indeed response structure unexpected" in caplog.text
        assert "data.jobSearch.results" in caplog.text
        assert "jobSearch_keys=['edges']" in caplog.text


class TestGlassdoorScraper:
    def test_parse_empty_response(self, scraping_config, params):
        from dev_job_radar.scrapers.glassdoor import GlassdoorScraper

        scraper = GlassdoorScraper(scraping_config)
        with respx.mock:
            respx.get("https://www.glassdoor.com").mock(
                return_value=httpx.Response(200, text="<html></html>")
            )
            respx.post("https://www.glassdoor.com/graph").mock(
                return_value=httpx.Response(
                    200,
                    json=[
                        {
                            "data": {
                                "jobListings": {
                                    "jobListings": [],
                                    "totalJobsCount": 0,
                                    "paginationCursors": [],
                                }
                            }
                        }
                    ],
                )
            )
            jobs = scraper.scrape(params)
            assert jobs == []

    def test_parse_listing(self, scraping_config, params):
        from dev_job_radar.scrapers.glassdoor import GlassdoorScraper

        scraper = GlassdoorScraper(scraping_config)
        with respx.mock:
            respx.get("https://www.glassdoor.com").mock(
                return_value=httpx.Response(200, text="<html></html>")
            )
            respx.post("https://www.glassdoor.com/graph").mock(
                return_value=httpx.Response(
                    200,
                    json=[
                        {
                            "data": {
                                "jobListings": {
                                    "jobListings": [
                                        {
                                            "jobview": {
                                                "header": {
                                                    "jobLink": "/job/123",
                                                    "jobTitleText": "Data Scientist",
                                                    "employerNameFromSearch": "DataCo",
                                                    "ageInDays": 2,
                                                    "payPercentile10": 80000,
                                                    "payPercentile90": 120000,
                                                    "payCurrency": "USD",
                                                    "payPeriod": "ANNUAL",
                                                },
                                                "job": {
                                                    "listingId": "gd-999",
                                                    "description": "ML role",
                                                },
                                                "overview": {"name": "DataCo"},
                                                "locationName": "Boston, MA",
                                                "remoteWorkTypes": [],
                                            }
                                        }
                                    ],
                                    "totalJobsCount": 1,
                                    "paginationCursors": [],
                                }
                            }
                        }
                    ],
                )
            )
            jobs = scraper.scrape(params)
            assert len(jobs) == 1
            assert jobs[0].title == "Data Scientist"
            assert jobs[0].source == Site.GLASSDOOR


class TestZipRecruiterScraper:
    def test_parse_empty_response(self, scraping_config, params):
        from dev_job_radar.scrapers.ziprecruiter import ZipRecruiterScraper

        scraper = ZipRecruiterScraper(scraping_config)
        with respx.mock:
            respx.get("https://api.ziprecruiter.com/jobs-app/jobs").mock(
                return_value=httpx.Response(
                    200, json={"jobs": [], "continue_from": None}
                )
            )
            jobs = scraper.scrape(params)
            assert jobs == []

    def test_parse_jobs(self, scraping_config, params):
        from dev_job_radar.scrapers.ziprecruiter import ZipRecruiterScraper

        scraper = ZipRecruiterScraper(scraping_config)
        with respx.mock:
            respx.get("https://api.ziprecruiter.com/jobs-app/jobs").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "jobs": [
                            {
                                "id": "zr-001",
                                "name": "Frontend Dev",
                                "url": "https://ziprecruiter.com/j/zr-001",
                                "hiring_company": {"name": "WebCo"},
                                "job_city": "Austin",
                                "job_state": "TX",
                                "job_country": "US",
                                "snippet": "<b>React</b> developer needed",
                                "posted_time_friendly": "2 days ago",
                                "salary_min_annual": 90000,
                                "salary_max_annual": 130000,
                            }
                        ],
                        "continue_from": None,
                    },
                )
            )
            jobs = scraper.scrape(params)
            assert len(jobs) == 1
            assert jobs[0].title == "Frontend Dev"
            assert jobs[0].source == Site.ZIPRECRUITER
            assert jobs[0].compensation is not None


class TestBaytScraper:
    def test_parse_empty_response(self, scraping_config, params):
        from dev_job_radar.scrapers.bayt import BaytScraper

        scraper = BaytScraper(scraping_config)
        with respx.mock:
            respx.get(
                url__startswith="https://www.bayt.com/en/international/jobs/"
            ).mock(return_value=httpx.Response(200, text="<html><body></body></html>"))
            jobs = scraper.scrape(params)
            assert jobs == []

    def test_parse_cards(self, scraping_config, params):
        from dev_job_radar.scrapers.bayt import BaytScraper

        html = """
        <html><body>
        <li data-js-job="1" data-job-id="bt-100">
            <h2><a href="/en/job/bt-100">DevOps Engineer</a></h2>
            <b class="company-name">CloudFirm</b>
            <span class="location-text">Dubai, UAE</span>
            <span class="date-posted">3 days ago</span>
        </li>
        </body></html>
        """
        scraper = BaytScraper(scraping_config)
        with respx.mock:
            respx.get(
                url__startswith="https://www.bayt.com/en/international/jobs/"
            ).mock(return_value=httpx.Response(200, text=html))
            jobs = scraper.scrape(params)
            assert len(jobs) == 1
            assert jobs[0].title == "DevOps Engineer"
            assert jobs[0].source == Site.BAYT


class TestScraperRegistry:
    def test_all_sites_registered(self):
        from dev_job_radar.scrapers import get_scraper

        cfg = ScrapingConfig()
        for site in [
            "linkedin",
            "indeed",
            "google",
            "glassdoor",
            "ziprecruiter",
            "bayt",
        ]:
            scraper = get_scraper(site, cfg)
            assert scraper is not None

    def test_unknown_site_raises(self):
        from dev_job_radar.scrapers import get_scraper

        with pytest.raises(ValueError, match="Unknown site"):
            get_scraper("fakeboard", ScrapingConfig())
