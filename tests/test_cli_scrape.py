"""Tests for zero-result warnings in the scrape command."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from dev_job_radar.cli import app
from dev_job_radar.config import AppConfig
from dev_job_radar.models import Job, Location, Site

runner = CliRunner()

MINIMAL_RAW = {
    "profile": {
        "name": "Test",
        "target_title": "Software Engineer",
        "keywords": {
            "critical": ["python"],
            "strong": ["backend"],
            "moderate": [],
            "weak": [],
        },
        "target_companies": {"tier1": [], "tier2": [], "tier3": []},
        "title_signals": [],
        "dealbreakers": {
            "title_patterns": [],
            "company_patterns": [],
            "description_patterns": [],
        },
    },
    "search": {
        "terms": ["python"],
        "locations": ["Remote"],
        "sites": ["linkedin"],
    },
}


def _make_job(source_id: str, *, title: str = "Software Engineer") -> Job:
    return Job(
        source=Site.INDEED,
        source_id=source_id,
        url=f"https://ma.indeed.com/viewjob?jk={source_id}",
        title=title,
        company="TestCo",
        location=Location(city="Casablanca", country="MA"),
        description="python backend developer building APIs " * 10,
    )


class TestScrapeZeroResultWarning:
    def test_warns_on_zero_results(self, tmp_path):
        """When a scraper returns 0 jobs (no error), a yellow warning is printed."""
        cfg = AppConfig(**MINIMAL_RAW)
        db_path = tmp_path / "test.db"

        from dev_job_radar.db import JobDB

        setup_db = JobDB(db_path)

        def _make_db(_cfg=None):
            return JobDB(db_path)

        mock_scraper = MagicMock()
        mock_scraper.scrape.return_value = []  # zero results, no error

        with (
            patch("dev_job_radar.cli._get_config", return_value=cfg),
            patch("dev_job_radar.cli._get_db", side_effect=_make_db),
            patch("dev_job_radar.cli.get_scraper", return_value=mock_scraper),
        ):
            result = runner.invoke(app, ["scrape"])

        setup_db.close()

        assert result.exit_code == 0
        assert "Warning" in result.output
        assert "0 jobs" in result.output

    def test_warns_on_zero_results_dry_run(self, tmp_path):
        """Zero-result warning also fires in --dry-run mode (no DB)."""
        cfg = AppConfig(**MINIMAL_RAW)

        mock_scraper = MagicMock()
        mock_scraper.scrape.return_value = []

        with (
            patch("dev_job_radar.cli._get_config", return_value=cfg),
            patch("dev_job_radar.cli.get_scraper", return_value=mock_scraper),
        ):
            result = runner.invoke(app, ["scrape", "--dry-run"])

        assert result.exit_code == 0
        assert "Warning" in result.output
        assert "0 jobs" in result.output

    def test_no_warning_when_results_found(self, tmp_path):
        """When a scraper returns jobs, no zero-result warning appears."""
        from dev_job_radar.models import Job, Location, Site

        cfg = AppConfig(**MINIMAL_RAW)
        db_path = tmp_path / "test.db"

        from dev_job_radar.db import JobDB

        setup_db = JobDB(db_path)

        def _make_db(_cfg=None):
            return JobDB(db_path)

        job = Job(
            source=Site.LINKEDIN,
            source_id="x1",
            url="https://example.com/x1",
            title="Software Engineer",
            company="TestCo",
            location=Location(city="SF", state="CA"),
            description="python backend",
            score=0,
            score_breakdown={},
        )

        mock_scraper = MagicMock()
        mock_scraper.scrape.return_value = [job]

        with (
            patch("dev_job_radar.cli._get_config", return_value=cfg),
            patch("dev_job_radar.cli._get_db", side_effect=_make_db),
            patch("dev_job_radar.cli.get_scraper", return_value=mock_scraper),
        ):
            result = runner.invoke(app, ["scrape"])

        setup_db.close()

        assert result.exit_code == 0
        assert "Warning" not in result.output


class TestScrapeRunDedup:
    def test_overlap_across_search_terms_is_processed_once(self, tmp_path):
        """Same source_id from two terms creates one row and one notification candidate."""
        from dev_job_radar.db import JobDB

        raw = {
            **MINIMAL_RAW,
            "search": {
                **MINIMAL_RAW["search"],
                "terms": ["python", "backend"],
                "sites": ["indeed"],
            },
            "scraping": {"max_workers": 1, "delay_min_seconds": 0, "delay_max_seconds": 0},
            "scoring": {"min_alert_score": 1, "min_display_score": 1},
        }
        cfg = AppConfig(**raw)
        db_path = tmp_path / "test.db"
        setup_db = JobDB(db_path)

        def _make_db(_cfg=None):
            return JobDB(db_path)

        class FakeScraper:
            def scrape(self, params):
                return [_make_job("same-indeed-key")]

        notifier = MagicMock()

        with (
            patch("dev_job_radar.cli._get_config", return_value=cfg),
            patch("dev_job_radar.cli._get_db", side_effect=_make_db),
            patch("dev_job_radar.cli.get_scraper", return_value=FakeScraper()),
            patch("dev_job_radar.cli.Notifier", return_value=notifier),
        ):
            result = runner.invoke(app, ["scrape"])

        setup_db.close()

        assert result.exit_code == 0
        db = JobDB(db_path)
        job_count = db.conn.execute("SELECT COUNT(*) as cnt FROM jobs").fetchone()["cnt"]
        runs = db.conn.execute(
            "SELECT search_term, jobs_found, jobs_new FROM scrape_runs ORDER BY id"
        ).fetchall()
        db.close()

        assert job_count == 1
        assert sum(row["jobs_found"] for row in runs) == 2
        assert sum(row["jobs_new"] for row in runs) == 1
        notifier.notify_new_jobs.assert_called_once()
        notified_jobs = notifier.notify_new_jobs.call_args.args[0]
        assert len(notified_jobs) == 1
        assert notified_jobs[0].source_id == "same-indeed-key"

    def test_later_scrape_existing_source_id_is_not_new_or_notified(self, tmp_path):
        """A later scrape of the same source_id updates the row but is not new."""
        from dev_job_radar.db import JobDB

        raw = {
            **MINIMAL_RAW,
            "search": {
                **MINIMAL_RAW["search"],
                "terms": ["python"],
                "sites": ["indeed"],
            },
            "scraping": {"max_workers": 1, "delay_min_seconds": 0, "delay_max_seconds": 0},
            "scoring": {"min_alert_score": 1, "min_display_score": 1},
        }
        cfg = AppConfig(**raw)
        db_path = tmp_path / "test.db"
        setup_db = JobDB(db_path)

        def _make_db(_cfg=None):
            return JobDB(db_path)

        class FakeScraper:
            def scrape(self, params):
                return [_make_job("repeat-indeed-key")]

        first_notifier = MagicMock()
        with (
            patch("dev_job_radar.cli._get_config", return_value=cfg),
            patch("dev_job_radar.cli._get_db", side_effect=_make_db),
            patch("dev_job_radar.cli.get_scraper", return_value=FakeScraper()),
            patch("dev_job_radar.cli.Notifier", return_value=first_notifier),
        ):
            first = runner.invoke(app, ["scrape"])

        second_notifier = MagicMock()
        with (
            patch("dev_job_radar.cli._get_config", return_value=cfg),
            patch("dev_job_radar.cli._get_db", side_effect=_make_db),
            patch("dev_job_radar.cli.get_scraper", return_value=FakeScraper()),
            patch("dev_job_radar.cli.Notifier", return_value=second_notifier),
        ):
            second = runner.invoke(app, ["scrape"])

        setup_db.close()

        assert first.exit_code == 0
        assert second.exit_code == 0
        db = JobDB(db_path)
        job_count = db.conn.execute("SELECT COUNT(*) as cnt FROM jobs").fetchone()["cnt"]
        runs = db.conn.execute(
            "SELECT jobs_found, jobs_new FROM scrape_runs ORDER BY id"
        ).fetchall()
        db.close()

        assert job_count == 1
        assert [row["jobs_found"] for row in runs] == [1, 1]
        assert [row["jobs_new"] for row in runs] == [1, 0]
        first_notifier.notify_new_jobs.assert_called_once()
        second_notifier.notify_new_jobs.assert_not_called()
