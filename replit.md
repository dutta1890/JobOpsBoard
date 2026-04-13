# Career-Ops

A job search automation system for Aishik Dutta — 8-year performance marketing professional based in Mumbai, open to relocation (India, UAE, Singapore, Europe).

## Architecture

- **Framework**: Flask 3.0 + Flask-SQLAlchemy
- **Database**: SQLite (`instance/career_ops.db`)
- **AI Integration**: Google Gemini (`gemini-2.5-flash`) via `google-genai` SDK
- **Frontend**: Jinja2 templates, Tailwind CSS (CDN), HTMX (CDN)
- **Port**: 5000
- **Auth**: Session-based with PBKDF2 password hashing, 24h timeout, rate-limited login

## Project Structure

```
career-ops/
├── main.py                      # Flask app entry point + all routes
├── config.py                    # Config class (env vars, model settings)
├── requirements.txt             # Python dependencies
├── models/
│   └── database.py              # SQLAlchemy models: JobPosting, Application, ScanHistory
├── services/
│   ├── gemini_client.py         # Gemini API: evaluate_job, generate_cv, generate_cover_letter
│   ├── claude_client.py         # Core evaluate_job logic (delegates to Gemini)
│   ├── agent.py                 # Background autonomous agent (5 methods, queue management)
│   ├── security.py              # Auth: login_required, rate_limit, sanitize_input, headers
│   ├── audit_logger.py          # JSONL audit log → logs/audit_YYYYMMDD.jsonl
│   ├── backup.py                # Daily SQLite + data/ zip backups → backups/
│   ├── interview_prep.py        # STAR+R story bank → data/star_stories.json
│   ├── pdf_generator.py         # ATS PDF CV via Playwright → output/pdfs/
│   ├── legitimacy_checker.py    # 7-layer URL trust scoring (WHOIS, SSL, typosquat)
│   ├── scam_detector.py         # JD scam detection (instant block + warning flags)
│   ├── email_applicator.py      # Gmail SMTP application sender (CV attachment)
│   ├── email_tracker.py         # IMAP inbox scanner (interview/rejection/reply classifier)
│   ├── form_applicator.py       # Playwright: Google Forms + generic web form filler
│   ├── job_parser.py            # URL fetching + JD text extraction
│   ├── dedup.py                 # 3-layer deduplication (URL → hash → fuzzy 80%/85%)
│   └── sources.py               # Job board source management
├── data/
│   ├── profile.json             # Aishik's skills, target roles, preferences
│   ├── job_sources.json         # Job board URLs and search keywords
│   ├── companies.json           # Target companies with career portal URLs
│   ├── star_stories.json        # Generated STAR interview stories (auto-created)
│   └── cv.pdf                   # CV file for email attachments (add manually)
├── templates/
│   └── index.html               # Single-page dashboard (dark theme)
├── static/
│   └── js/app.js                # Frontend JS (evaluate, save, render results)
├── logs/                        # Audit logs (audit_YYYYMMDD.jsonl, auto-created)
├── backups/                     # Rolling 7-day DB + data backups (auto-created)
└── output/pdfs/                 # Generated PDF CVs (auto-created)
```

## Key Services

### Job Evaluation Pipeline (4-layer gate)
1. **Layer 0 — URL legitimacy**: Trusted-list → IP-based → SSL → domain age (WHOIS) → suspicious TLD → typosquatting
2. **Layer 0b — Scam detection**: Instant block (money requests, personal data, guaranteed income, known scam jobs) + warning flags (WhatsApp-only, personal email, urgency, vague JD)
3. **Layer 1 — Pre-filter**: Age >7 days, junior/intern/fresher/analyst titles, 0–3yr experience
4. **Layer 2 — Gemini scoring**: 10-dimension evaluation (role_match, channel_fit, budget_scale, team_scope, compensation, geographic, company_stage, brand_quality, growth_path, interview_likelihood)

### Autonomous Agent
Background thread with 5 application methods: `EMAIL`, `GOOGLE_FORM`, `WEB_FORM`, `NAUKRI`, `INDEED`. LinkedIn skipped silently. Naukri/Indeed pause as `WAITING_AUTH`.

### Audit Logging
All events written to `logs/audit_YYYYMMDD.jsonl`: login attempts, job evaluations, applications sent, scam detections, agent actions.

### Backups
Daily daemon thread copies SQLite DB and zips `data/` to `backups/`. Keeps last 7.

## Routes

### Dashboard & Auth
- `GET /` — Dashboard (protected)
- `GET|POST /login` — Login (rate-limited)
- `GET /logout`

### Job Evaluation & Tracking
- `POST /evaluate` — Evaluate JD via 4-layer pipeline
- `POST /api/save-job` — Save evaluated job with 3-layer dedup
- `GET /api/profile` — Return profile.json
- `GET /api/sources` — Job board sources (by region/category)

### CV & Cover Letter Generation
- `POST /api/generate-cv` — AI-tailored CV content (JSON)
- `POST /generate-pdf/<id>` — Generate PDF CV, returns download link
- `GET /download/<filename>` — Download generated PDF
- `POST /api/generate-cover` — AI cover letter (JSON)
- `POST /generate-cover/<id>` — Cover letter for saved application (HTMX)

### Email Applications
- `POST /api/apply-email` — Send application email with CV
- `GET /api/email-test` — Test SMTP connection
- `POST /check-emails` — Scan inbox for interview/rejection replies
- `GET /api/email-summary` — Inbox stats (HTMX)

### Form Applications
- `POST /api/form-apply` — Submit Google Form or web form via Playwright

### Agent Control
- `POST /agent/start` / `POST /agent/stop` / `GET /agent/status`
- `POST /agent/queue/<id>` — Queue single job
- `POST /agent/queue-grade-a` / `POST /agent/queue-grade-b` — Bulk queue
- `POST /agent/resume/<task_id>` — Resume WAITING_AUTH task

### Interview Prep
- `GET /api/interview/stories` — List all STAR stories (or search with `?q=question`)
- `POST /api/interview/generate` — Generate STAR+R story from achievement

### System
- `GET /api/health` — DB status + timestamp (public)

## Environment Variables (Replit Secrets)

| Secret | Purpose |
|---|---|
| `GEMINI_API_KEY` | Google Gemini AI evaluation |
| `DASHBOARD_PASSWORD` | Login password |
| `FLASK_SECRET_KEY` | Session signing key (64+ chars recommended) |
| `EMAIL_ADDRESS` | Gmail address for sending applications |
| `EMAIL_APP_PASSWORD` | Gmail App Password (not main password) |

## Database Models

- **JobPosting** — Dedup table; keyed by SHA-256 of `normalize(company|role|location)`. Tracks `times_seen`.
- **Application** — Main tracker: score, grade (A–F), recommendation, visa_sponsorship, status lifecycle (Evaluated → Applied → Interview → Offer/Rejected).
- **ScanHistory** — Every scanned URL with result (new/duplicate/skipped).

## Running

Workflow: `python main.py` on port 5000. Flask debug mode with Werkzeug stat reloader.

## Important Notes

- `data/cv.pdf` must be placed manually for email applications to include the CV attachment. Without it, emails send without attachment (graceful degradation).
- `FLASK_SECRET_KEY` should be set as a persistent Replit secret — if auto-generated, all sessions invalidate on restart.
- The scam detector and legitimacy checker run before Gemini, saving API quota.
- Playwright is required for PDF generation and form applications (`playwright install chromium`).
