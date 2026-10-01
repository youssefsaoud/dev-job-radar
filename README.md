# Dev Job Radar

Dev Job Radar is a Python CLI application for monitoring software-development job postings. It searches configured job boards, scores each posting against a technical profile, deduplicates repeated listings, stores results in SQLite, and can notify the user when strong matches appear.

This customized version is focused on junior Java / Spring Boot backend opportunities, but the search terms, locations, scoring profile, sources, thresholds, and notification channels are configurable.

## Highlights

- Scrapes job postings from LinkedIn and Indeed, with additional scraper modules for Google Jobs, Glassdoor, ZipRecruiter, and Bayt.
- Supports multiple search terms and locations, so broad role names like `backend developer`, `java developer`, and `développeur java` can run in one pass.
- Scores postings with configurable keyword tiers, title signals, company tiers, recency, and dealbreaker rules.
- Tracks jobs in a local SQLite database with status fields for new, applied, rejected, interview, offer, and filtered jobs.
- Deduplicates by source job ID and by normalized job content when enough description text is available.
- Includes a dry-run mode for safely testing scrapers and scoring without writing to the database.
- Provides CLI commands for scraping, listing, viewing, applying, rejecting, rescoring, exporting, reporting, deduplication, and notification digests.
- Supports optional notifications through Telegram, email, Slack, Discord, and macOS notifications.

## Tech Stack

- Python 3.12+
- Typer CLI
- Pydantic configuration models
- HTTPX for HTTP requests
- BeautifulSoup for HTML parsing
- Rich terminal output
- PyYAML configuration
- SQLite persistence
- Pytest, respx, and pytest-asyncio for tests
- Hatchling build backend

## Architecture / How It Works

```text
config.yaml
   |
   v
CLI command: dev-job-radar scrape
   |
   v
Scrapers: LinkedIn, Indeed, Google Jobs, Glassdoor, ZipRecruiter, Bayt
   |
   v
Job normalization into shared Job models
   |
   v
Profile scoring and dealbreaker filtering
   |
   v
SQLite persistence and duplicate detection
   |
   v
CLI review, export/report generation, and optional notifications
```

The main package is `dev_job_radar`. The console entry point is `dev-job-radar`, mapped to `dev_job_radar.cli:app`.

## Supported Sources

The active sources are configured in `search.sites`.

```yaml
search:
  sites:
    - linkedin
    - indeed
```

Implemented scraper modules currently exist for:

- LinkedIn
- Indeed
- Google Jobs
- Glassdoor
- ZipRecruiter
- Bayt

LinkedIn applies the configured `search.hours_old` recency window. Indeed currently omits the server-side `dateOnIndeed` filter intentionally because that mobile GraphQL filter can under-return results for Morocco; recency can still be evaluated from parsed posting dates when Indeed provides them.

## Windows Setup

From PowerShell in the project directory:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e .
dev-job-radar init
dev-job-radar check
dev-job-radar scrape --dry-run
```

If PowerShell blocks virtual environment activation, run through the virtual environment executables directly:

```powershell
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\pip.exe install -e .
.\.venv\Scripts\dev-job-radar.exe init
.\.venv\Scripts\dev-job-radar.exe check
.\.venv\Scripts\dev-job-radar.exe scrape --dry-run
```

Use `dev-job-radar init --full` to generate the full configuration template instead of the minimal one.

## Configuration

The main configuration file is YAML. By default, the CLI resolves `config.yaml` from the app config location or current working directory; you can also pass a specific file with `--config`.

Important sections:

- `profile`: name, target title, keyword tiers, title signals, company tiers, target levels, and dealbreakers.
- `search`: search terms, locations, enabled sites, result limit, distance, and recency window.
- `scraping`: request timing, retries, page limits, proxies, TLS fingerprinting option, and concurrency.
- `scoring`: display and alert thresholds.
- `notifications`: optional macOS, email, Telegram, Slack, and Discord settings.
- `schedule`: launchd schedule settings for macOS.

Example search profile:

```yaml
profile:
  name: "Youssef"
  target_title: "Junior Java / Spring Boot Backend Developer"
  keywords:
    critical:
      - "java"
      - "spring boot"
    strong:
      - "spring"
      - "backend"
      - "rest api"
      - "jpa"

search:
  terms:
    - "java developer"
    - "backend developer"
    - "spring boot"
    - "développeur java"
  locations:
    - "Morocco"
  sites:
    - linkedin
    - indeed
  results_per_site: 25
  hours_old: 24
```

Validate before scraping:

```powershell
dev-job-radar check
```

## Example CLI Session

```powershell
dev-job-radar --config .\config.yaml check
dev-job-radar --config .\config.yaml scrape --dry-run --site linkedin --term "java developer"
dev-job-radar --config .\config.yaml scrape
dev-job-radar --config .\config.yaml list --min-score 55
dev-job-radar --config .\config.yaml view 42
dev-job-radar --config .\config.yaml apply 42 --notes "Applied through company site"
```

`--dry-run` scrapes and scores results without persisting jobs or sending notifications, which makes it useful for tuning search terms and scoring rules.

## Scoring

Jobs are scored with several signals:

- Keyword matches from `profile.keywords`.
- The best matching rule from `profile.title_signals`.
- Target company tier matches.
- Posting recency when a date is available.
- Dealbreaker patterns, which can force a job to score zero.

After changing scoring-related configuration, rescore stored jobs:

```powershell
dev-job-radar rescore
```

## Deduplication and Tracking

The database prevents duplicate records for the same source and source-specific job ID. It also computes a normalized content fingerprint from title, company, location, posting date, and description text when enough content is available.

Useful commands:

```powershell
dev-job-radar list
dev-job-radar view 42
dev-job-radar apply 42
dev-job-radar reject 42
dev-job-radar interview 42
dev-job-radar offer 42
dev-job-radar dedup --dry-run
dev-job-radar dedup
```

## Notifications

Notifications are optional. Supported channels:

- macOS local notifications
- Telegram Bot API
- Email through SMTP
- Slack incoming webhooks
- Discord webhooks

For a first Windows run, keep notifications disabled and use `scrape --dry-run`. Store tokens, webhooks, and email app passwords only in local config files; `config.yaml` is ignored by Git.

You can send a digest through enabled channels:

```powershell
dev-job-radar digest
```

## Scheduling

The built-in scheduler manages macOS `launchd` plists for scrape, digest, and report tasks:

```powershell
dev-job-radar schedule
dev-job-radar schedule --install
dev-job-radar schedule --uninstall
```

Do not use `schedule --install` on Windows. For Windows automation, use Windows Task Scheduler or another external scheduler after manual dry runs are working.

## Reporting and Export

```powershell
dev-job-radar stats
dev-job-radar report
dev-job-radar export --output jobs.csv
dev-job-radar export --output jobs.json
```

Reports summarize stored jobs and recent search activity. Exports support CSV or JSON, inferred from the output extension unless `--format` is provided.

## Multiple Configurations

Use `--config` to run separate searches with separate data paths:

```powershell
dev-job-radar --config .\backend.yaml check
dev-job-radar --config .\backend.yaml scrape --dry-run
dev-job-radar --config .\backend.yaml scrape
```

You can set a stable profile/config identity:

```yaml
config_name: backend-jobs
```

## Troubleshooting

`dev-job-radar check` reports configuration errors:
Fix the YAML fields shown in the validation output.

Scrapers return no results:
Job boards can rate-limit, change markup, geofence results, or return different data through unofficial/mobile endpoints than through the public website. Try a broader search term, a different location, or another source.

Notifications do not send:
Confirm the channel is enabled and that the required token, webhook, SMTP host, username, password, and destination fields are present.

Scheduling on Windows:
The built-in scheduler is macOS-specific. Use Windows Task Scheduler only after manual commands work reliably.

## License

This project is distributed under the MIT License. See `LICENSE`.

## Attribution

Some scraper implementations retain attribution to [JobSpy](https://github.com/speedyapply/JobSpy), which is MIT licensed. The JobSpy attribution comments in the scraper source files are intentionally preserved.
