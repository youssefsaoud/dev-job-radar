"""Tests for the digest CLI command."""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from dev_job_radar.cli import app
from dev_job_radar.config import (
    AppConfig,
    EmailConfig,
    MacOSNotifyConfig,
    NotificationsConfig,
    ScoringConfig,
    TelegramConfig,
)
from dev_job_radar.db import JobDB
from dev_job_radar.models import Compensation, CompInterval, Job, Location, Site

runner = CliRunner()


def _make_job(
    source_id, *, score=60, state="CA", company="TestCo", title="ML Engineer"
):
    return Job(
        source=Site.LINKEDIN,
        source_id=source_id,
        url=f"https://example.com/{source_id}",
        title=title,
        company=company,
        location=Location(city="San Jose", state=state),
        description="desc",
        score=score,
        score_breakdown={"keyword": score},
        status="new",
        date_scraped=datetime.now(),
        compensation=Compensation(
            min_amount=180000, max_amount=250000, interval=CompInterval.YEARLY
        ),
    )


def _mock_cfg(
    db_path, *, email_enabled=False, telegram_enabled=False, alert_states=None
):
    mock = MagicMock(spec=AppConfig)
    mock.db_path = db_path
    mock._config_path = None
    mock.config_name = None
    mock.report_dir = db_path.parent / "reports"
    mock.scoring = ScoringConfig(min_alert_score=55, alert_states=alert_states or [])
    mock.notifications = NotificationsConfig(
        macos=MacOSNotifyConfig(enabled=False),
        email=EmailConfig(
            enabled=email_enabled,
            username="a@b.com",
            app_password="pass",
            to_address="a@b.com",
        ),
        telegram=TelegramConfig(
            enabled=telegram_enabled,
            bot_token="123:ABC",
            chat_id="42",
        ),
    )
    return mock


class TestDigestCommand:
    def test_no_matches_prints_message(self, tmp_path):
        """digest() with no matching jobs prints 'no matches' message."""
        db_path = tmp_path / "test.db"
        db = JobDB(db_path)
        db.close()  # empty DB

        cfg = _mock_cfg(db_path)

        with patch("dev_job_radar.cli._get_config", return_value=cfg):
            result = runner.invoke(app, ["digest"])

        assert result.exit_code == 0
        assert "No new matches" in result.output

    def test_no_matches_old_jobs(self, tmp_path):
        """digest() skips jobs older than 24h."""
        db_path = tmp_path / "test.db"
        db = JobDB(db_path)
        old_job = _make_job("old1", score=70)
        old_job.date_scraped = datetime.now() - timedelta(hours=48)
        db.upsert_job(old_job)
        db.close()

        cfg = _mock_cfg(db_path)

        with patch("dev_job_radar.cli._get_config", return_value=cfg):
            result = runner.invoke(app, ["digest"])

        assert result.exit_code == 0
        assert "No new matches" in result.output

    def test_no_matches_below_threshold(self, tmp_path):
        """digest() skips jobs below min_alert_score."""
        db_path = tmp_path / "test.db"
        db = JobDB(db_path)
        low_job = _make_job("low1", score=30)
        low_job.date_scraped = datetime.now()
        db.upsert_job(low_job)
        db.close()

        cfg = _mock_cfg(db_path)

        with patch("dev_job_radar.cli._get_config", return_value=cfg):
            result = runner.invoke(app, ["digest"])

        assert result.exit_code == 0
        assert "No new matches" in result.output

    @patch("dev_job_radar.notify.httpx.post")
    def test_sends_telegram(self, mock_post, tmp_path):
        """digest() sends Telegram message when enabled and jobs found."""
        mock_post.return_value = MagicMock(status_code=200)

        db_path = tmp_path / "test.db"
        db = JobDB(db_path)
        db.upsert_job(_make_job("tg1", score=70, company="NVIDIA"))
        db.close()

        cfg = _mock_cfg(db_path, telegram_enabled=True)

        with patch("dev_job_radar.cli._get_config", return_value=cfg):
            result = runner.invoke(app, ["digest"])

        assert result.exit_code == 0
        assert "Digest sent" in result.output
        mock_post.assert_called_once()
        text = mock_post.call_args.kwargs["json"]["text"]
        assert "🎯 *NEW JOB*" in text
        assert "NVIDIA" in text
        assert "🔗 [View & Apply](https://example.com/tg1)" in text

    @patch("dev_job_radar.notify.httpx.post")
    def test_default_limit_is_10(self, mock_post, tmp_path):
        """digest() includes 10 qualifying jobs by default."""
        mock_post.return_value = MagicMock(status_code=200)

        db_path = tmp_path / "test.db"
        db = JobDB(db_path)
        for i in range(12):
            db.upsert_job(
                _make_job(
                    f"default-limit-{i}",
                    score=100 - i,
                    company=f"DefaultCo{i:02d}",
                )
            )
        db.close()

        cfg = _mock_cfg(db_path, telegram_enabled=True)

        with patch("dev_job_radar.cli._get_config", return_value=cfg):
            result = runner.invoke(app, ["digest"])

        assert result.exit_code == 0
        assert mock_post.call_count == 10
        text = "\n".join(call.kwargs["json"]["text"] for call in mock_post.call_args_list)
        for i in range(10):
            assert f"DefaultCo{i:02d}" in text
        assert "DefaultCo10" not in text
        assert "DefaultCo11" not in text

    @patch("dev_job_radar.notify.httpx.post")
    def test_custom_limit_100(self, mock_post, tmp_path):
        """digest --limit 100 can include more than the default 10 jobs."""
        mock_post.return_value = MagicMock(status_code=200)

        db_path = tmp_path / "test.db"
        db = JobDB(db_path)
        for i in range(25):
            db.upsert_job(
                _make_job(
                    f"custom-limit-{i}",
                    score=100 - i,
                    company=f"CustomCo{i:02d}",
                    title=f"Backend Engineer {i:02d}",
                )
            )
        db.close()

        cfg = _mock_cfg(db_path, telegram_enabled=True)

        with patch("dev_job_radar.cli._get_config", return_value=cfg):
            result = runner.invoke(app, ["digest", "--limit", "100"])

        assert result.exit_code == 0
        assert mock_post.call_count == 25
        text = "\n".join(call.kwargs["json"]["text"] for call in mock_post.call_args_list)
        for i in range(25):
            assert f"CustomCo{i:02d}" in text

    @patch("dev_job_radar.notify.smtplib.SMTP")
    def test_sends_email(self, mock_smtp, tmp_path):
        """digest() sends email when enabled and jobs found."""
        mock_smtp_instance = MagicMock()
        mock_smtp.return_value = mock_smtp_instance

        db_path = tmp_path / "test.db"
        db = JobDB(db_path)
        db.upsert_job(_make_job("em1", score=70, company="Google"))
        db.close()

        cfg = _mock_cfg(db_path, email_enabled=True)

        with patch("dev_job_radar.cli._get_config", return_value=cfg):
            result = runner.invoke(app, ["digest"])

        assert result.exit_code == 0
        assert "Digest sent" in result.output
        mock_smtp.assert_called_once()

    @patch("dev_job_radar.notify.httpx.post")
    @patch("dev_job_radar.notify.smtplib.SMTP")
    def test_sends_both_channels(self, mock_smtp, mock_post, tmp_path):
        """digest() sends to both email and Telegram when both enabled."""
        mock_post.return_value = MagicMock(status_code=200)
        mock_smtp_instance = MagicMock()
        mock_smtp.return_value = mock_smtp_instance

        db_path = tmp_path / "test.db"
        db = JobDB(db_path)
        db.upsert_job(_make_job("both1", score=70))
        db.close()

        cfg = _mock_cfg(db_path, email_enabled=True, telegram_enabled=True)

        with patch("dev_job_radar.cli._get_config", return_value=cfg):
            result = runner.invoke(app, ["digest"])

        assert result.exit_code == 0
        assert "Digest sent" in result.output
        mock_post.assert_called_once()
        mock_smtp.assert_called_once()

    @patch("dev_job_radar.notify.httpx.post")
    def test_failed_send_prints_error(self, mock_post, tmp_path):
        """digest() prints error when all sends fail."""
        mock_post.return_value = MagicMock(status_code=400, text="Bad Request")

        db_path = tmp_path / "test.db"
        db = JobDB(db_path)
        db.upsert_job(_make_job("fail1", score=70))
        db.close()

        cfg = _mock_cfg(db_path, telegram_enabled=True)

        with patch("dev_job_radar.cli._get_config", return_value=cfg):
            result = runner.invoke(app, ["digest"])

        assert result.exit_code == 0
        assert "Failed to send digest" in result.output

    def test_respects_alert_states_filter(self, tmp_path):
        """digest() filters jobs by alert_states."""
        db_path = tmp_path / "test.db"
        db = JobDB(db_path)
        # CA job -- allowed
        db.upsert_job(_make_job("ca1", score=70, state="CA"))
        # TX job -- not allowed
        db.upsert_job(_make_job("tx1", score=70, state="TX"))
        db.close()

        cfg = _mock_cfg(db_path, alert_states=["CA", "WA"])

        with (
            patch("dev_job_radar.cli._get_config", return_value=cfg),
            patch("dev_job_radar.notify.httpx.post") as mock_post,
        ):
            mock_post.return_value = MagicMock(status_code=200)
            # Enable telegram to observe what gets sent
            cfg.notifications.telegram = TelegramConfig(
                enabled=True, bot_token="123:ABC", chat_id="42"
            )
            result = runner.invoke(app, ["digest"])

        assert result.exit_code == 0
        # Should have sent -- CA job passes the filter
        assert "Digest sent" in result.output
        mock_post.assert_called_once()
        text = mock_post.call_args.kwargs["json"]["text"]
        assert "🎯 *NEW JOB*" in text
        assert "San Jose, CA" in text
        assert "tx1" not in text

    @patch("dev_job_radar.notify.httpx.post")
    def test_digest_uses_one_job_per_telegram_message(self, mock_post, tmp_path):
        """Telegram digest uses the same per-job presentation as live alerts."""
        mock_post.return_value = MagicMock(status_code=200)

        db_path = tmp_path / "test.db"
        db = JobDB(db_path)
        for i in range(3):
            job = _make_job(
                f"one-job-{i}",
                score=80 - i,
                company=f"DigestCo{i}",
                title=f"Backend Engineer {i}",
            )
            job.id = 179 + i
            db.upsert_job(job)
        db.close()

        cfg = _mock_cfg(db_path, telegram_enabled=True)

        with patch("dev_job_radar.cli._get_config", return_value=cfg):
            result = runner.invoke(app, ["digest", "--limit", "3"])

        assert result.exit_code == 0
        assert mock_post.call_count == 3
        texts = [call.kwargs["json"]["text"] for call in mock_post.call_args_list]
        assert all("🎯 *NEW JOB*" in text for text in texts)
        assert all("View & Apply" in text for text in texts)
        assert all("#179" not in text and "\\#179" not in text for text in texts)
        for i, text in enumerate(texts):
            assert f"DigestCo{i}" in text

    def test_no_notifications_configured(self, tmp_path):
        """digest() with no notification channels enabled prints failure."""
        db_path = tmp_path / "test.db"
        db = JobDB(db_path)
        db.upsert_job(_make_job("nn1", score=70))
        db.close()

        cfg = _mock_cfg(db_path, email_enabled=False, telegram_enabled=False)

        with patch("dev_job_radar.cli._get_config", return_value=cfg):
            result = runner.invoke(app, ["digest"])

        assert result.exit_code == 0
        # With jobs found but no channels, it should indicate failure
        assert "Failed to send digest" in result.output
