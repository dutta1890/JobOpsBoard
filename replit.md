# Career-Ops

A job search automation system for performance marketing roles built with Flask.

## Architecture

- **Framework**: Flask 3.0 + Flask-SQLAlchemy
- **Database**: SQLite (`career_ops.db`)
- **AI Integration**: Anthropic Claude (`claude-sonnet-4-20250514`)
- **Frontend**: Jinja2 templates, Tailwind CSS (CDN), HTMX (CDN)
- **Port**: 5000
- **Profile**: Aishik Dutta — 8 years performance marketing, Mumbai-based, open to relocation

## Project Structure

```
career-ops/
├── main.py                  # Flask app entry point + all routes
├── config.py                # Config class (env vars, model settings)
├── requirements.txt         # Python dependencies
├── .env.example             # Environment variable template
├── models/
│   └── database.py          # SQLAlchemy models (JobApplication, ScanHistory)
├── services/
│   ├── claude_client.py     # Anthropic API integration + job evaluation
│   ├── job_parser.py        # URL fetching + JD text extraction
│   └── dedup.py             # Fuzzy deduplication logic
├── data/
│   ├── profile.json         # User profile (skills, target roles, etc.)
│   ├── job_sources.json     # Job board URLs and keywords
│   └── companies.json       # Target companies with career URLs
├── templates/
│   ├── base.html            # Base layout (dark theme, Tailwind, HTMX)
│   ├── index.html           # Dashboard page
│   └── components/          # Reusable Jinja2 components
├── static/
│   └── js/app.js            # Frontend JS (evaluate, save, render results)
└── tracker/                 # Scan history files directory
```

## Database Models

- **JobPosting** — Deduplication table; keyed by SHA-256 hash of `normalize(company|role|location)`. Tracks `times_seen` and accumulates duplicate URLs.
- **Application** — Main tracker; linked to `JobPosting`. Stores score (0–5), grade (A–F), recommendation, visa_sponsorship, evaluation JSON, and status lifecycle.
- **ScanHistory** — Records each scanned URL and its result (new / duplicate / skipped).

Helper functions in `models/database.py`: `create_job_hash()`, `check_duplicate()`, `record_job_posting()`, `get_or_create_posting()`.

## Key Routes

- `GET /` — Dashboard with stats + applications tracker table
- `POST /evaluate` — Evaluate job description via Claude API (JSON)
- `POST /api/save-job` — Save evaluated job; deduplicates via `get_or_create_posting()`
- `GET /api/profile` — Returns full `data/profile.json` as JSON
- `GET /api/health` — Health check (DB status + timestamp)

## Environment Variables

Set `ANTHROPIC_API_KEY` to enable AI evaluations. See `.env.example`.

## Running

The app runs via the "Start application" workflow (`python main.py`) on port 5000.
