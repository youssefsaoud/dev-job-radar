# Job Monitoring Application

This project is a Python job-monitoring application for finding relevant software-development opportunities. It searches configured job sources, scores postings against a technical profile, deduplicates repeated results, persists jobs locally, and can send notifications through optional channels.

This customized version is currently Windows-first for local use. The Python package is `dev_job_radar` and the CLI command is `dev-job-radar`.

## What It Does

- Collects job postings from configured sources.
- Scores each job against profile keywords, title signals, company preferences, dealbreakers, and posting recency.
- Stores scraped jobs in a local SQLite database.
- Detects duplicates by source identifiers and, when enough description content exists, by normalized job content.
- Lets you review, rescore, export, mark jobs as applied, and generate digests or reports.
- Supports optional notifications by macOS notification, Telegram, email, Slack, and Discord.

## Architecture

- Language: Python 3.12+
- CLI entry point: `dev-job-radar`, mapped to `dev_job_radar.cli:app`
- Source package: `src/dev_job_radar`
- Configuration: YAML, usually `config.yaml`
- Persistence: SQLite through the local `JobDB` layer
- Scrapers: one scraper module per supported job source under `src/dev_job_radar/scrapers`
- Scoring: `src/dev_job_radar/scorer.py`
- Notifications: `src/dev_job_radar/notify.py`
- Scheduling: `src/dev_job_radar/scheduler.py`

## Supported Sources

The application includes scraper implementations for:

- LinkedIn
- Indeed
- Google Jobs
- Glassdoor
- ZipRecruiter
- Bayt

Configure the active sources in `search.sites`.

## Windows Setup

From PowerShell in the project directory:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e .
dev-job-radar check
dev-job-radar scrape --dry-run
```

If PowerShell blocks virtual environment activation, either adjust your execution policy for the current user or run commands through the venv executable directly:

```powershell
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\pip.exe install -e .
.\.venv\Scripts\dev-job-radar.exe check
.\.venv\Scripts\dev-job-radar.exe scrape --dry-run
```

Use `dev-job-radar init` to generate a starter `config.yaml` if one does not already exist.

## macOS-Specific Notes

The built-in scheduler uses macOS `launchd`. Do not use `dev-job-radar schedule --install` on Windows. For Windows automation, use Task Scheduler or another external scheduler after you are satisfied with manual dry runs.

The original setup script is shell/macOS-oriented. On Windows, prefer the PowerShell commands above.

## Configuration

A minimal configuration has these main sections:

- `profile`: your name, target title, scoring keywords, title signals, company tiers, target levels, and dealbreakers.
- `search`: search terms, locations, enabled sites, result limit, distance, and recency window.
- `scoring`: minimum score thresholds for display and alerts.
- `notifications`: optional notification channels.
- `schedule`: interval settings used by the macOS scheduler.

Example search section:

```yaml
search:
  terms:
    - "java developer"
    - "backend developer"
    - "spring boot"
  locations:
    - "Morocco"
  sites:
    - linkedin
    - indeed
  results_per_site: 25
  hours_old: 24
```

Run validation before scraping:

```powershell
dev-job-radar check
```

## Scoring

Jobs are scored from several signals:

- Keyword matches from `profile.keywords`.
- The best matching title signal from `profile.title_signals`.
- Target company tier matches.
- Recency of the posting.
- Dealbreaker patterns, which can force a score of zero.

Keyword tiers are weighted differently. Critical keywords are the strongest profile signal, strong keywords only fully matter once at least one critical keyword is present, and moderate or weak keywords add smaller amounts.

Use:

```powershell
dev-job-radar rescore
```

after changing scoring-related configuration.

## Deduplication

The database prevents exact duplicates by source and source-specific job ID. It can also detect content duplicates when enough description text is available, using normalized job content such as title, company, location, date, and description.

Preview duplicate cleanup:

```powershell
dev-job-radar dedup --dry-run
```

Apply duplicate cleanup:

```powershell
dev-job-radar dedup
```

## Notifications

Supported notification channels are:

- macOS local notifications
- Telegram bot messages
- Email through SMTP
- Slack incoming webhooks
- Discord webhooks

For a first Windows dry run, keep all notification channels disabled and use:

```powershell
dev-job-radar scrape --dry-run
```

Enable notification channels only after the search and scoring configuration are working as expected. Store tokens and passwords carefully; the app reads them from local configuration and does not require them for dry-run scraping.

## Common Commands

```powershell
dev-job-radar check                # validate config
dev-job-radar scrape --dry-run     # test scraping without saving
dev-job-radar scrape               # scrape and persist new jobs
dev-job-radar list                 # list recent matches
dev-job-radar view 42              # view one job
dev-job-radar apply 42             # mark a job as applied
dev-job-radar rescore              # rescore persisted jobs
dev-job-radar dedup --dry-run      # preview duplicate cleanup
dev-job-radar stats                # show search stats
dev-job-radar digest               # send a digest through enabled notifications
dev-job-radar report               # generate a report
dev-job-radar export -o jobs.csv   # export jobs to CSV or JSON
```

## Multiple Configurations

Use `--config` to run separate searches with separate configuration and data paths:

```powershell
dev-job-radar --config backend.yaml check
dev-job-radar --config backend.yaml scrape --dry-run
dev-job-radar --config backend.yaml scrape
```

You can set an explicit profile/config name:

```yaml
config_name: backend-jobs
```

## Troubleshooting

`dev-job-radar check` reports configuration errors:
Follow the validation output and fix the referenced YAML fields.

Scrapers return no results:
Job boards can rate-limit, change markup, or return empty results for a specific query/location/date window. Try a broader query, a wider `hours_old` value, or a different source.

Notifications do not send:
Confirm the channel is enabled and that its token, webhook, SMTP host, username, and password are correct.

Scheduling on Windows:
The built-in scheduler is macOS-specific. Use Windows Task Scheduler only after manual commands work reliably.

## License

This project is distributed under the MIT License. See `LICENSE`.

## Attribution

Some scraper implementations retain attribution to [JobSpy](https://github.com/speedyapply/JobSpy), which is MIT licensed. The JobSpy attribution comments in the scraper source files are intentionally preserved.
