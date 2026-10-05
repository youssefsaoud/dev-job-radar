"""Tests for zero-result warnings in the scrape command."""

from __future__ import annotations

import logging
from types import SimpleNamespace
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


def _mock_paths(tmp_path):
    return SimpleNamespace(
        profile_name="default",
        db=tmp_path / "test.db",
        logs=tmp_path / "logs",
        reports=tmp_path / "reports",
    )


def _read_scrape_log(log_path):
    for handler in logging.getLogger().handlers:
        if getattr(handler, "_dev_job_radar_scrape_log", False):
            handler.flush()
    return log_path.read_text(encoding="utf-8")


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


class TestScrapeFileLogging:
    def test_creates_log_file_and_logs_start_result_and_completion(self, tmp_path):
        """scrape writes a persistent rotating log file for unattended runs."""
        raw = {
            **MINIMAL_RAW,
            "search": {
                **MINIMAL_RAW["search"],
                "terms": ["python"],
                "locations": ["Morocco"],
                "sites": ["indeed"],
            },
            "scraping": {"max_workers": 1},
        }
        cfg = AppConfig(**raw)
        cfg._config_path = tmp_path / "config.yaml"
        paths = _mock_paths(tmp_path)

        mock_scraper = MagicMock()
        mock_scraper.scrape.return_value = [_make_job("log-1")]

        with (
            patch("dev_job_radar.cli._get_config", return_value=cfg),
            patch("dev_job_radar.cli.resolve_data_paths", return_value=paths),
            patch("dev_job_radar.cli.get_scraper", return_value=mock_scraper),
        ):
            result = runner.invoke(app, ["scrape", "--dry-run"])

        log_path = paths.logs / "dev-job-radar.log"
        assert result.exit_code == 0
        assert "Scraped indeed" in result.output
        assert log_path.exists()
        text = _read_scrape_log(log_path)
        assert "Scrape run started" in text
        assert f"config={cfg._config_path}" in text
        assert "sites=indeed" in text
        assert "terms=python" in text
        assert "locations=Morocco" in text
        assert "Indeed | python | Morocco | 1 jobs found" in text
        assert "Scrape completed" in text
        assert "returned=1" in text
        assert "duration=" in text

    def test_scraper_error_is_logged_with_context_and_traceback(self, tmp_path):
        cfg = AppConfig(**MINIMAL_RAW)
        cfg._config_path = tmp_path / "config.yaml"
        paths = _mock_paths(tmp_path)

        class FailingScraper:
            def scrape(self, params):
                raise RuntimeError("network down")

        with (
            patch("dev_job_radar.cli._get_config", return_value=cfg),
            patch("dev_job_radar.cli.resolve_data_paths", return_value=paths),
            patch("dev_job_radar.cli.get_scraper", return_value=FailingScraper()),
        ):
            result = runner.invoke(app, ["scrape", "--dry-run"])

        log_path = paths.logs / "dev-job-radar.log"
        assert result.exit_code == 0
        assert "Error scraping linkedin" in result.output
        text = _read_scrape_log(log_path)
        assert "Scrape request failed | site=linkedin | term=python | location=Remote" in text
        assert "Traceback" in text
        assert "RuntimeError: network down" in text
        assert "Scrape request error recorded" in text
        assert "Scrape completed" in text

    def test_unexpected_exception_is_logged_with_traceback(self, tmp_path):
        cfg = AppConfig(**MINIMAL_RAW)
        cfg._config_path = tmp_path / "config.yaml"
        paths = _mock_paths(tmp_path)

        mock_scraper = MagicMock()
        mock_scraper.scrape.return_value = [_make_job("boom")]

        with (
            patch("dev_job_radar.cli._get_config", return_value=cfg),
            patch("dev_job_radar.cli.resolve_data_paths", return_value=paths),
            patch("dev_job_radar.cli.get_scraper", return_value=mock_scraper),
            patch(
                "dev_job_radar.cli.JobScorer.score",
                side_effect=RuntimeError("scorer exploded"),
            ),
        ):
            result = runner.invoke(app, ["scrape", "--dry-run"])

        log_path = paths.logs / "dev-job-radar.log"
        assert result.exit_code != 0
        text = _read_scrape_log(log_path)
        assert "Scrape terminated unexpectedly" in text
        assert "Traceback" in text
        assert "RuntimeError: scorer exploded" in text

    def test_secrets_are_not_written_to_scrape_log(self, tmp_path):
        raw = {
            **MINIMAL_RAW,
            "notifications": {
                "telegram": {
                    "enabled": True,
                    "bot_token": "123:SECRET_TOKEN",
                    "chat_id": "secret-chat",
                },
                "email": {
                    "enabled": True,
                    "username": "me@example.com",
                    "app_password": "email-secret",
                    "to_address": "me@example.com",
                },
                "slack": {
                    "enabled": True,
                    "webhook_url": "https://hooks.slack.com/services/SECRET",
                },
                "discord": {
                    "enabled": True,
                    "webhook_url": "https://discord.com/api/webhooks/SECRET",
                },
            },
            "bot": {"gemini_api_key": "gemini-secret"},
        }
        cfg = AppConfig(**raw)
        cfg._config_path = tmp_path / "config.yaml"
        paths = _mock_paths(tmp_path)

        mock_scraper = MagicMock()
        mock_scraper.scrape.return_value = []

        with (
            patch("dev_job_radar.cli._get_config", return_value=cfg),
            patch("dev_job_radar.cli.resolve_data_paths", return_value=paths),
            patch("dev_job_radar.cli.get_scraper", return_value=mock_scraper),
        ):
            result = runner.invoke(app, ["scrape", "--dry-run"])

        assert result.exit_code == 0
        text = _read_scrape_log(paths.logs / "dev-job-radar.log")
        assert "SECRET_TOKEN" not in text
        assert "secret-chat" not in text
        assert "email-secret" not in text
        assert "hooks.slack.com" not in text
        assert "discord.com/api/webhooks" not in text
        assert "gemini-secret" not in text
