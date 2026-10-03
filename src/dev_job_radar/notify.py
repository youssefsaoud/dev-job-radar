"""Notification system: macOS native + email + Telegram + Slack + Discord."""

from __future__ import annotations

import logging
import smtplib
import subprocess
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email import encoders
from pathlib import Path

import httpx

from dev_job_radar.config import DiscordConfig, NotificationsConfig, SlackConfig
from dev_job_radar.models import Job

log = logging.getLogger("dev_job_radar.notify")

TELEGRAM_SAFE_MESSAGE_LIMIT = 3800
TELEGRAM_SOURCE_LABELS = {
    "linkedin": "LinkedIn",
    "indeed": "Indeed",
    "google": "Google Jobs",
    "glassdoor": "Glassdoor",
    "ziprecruiter": "ZipRecruiter",
    "bayt": "Bayt",
}


class Notifier:
    def __init__(self, config: NotificationsConfig, profile_name: str = "default"):
        self.config = config
        self.profile_name = profile_name

    def notify_new_jobs(self, jobs: list[Job]) -> None:
        if not jobs:
            return
        if self.config.macos.enabled:
            self._notify_macos(jobs)
        if self.config.email.enabled:
            self._notify_email(jobs)
        if self.config.telegram.enabled:
            self._notify_telegram(jobs)
        if self.config.slack.enabled:
            self._notify_slack(jobs)
        if self.config.discord.enabled:
            self._notify_discord(jobs)

    def _notify_macos(self, jobs: list[Job]) -> None:
        prefix = (
            f"Dev Job Radar ({self.profile_name})"
            if self.profile_name != "default"
            else "Dev Job Radar"
        )
        if len(jobs) == 1:
            job = jobs[0]
            title = f"{prefix}: {job.company}"
            body = f"{job.title} (Score: {job.score})"
        else:
            title = f"{prefix}: {len(jobs)} new matches"
            top3 = jobs[:3]
            body = "\\n".join(f"{j.company}: {j.title} ({j.score})" for j in top3)
            if len(jobs) > 3:
                body += f"\\n... and {len(jobs) - 3} more"

        script = (
            f'display notification "{_esc(body)}" '
            f'with title "{_esc(title)}" '
            f'sound name "{self.config.macos.sound}"'
        )
        try:
            subprocess.run(
                ["osascript", "-e", script],
                capture_output=True,
                timeout=5,
            )
        except Exception as e:
            log.error(f"macOS notification failed: {e}")

    def _notify_email(self, jobs: list[Job]) -> None:
        cfg = self.config.email
        if not cfg.username or not cfg.app_password or not cfg.to_address:
            log.warning("Email not configured — skipping alert")
            return

        prefix = (
            f"Dev Job Radar ({self.profile_name})"
            if self.profile_name != "default"
            else "Dev Job Radar"
        )
        lines = [f"{prefix} alert — {len(jobs)} new match(es)\n"]
        for job in jobs:
            salary = job.compensation.display_concise if job.compensation else ""
            kw = job.score_breakdown.get("keyword", "?") if job.score_breakdown else "?"
            id_tag = f"#{job.id} " if job.id else ""
            loc_line = f"  {job.location.display}"
            if salary:
                loc_line += f" | {salary}"
            lines.append(
                f"[{job.score}] (kw:{kw}) {id_tag}{job.company}: {job.title}\n"
                f"{loc_line}\n"
                f"  {job.url}\n"
            )
        body = "\n".join(lines)

        send_email(
            subject=f"{prefix}: {len(jobs)} new match(es)",
            body=body,
            cfg=cfg,
        )

    def _notify_telegram(self, jobs: list[Job]) -> None:
        cfg = self.config.telegram
        if not cfg.bot_token or not cfg.chat_id:
            log.warning("Telegram bot_token or chat_id not configured")
            return

        send_telegram_job_alerts(jobs, cfg)

    def _notify_slack(self, jobs: list[Job]) -> None:
        cfg = self.config.slack
        if not cfg.webhook_url:
            log.warning("Slack webhook_url not configured")
            return

        prefix = (
            f"*Dev Job Radar ({self.profile_name})* "
            if self.profile_name != "default"
            else "*Dev Job Radar* "
        )
        lines = [f"{prefix}— {len(jobs)} new match(es)\n"]
        for job in jobs:
            salary = job.compensation.display_concise if job.compensation else ""
            kw = job.score_breakdown.get("keyword", "?") if job.score_breakdown else "?"
            id_tag = f"#{job.id} " if job.id else ""
            loc_line = f"  {_esc_slack(job.location.display)}"
            if salary:
                loc_line += f" | {_esc_slack(salary)}"
            lines.append(
                f"*{_esc_slack(job.company)}: {_esc_slack(job.title)}*\n"
                f"Score: {job.score} | keywords: {kw} | {id_tag}{loc_line}\n"
                f"{job.url}"
            )
        text = "\n".join(lines)

        send_slack(text=text, cfg=cfg)

    def _notify_discord(self, jobs: list[Job]) -> None:
        cfg = self.config.discord
        if not cfg.webhook_url:
            log.warning("Discord webhook_url not configured")
            return

        prefix = (
            f"**Dev Job Radar ({self.profile_name})**"
            if self.profile_name != "default"
            else "**Dev Job Radar**"
        )
        lines = [f"{prefix} — {len(jobs)} new match(es)\n"]
        for job in jobs:
            salary = job.compensation.display_concise if job.compensation else ""
            kw = job.score_breakdown.get("keyword", "?") if job.score_breakdown else "?"
            id_tag = f"#{job.id} " if job.id else ""
            loc_line = f"  {_esc_discord(job.location.display)}"
            if salary:
                loc_line += f" | {_esc_discord(salary)}"
            lines.append(
                f"**{_esc_discord(job.company)}: {_esc_discord(job.title)}**\n"
                f"Score: {job.score} | keywords: {kw} | {id_tag}{loc_line}\n"
                f"{job.url}"
            )
        text = "\n".join(lines)

        send_discord(text=text, cfg=cfg)


def send_telegram(text: str, cfg) -> bool:
    """Send a message via Telegram Bot API."""
    if not cfg.bot_token or not cfg.chat_id:
        log.error("Telegram not configured (missing bot_token or chat_id)")
        return False

    url = f"https://api.telegram.org/bot{cfg.bot_token}/sendMessage"
    try:
        resp = httpx.post(
            url,
            json={
                "chat_id": cfg.chat_id,
                "text": text,
                "parse_mode": "MarkdownV2",
                "disable_web_page_preview": True,
            },
            timeout=10,
        )
        if resp.status_code == 200:
            log.info("Telegram message sent")
            return True
        log.error(f"Telegram API returned {resp.status_code}: {resp.text}")
        return False
    except Exception as e:
        log.error(f"Telegram notification failed: {e}")
        return False


def send_telegram_job_alerts(jobs: list[Job], cfg) -> bool:
    """Send each job as its own Telegram message."""
    if not jobs:
        return False

    sent_any = False
    for index, job in enumerate(jobs, 1):
        try:
            text = build_telegram_job_alert(job)
        except Exception as e:
            log.error(
                "Telegram job alert formatting failed "
                f"for source_id={job.source_id}: {e}"
            )
            continue

        if send_telegram(text=text, cfg=cfg):
            sent_any = True
        else:
            log.error(
                "Telegram job alert "
                f"{index}/{len(jobs)} failed for source_id={job.source_id}"
            )

    return sent_any


def build_telegram_job_alert(
    job: Job,
    *,
    max_chars: int = TELEGRAM_SAFE_MESSAGE_LIMIT,
) -> str:
    """Build one mobile-friendly MarkdownV2 Telegram alert for one job."""
    title = job.title or "Untitled job"
    company = job.company or "Unknown company"
    location = job.location.display if job.location else "Unknown"
    salary = job.compensation.display_concise if job.compensation else ""

    for limit in (320, 240, 160, 100, 60, 40):
        message = _telegram_single_job_message(
            job,
            title=_truncate(title, limit),
            company=_truncate(company, limit),
            location=_truncate(location, limit),
            salary=_truncate(salary, limit) if salary else "",
        )
        if len(message) <= max_chars:
            return message

    log.warning(
        f"Telegram job alert exceeded {max_chars} chars after truncation; "
        f"sending minimal entry for job source_id={job.source_id}"
    )
    message = _telegram_single_job_message(
        job,
        title=_truncate(title, 24),
        company=_truncate(company, 24),
        location=_truncate(location, 24),
        salary="",
    )
    return message[:max_chars]


def _telegram_single_job_message(
    job: Job,
    *,
    title: str,
    company: str,
    location: str,
    salary: str,
) -> str:
    kw = job.score_breakdown.get("keyword", "?") if job.score_breakdown else "?"
    source = _source_label(job.source)

    lines = [
        "🎯 *NEW JOB*",
        "",
        f"*{_esc_md(title)}*",
        _esc_md(company),
        "",
        f"📍 {_esc_md(location)}",
        f"⭐ Score: {_esc_md(str(job.score))}",
        f"🔑 Keyword score: {_esc_md(str(kw))}",
        f"🌐 {_esc_md(source)}",
    ]
    if salary:
        lines.append(f"💰 {_esc_md(salary)}")
    if job.location and job.location.is_remote:
        lines.append("🏠 Remote")
    lines.extend(["", f"🔗 [View & Apply]({_esc_md_url(job.url)})"])
    return "\n".join(lines)


def _source_label(source) -> str:
    value = getattr(source, "value", str(source))
    return TELEGRAM_SOURCE_LABELS.get(value, value.title())


def _esc_md_url(value: str) -> str:
    return value.replace("\\", "\\\\").replace(")", "\\)")


def build_telegram_job_messages(
    jobs: list[Job],
    *,
    profile_name: str = "default",
    label: str = "new",
    max_chars: int = TELEGRAM_SAFE_MESSAGE_LIMIT,
) -> list[str]:
    """Build MarkdownV2 Telegram messages without splitting job blocks."""
    if not jobs:
        return []

    blocks = [_telegram_job_block(job, max_chars=max_chars) for job in jobs]
    chunks: list[list[str]] = []
    current: list[str] = []

    for block in blocks:
        candidate = [*current, block]
        if current and _telegram_message_len(
            candidate,
            profile_name=profile_name,
            label=label,
            total_jobs=len(jobs),
            chunk_index=1,
            chunk_count=1,
        ) > max_chars:
            chunks.append(current)
            current = [block]
        else:
            current = candidate

    if current:
        chunks.append(current)

    chunk_count = len(chunks)
    return [
        "\n".join(
            [
                _telegram_header(
                    profile_name=profile_name,
                    label=label,
                    total_jobs=len(jobs),
                    chunk_index=index,
                    chunk_count=chunk_count,
                ),
                *chunk,
            ]
        )
        for index, chunk in enumerate(chunks, 1)
    ]


def _telegram_message_len(
    blocks: list[str],
    *,
    profile_name: str,
    label: str,
    total_jobs: int,
    chunk_index: int,
    chunk_count: int,
) -> int:
    return len(
        "\n".join(
            [
                _telegram_header(
                    profile_name=profile_name,
                    label=label,
                    total_jobs=total_jobs,
                    chunk_index=chunk_index,
                    chunk_count=chunk_count,
                ),
                *blocks,
            ]
        )
    )


def _telegram_header(
    *,
    profile_name: str,
    label: str,
    total_jobs: int,
    chunk_index: int,
    chunk_count: int,
) -> str:
    name = (
        f"*Dev Job Radar \\({_esc_md(profile_name)}\\)*"
        if profile_name != "default"
        else "*Dev Job Radar*"
    )
    plural = "match" if total_jobs == 1 else "matches"
    suffix = f" \\({chunk_index}/{chunk_count}\\)" if chunk_count > 1 else ""
    label_text = f"{label} " if label else ""
    return f"{name} — {total_jobs} {label_text}{plural}{suffix}\n"


def _telegram_job_block(job: Job, *, max_chars: int) -> str:
    salary = job.compensation.display_concise if job.compensation else ""
    kw = job.score_breakdown.get("keyword", "?") if job.score_breakdown else "?"
    id_tag = f"\\#{job.id} " if job.id else ""

    def make_block(company: str, title: str, location: str) -> str:
        loc_line = f"  {_esc_md(location)}"
        if salary:
            loc_line += f" \\| {_esc_md(salary)}"
        return (
            f"*{job.score}* \\(kw:{kw}\\) \\| "
            f"{id_tag}[{_esc_md(company)}: {_esc_md(title)}]({job.url})\n"
            f"{loc_line}"
        )

    company = job.company
    title = job.title
    location = job.location.display
    block = make_block(company, title, location)
    if len(block) <= max_chars:
        return block

    for limit in (240, 160, 100, 60, 30):
        block = make_block(
            _truncate(company, limit),
            _truncate(title, limit),
            _truncate(location, limit),
        )
        if len(block) <= max_chars:
            return block

    block = make_block(_truncate(company, 20), _truncate(title, 20), _truncate(location, 20))
    if len(block) <= max_chars:
        return block

    log.warning(
        f"Telegram job block exceeded {max_chars} chars after truncation; "
        f"sending shortened entry for job source_id={job.source_id}"
    )
    fallback = (
        f"*{job.score}* \\(kw:{kw}\\) \\| "
        f"{id_tag}{_esc_md(_truncate(company, 20))}: {_esc_md(_truncate(title, 40))}\n"
        f"  {_esc_md(_truncate(location, 40))}\n"
        "  Link omitted because this job entry is too long for Telegram"
    )
    return fallback[:max_chars]


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: max(1, limit - 1)].rstrip() + "…"


def send_email(subject: str, body: str, cfg, attachment: Path | None = None) -> bool:
    """Send a plain-text email via Gmail SMTP, optionally with a file attachment."""
    if not cfg.username or not cfg.app_password or not cfg.to_address:
        log.error(
            "Email not configured (missing username, app_password, or to_address)"
        )
        return False

    msg = MIMEMultipart("mixed" if attachment else "alternative")
    msg["Subject"] = subject
    msg["From"] = cfg.username
    msg["To"] = cfg.to_address
    msg.attach(MIMEText(body, "plain"))

    if attachment and attachment.exists():
        part = MIMEBase("application", "octet-stream")
        part.set_payload(attachment.read_bytes())
        encoders.encode_base64(part)
        part.add_header(
            "Content-Disposition", f"attachment; filename={attachment.name}"
        )
        msg.attach(part)

    smtp = None
    try:
        smtp = smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=10)
        smtp.starttls()
        smtp.login(cfg.username, cfg.app_password)
        smtp.send_message(msg)
        log.info(f"Email sent: {subject}")
        return True
    except Exception as e:
        log.error(f"Email failed: {e}")
        return False
    finally:
        if smtp:
            try:
                smtp.quit()
            except Exception:
                pass


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _esc_md(s: str) -> str:
    """Escape special characters for Telegram MarkdownV2."""
    for ch in r"_*[]()~`>#+-=|{}.!":
        s = s.replace(ch, f"\\{ch}")
    return s


def _esc_slack(s: str) -> str:
    """Escape special characters for Slack mrkdwn."""
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _esc_discord(s: str) -> str:
    """Escape special characters for Discord markdown."""
    for ch in r"\*_~`|":
        s = s.replace(ch, f"\\{ch}")
    return s


def send_slack(text: str, cfg: SlackConfig) -> bool:
    """POST to Slack incoming webhook. Returns True on success."""
    if not cfg.webhook_url:
        log.error("Slack not configured (missing webhook_url)")
        return False

    try:
        resp = httpx.post(cfg.webhook_url, json={"text": text}, timeout=10)
        if 200 <= resp.status_code < 300:
            log.info("Slack message sent")
            return True
        log.error(f"Slack webhook returned {resp.status_code}: {resp.text}")
        return False
    except Exception as e:
        log.error(f"Slack notification failed: {e}")
        return False


def send_discord(text: str, cfg: DiscordConfig) -> bool:
    """POST to Discord webhook. Returns True on success."""
    if not cfg.webhook_url:
        log.error("Discord not configured (missing webhook_url)")
        return False

    try:
        resp = httpx.post(cfg.webhook_url, json={"content": text}, timeout=10)
        if 200 <= resp.status_code < 300:
            log.info("Discord message sent")
            return True
        log.error(f"Discord webhook returned {resp.status_code}: {resp.text}")
        return False
    except Exception as e:
        log.error(f"Discord notification failed: {e}")
        return False
