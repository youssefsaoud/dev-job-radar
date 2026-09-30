"""Tests for scheduler.py multi-plist support."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, MagicMock


from dev_job_radar.config import BotConfig, NotificationsConfig, ScheduleConfig


class TestGeneratePlists:
    def test_returns_three_plists(self):
        from dev_job_radar.scheduler import generate_plists

        schedule = ScheduleConfig()
        plists = generate_plists(schedule, project_dir=Path("/fake/project"))
        assert len(plists) == 3
        labels = list(plists.keys())
        assert "com.user.dev-job-radar.scrape" in labels
        assert "com.user.dev-job-radar.digest" in labels
        assert "com.user.dev-job-radar.report" in labels

    def test_scrape_uses_start_interval(self):
        from dev_job_radar.scheduler import generate_plists

        schedule = ScheduleConfig(interval_hours=4)
        plists = generate_plists(schedule, project_dir=Path("/fake/project"))
        scrape = plists["com.user.dev-job-radar.scrape"]
        assert scrape["StartInterval"] == 4 * 3600
        assert scrape["RunAtLoad"] is True

    def test_digest_uses_calendar_interval(self):
        from dev_job_radar.scheduler import generate_plists

        schedule = ScheduleConfig(digest_hour=10, digest_minute=30)
        plists = generate_plists(schedule, project_dir=Path("/fake/project"))
        digest = plists["com.user.dev-job-radar.digest"]
        assert digest["StartCalendarInterval"] == {"Hour": 10, "Minute": 30}
        assert digest["RunAtLoad"] is False

    def test_report_uses_calendar_interval(self):
        from dev_job_radar.scheduler import generate_plists

        schedule = ScheduleConfig(report_hour=7, report_minute=45)
        plists = generate_plists(schedule, project_dir=Path("/fake/project"))
        report = plists["com.user.dev-job-radar.report"]
        assert report["StartCalendarInterval"] == {"Hour": 7, "Minute": 45}
        assert report["RunAtLoad"] is False

    def test_command_args_correct(self):
        from dev_job_radar.scheduler import generate_plists

        schedule = ScheduleConfig()
        plists = generate_plists(schedule, project_dir=Path("/fake/project"))

        scrape_args = plists["com.user.dev-job-radar.scrape"]["ProgramArguments"]
        assert scrape_args[-2:] == ["dev_job_radar", "scrape"]

        digest_args = plists["com.user.dev-job-radar.digest"]["ProgramArguments"]
        assert digest_args[-2:] == ["dev_job_radar", "digest"]

        report_args = plists["com.user.dev-job-radar.report"]["ProgramArguments"]
        assert report_args[-2:] == ["dev_job_radar", "report"]

    def test_separate_log_files(self):
        from dev_job_radar.scheduler import generate_plists

        schedule = ScheduleConfig()
        plists = generate_plists(schedule, project_dir=Path("/fake/project"))

        stdout_paths = {p["StandardOutPath"] for p in plists.values()}
        # All 3 should have different log paths
        assert len(stdout_paths) == 3


class TestGeneratePlistsMultiConfig:
    def test_default_no_config_flag(self):
        from dev_job_radar.scheduler import generate_plists

        schedule = ScheduleConfig()
        plists = generate_plists(schedule, project_dir=Path("/fake/project"))
        scrape_args = plists["com.user.dev-job-radar.scrape"]["ProgramArguments"]
        assert "--config" not in scrape_args

    def test_named_includes_config_flag(self, tmp_path):
        from dev_job_radar.scheduler import generate_plists

        config_path = tmp_path / "frontend.yaml"
        config_path.touch()
        schedule = ScheduleConfig()
        plists = generate_plists(
            schedule,
            project_dir=Path("/fake/project"),
            profile_name="frontend",
            config_path=config_path,
        )
        label = "com.user.dev-job-radar.frontend.scrape"
        scrape_args = plists[label]["ProgramArguments"]
        assert "--config" in scrape_args
        config_idx = scrape_args.index("--config")
        assert scrape_args[config_idx + 1] == str(config_path.resolve())

    def test_named_labels(self, tmp_path):
        from dev_job_radar.scheduler import generate_plists

        config_path = tmp_path / "frontend.yaml"
        config_path.touch()
        schedule = ScheduleConfig()
        plists = generate_plists(
            schedule,
            project_dir=Path("/fake/project"),
            profile_name="frontend",
            config_path=config_path,
        )
        labels = list(plists.keys())
        assert "com.user.dev-job-radar.frontend.scrape" in labels
        assert "com.user.dev-job-radar.frontend.digest" in labels
        assert "com.user.dev-job-radar.frontend.report" in labels

    def test_log_paths_namespaced(self, tmp_path):
        from dev_job_radar.scheduler import generate_plists

        config_path = tmp_path / "frontend.yaml"
        config_path.touch()
        schedule = ScheduleConfig()

        with patch("dev_job_radar.scheduler.LOG_DIR", tmp_path / "logs"):
            plists = generate_plists(
                schedule,
                project_dir=Path("/fake/project"),
                profile_name="frontend",
                config_path=config_path,
            )

        label = "com.user.dev-job-radar.frontend.scrape"
        stdout_path = plists[label]["StandardOutPath"]
        assert "frontend" in stdout_path


class TestUninstallLegacy:
    @patch("dev_job_radar.scheduler.subprocess.run")
    def test_removes_legacy_plist(self, mock_run):
        from dev_job_radar.scheduler import uninstall, PLIST_DIR, LEGACY_LABEL

        legacy_path = PLIST_DIR / f"{LEGACY_LABEL}.plist"

        with (
            patch.object(Path, "exists", return_value=True),
            patch.object(Path, "unlink"),
        ):
            uninstall()

        # Should have attempted to unload and unlink the legacy plist
        unload_calls = [
            c
            for c in mock_run.call_args_list
            if Path(c.args[0][2]) == legacy_path
        ]
        assert len(unload_calls) >= 1


class TestPlistLabels:
    def test_label_constants(self):
        from dev_job_radar.scheduler import PLIST_LABELS

        assert PLIST_LABELS["scrape"] == "com.user.dev-job-radar.scrape"
        assert PLIST_LABELS["digest"] == "com.user.dev-job-radar.digest"
        assert PLIST_LABELS["report"] == "com.user.dev-job-radar.report"

    def test_plist_labels_default(self):
        from dev_job_radar.scheduler import plist_labels

        labels = plist_labels("default")
        assert labels["scrape"] == "com.user.dev-job-radar.scrape"
        assert labels["digest"] == "com.user.dev-job-radar.digest"
        assert labels["report"] == "com.user.dev-job-radar.report"

    def test_plist_labels_named(self):
        from dev_job_radar.scheduler import plist_labels

        labels = plist_labels("frontend")
        assert labels["scrape"] == "com.user.dev-job-radar.frontend.scrape"
        assert labels["digest"] == "com.user.dev-job-radar.frontend.digest"
        assert labels["report"] == "com.user.dev-job-radar.frontend.report"


class TestInstall:
    @patch("dev_job_radar.scheduler.subprocess.run")
    def test_installs_three_plists(self, mock_run, tmp_path):
        from dev_job_radar.scheduler import install, PLIST_LABELS

        plist_dir = tmp_path / "LaunchAgents"
        plist_dir.mkdir()
        schedule = ScheduleConfig()

        with (
            patch("dev_job_radar.scheduler.PLIST_DIR", plist_dir),
            patch("dev_job_radar.scheduler.LOG_DIR", tmp_path / "logs"),
        ):
            paths = install(schedule, project_dir=tmp_path)

        assert len(paths) == 3
        # All plist files should exist
        for label in PLIST_LABELS.values():
            assert (plist_dir / f"{label}.plist").exists()
        # subprocess should have been called for unload + load for each plist (6 calls total)
        assert mock_run.call_count == 6

    @patch("dev_job_radar.scheduler.subprocess.run")
    def test_install_returns_correct_paths(self, mock_run, tmp_path):
        from dev_job_radar.scheduler import install, PLIST_LABELS

        plist_dir = tmp_path / "LaunchAgents"
        plist_dir.mkdir()
        schedule = ScheduleConfig()

        with (
            patch("dev_job_radar.scheduler.PLIST_DIR", plist_dir),
            patch("dev_job_radar.scheduler.LOG_DIR", tmp_path / "logs"),
        ):
            paths = install(schedule, project_dir=tmp_path)

        expected_labels = list(PLIST_LABELS.values())
        for path, label in zip(paths, expected_labels):
            assert path.name == f"{label}.plist"


class TestStatus:
    @patch("dev_job_radar.scheduler.subprocess.run")
    def test_status_not_installed(self, mock_run, tmp_path):
        from dev_job_radar.scheduler import status

        plist_dir = tmp_path / "LaunchAgents"
        plist_dir.mkdir()

        with patch("dev_job_radar.scheduler.PLIST_DIR", plist_dir):
            result = status()

        assert "scrape" in result
        assert "digest" in result
        assert "report" in result
        assert result["scrape"]["installed"] is False
        assert result["scrape"]["running"] is False
        # subprocess should not be called for non-installed plists
        mock_run.assert_not_called()

    @patch("dev_job_radar.scheduler.subprocess.run")
    def test_status_installed_and_running(self, mock_run, tmp_path):
        from dev_job_radar.scheduler import status, PLIST_LABELS

        plist_dir = tmp_path / "LaunchAgents"
        plist_dir.mkdir()

        # Create plist files to simulate installation
        for label in PLIST_LABELS.values():
            (plist_dir / f"{label}.plist").touch()

        # Mock launchctl list returning success (running)
        mock_run.return_value = MagicMock(returncode=0)

        with patch("dev_job_radar.scheduler.PLIST_DIR", plist_dir):
            result = status()

        assert result["scrape"]["installed"] is True
        assert result["scrape"]["running"] is True
        assert result["digest"]["installed"] is True
        assert result["report"]["installed"] is True
        assert "log_dir" in result

    @patch("dev_job_radar.scheduler.subprocess.run")
    def test_status_installed_not_running(self, mock_run, tmp_path):
        from dev_job_radar.scheduler import status, PLIST_LABELS

        plist_dir = tmp_path / "LaunchAgents"
        plist_dir.mkdir()

        for label in PLIST_LABELS.values():
            (plist_dir / f"{label}.plist").touch()

        # Mock launchctl list returning failure (not running)
        mock_run.return_value = MagicMock(returncode=1)

        with patch("dev_job_radar.scheduler.PLIST_DIR", plist_dir):
            result = status()

        assert result["scrape"]["installed"] is True
        assert result["scrape"]["running"] is False


class TestUninstallNewPlists:
    @patch("dev_job_radar.scheduler.subprocess.run")
    def test_removes_all_new_plists(self, mock_run, tmp_path):
        from dev_job_radar.scheduler import uninstall, PLIST_LABELS

        plist_dir = tmp_path / "LaunchAgents"
        plist_dir.mkdir()

        # Create all new plist files
        for label in PLIST_LABELS.values():
            (plist_dir / f"{label}.plist").touch()

        with patch("dev_job_radar.scheduler.PLIST_DIR", plist_dir):
            uninstall()

        # All new plists should be removed
        for label in PLIST_LABELS.values():
            assert not (plist_dir / f"{label}.plist").exists()

    @patch("dev_job_radar.scheduler.subprocess.run")
    def test_uninstall_no_error_when_not_installed(self, mock_run, tmp_path):
        """uninstall() should not error when no plists exist."""
        from dev_job_radar.scheduler import uninstall

        plist_dir = tmp_path / "LaunchAgents"
        plist_dir.mkdir()

        with patch("dev_job_radar.scheduler.PLIST_DIR", plist_dir):
            uninstall()  # Should not raise

        mock_run.assert_not_called()


class TestScheduleCLI:
    @patch("dev_job_radar.scheduler.subprocess.run")
    def test_schedule_status_display(self, mock_run, tmp_path):
        """schedule command (no flags) shows status."""
        from typer.testing import CliRunner
        from dev_job_radar.cli import app

        plist_dir = tmp_path / "LaunchAgents"
        plist_dir.mkdir()

        with patch("dev_job_radar.scheduler.PLIST_DIR", plist_dir):
            runner = CliRunner()
            result = runner.invoke(app, ["schedule"])

        assert result.exit_code == 0
        assert "scrape" in result.output
        assert "digest" in result.output
        assert "report" in result.output
        assert "not installed" in result.output

    @patch("dev_job_radar.scheduler.subprocess.run")
    def test_schedule_install(self, mock_run, tmp_path):
        """schedule --install calls installer and shows confirmation."""
        from typer.testing import CliRunner
        from dev_job_radar.cli import app
        from dev_job_radar.config import AppConfig

        plist_dir = tmp_path / "LaunchAgents"
        plist_dir.mkdir()

        cfg = MagicMock(spec=AppConfig)
        cfg.schedule = ScheduleConfig()
        cfg._config_path = tmp_path / "config.yaml"
        cfg.config_name = None
        cfg.db_path = None
        cfg.report_dir = Path.home() / ".local" / "share" / "dev-job-radar" / "reports"
        cfg.notifications = NotificationsConfig()
        cfg.bot = BotConfig()

        with (
            patch("dev_job_radar.cli._get_config", return_value=cfg),
            patch("dev_job_radar.scheduler.PLIST_DIR", plist_dir),
            patch("dev_job_radar.scheduler.LOG_DIR", tmp_path / "logs"),
        ):
            runner = CliRunner()
            result = runner.invoke(app, ["schedule", "--install"])

        assert result.exit_code == 0
        assert "Installed schedules" in result.output
        assert "every 6 hours" in result.output
        assert "09:00" in result.output  # digest default
        assert "08:50" in result.output  # report default

    @patch("dev_job_radar.scheduler.subprocess.run")
    def test_schedule_uninstall(self, mock_run, tmp_path):
        """schedule --uninstall calls uninstaller."""
        from typer.testing import CliRunner
        from dev_job_radar.cli import app

        plist_dir = tmp_path / "LaunchAgents"
        plist_dir.mkdir()

        with patch("dev_job_radar.scheduler.PLIST_DIR", plist_dir):
            runner = CliRunner()
            result = runner.invoke(app, ["schedule", "--uninstall"])

        assert result.exit_code == 0
        assert "removed" in result.output.lower()
