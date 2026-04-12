import json
import logging
import os
from datetime import datetime, date

from flask import Flask, jsonify, render_template, request
from models.database import (
    db,
    Application,
    JobPosting,
    ScanHistory,
)
from config import Config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

PROFILE_PATH = os.path.join(os.path.dirname(__file__), "data", "profile.json")


def load_profile() -> dict:
    try:
        with open(PROFILE_PATH) as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"Could not load profile.json: {e}")
        return {}


def load_json(path: str) -> dict:
    try:
        full_path = os.path.join(os.path.dirname(__file__), path)
        with open(full_path) as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"Could not load {path}: {e}")
        return {}


def _score_to_grade(score: float | None) -> str | None:
    if score is None:
        return None
    if score >= 4.5:
        return "A"
    if score >= 4.0:
        return "B"
    if score >= 3.5:
        return "C"
    if score >= 3.0:
        return "D"
    return "F"


def render_evaluation_card(result: dict, app_id: int) -> str:
    """Render an evaluation result as an HTML card fragment (for HTMX / server-side use)."""
    grade = result.get("grade", "F")
    grade_css = {
        "A": "border-green-500 bg-green-950/40",
        "B": "border-blue-500 bg-blue-950/40",
        "C": "border-yellow-500 bg-yellow-950/40",
        "D": "border-orange-500 bg-orange-950/40",
        "F": "border-red-600 bg-red-950/40",
    }.get(grade, "border-gray-600 bg-gray-900")

    visa = result.get("visa_sponsorship", "unclear")
    visa_badge = {
        "yes":    '<span class="px-2 py-0.5 bg-green-900 text-green-300 rounded text-xs">✓ Visa Sponsored</span>',
        "no":     '<span class="px-2 py-0.5 bg-red-900 text-red-300 rounded text-xs">✗ No Visa</span>',
        "unclear":'<span class="px-2 py-0.5 bg-yellow-900 text-yellow-300 rounded text-xs">? Visa Unclear</span>',
    }.get(visa, "")

    dims = result.get("dimensions", {})
    dim_html = '<div class="grid grid-cols-5 gap-2 mt-3 text-xs">'
    for key, label in [
        ("role_match", "Role"), ("channel_fit", "Channel"),
        ("budget_scale", "Budget"), ("compensation", "Comp"),
        ("interview_likelihood", "Interview"),
    ]:
        s = dims.get(key, {}).get("score", "—")
        color = "text-green-400" if isinstance(s, (int, float)) and s >= 4 \
            else "text-yellow-400" if isinstance(s, (int, float)) and s >= 3 \
            else "text-red-400"
        dim_html += f'<div class="{color} text-center"><div class="font-semibold">{s}</div><div class="text-gray-500">{label}</div></div>'
    dim_html += "</div>"

    skip = f'<p class="text-red-400 text-xs mt-2">Skip reason: {result["skip_reason"]}</p>' \
        if result.get("skip_reason") else ""

    return f"""
    <div class="rounded-xl border-l-4 {grade_css} p-5 mt-4">
      <div class="flex justify-between items-start">
        <div>
          <h3 class="text-lg font-semibold text-white">{result.get('company','Unknown')}</h3>
          <p class="text-gray-300 text-sm">{result.get('role','')}</p>
          <p class="text-gray-500 text-xs">{result.get('location','')}</p>
        </div>
        <div class="text-right shrink-0 ml-4">
          <div class="text-4xl font-bold text-white">{result.get('score',0):.2f}</div>
          <div class="text-xl font-bold mt-0.5 {'text-green-400' if grade=='A' else 'text-blue-400' if grade=='B' else 'text-yellow-400' if grade=='C' else 'text-red-400'}">Grade {grade}</div>
        </div>
      </div>
      <div class="flex flex-wrap gap-2 mt-3">
        <span class="px-2 py-0.5 bg-purple-900 text-purple-300 rounded text-xs">{result.get('archetype','').replace('_',' ')}</span>
        <span class="px-2 py-0.5 bg-gray-700 text-gray-300 rounded text-xs">{result.get('recommendation','')}</span>
        {visa_badge}
      </div>
      {dim_html}
      {skip}
      <p class="text-xs text-green-400 mt-3">✓ Saved to tracker (id={app_id})</p>
    </div>"""


def render_duplicate_warning(result: dict, existing, dup_type: str) -> str:
    """Render a duplicate-detected warning as an HTML fragment."""
    first_seen = getattr(existing, "first_seen_date", None)
    first_seen_str = first_seen.strftime("%b %d, %Y") if first_seen else "Unknown"
    return f"""
    <div class="rounded-lg border border-yellow-600 bg-yellow-950/40 p-4 mt-4">
      <h3 class="text-yellow-300 font-semibold">⚠️ Duplicate Detected</h3>
      <p class="text-yellow-200 text-sm mt-1">
        {result.get('company','?')} — {result.get('role','?')} has already been evaluated.
      </p>
      <p class="text-yellow-500 text-xs mt-1">
        Type: {dup_type.replace('_',' ')} · First seen: {first_seen_str}
      </p>
    </div>"""


def render_error(message: str) -> str:
    """Render an error message as an HTML fragment."""
    return f"""
    <div class="rounded-lg border border-red-600 bg-red-950/40 p-4 mt-4">
      <p class="text-red-300 text-sm">{message}</p>
    </div>"""


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    db.init_app(app)

    with app.app_context():
        db.create_all()
        logger.info("Database tables created/verified.")

    @app.route("/")
    def index():
        applications = Application.query.order_by(
            Application.created_at.desc()
        ).all()

        total = len(applications)
        applied = sum(1 for a in applications if a.status == "Applied")
        interviews = sum(1 for a in applications if a.status == "Interview")
        scores = [a.score for a in applications if a.score is not None]
        avg_score = round(sum(scores) / len(scores), 1) if scores else 0

        stats = {
            "total": total,
            "applied": applied,
            "interviews": interviews,
            "avg_score": avg_score,
        }

        return render_template(
            "index.html",
            applications=applications,
            stats=stats,
            today=date.today().strftime("%B %d, %Y"),
        )

    @app.route("/evaluate", methods=["POST"])
    def evaluate():
        try:
            body = request.get_json(silent=True) or {}
            jd_text = (body.get("jd_text") or "").strip()
            platform = (body.get("platform") or "manual").strip()
            # Optional metadata for pre-filtering (posted_date, title, experience_required)
            job_metadata = body.get("job_metadata") or None

            if not jd_text:
                return jsonify({"success": False, "error": "No job description provided."}), 400

            from services.job_parser import parse_job_input
            from services.gemini_client import evaluate_job, pre_filter_job
            from services.dedup import is_duplicate as check_dup
            from models.database import _normalize

            # ── Pre-filter gate (age + seniority) before any API call ─────────
            if job_metadata:
                pf = pre_filter_job(job_metadata)
                if not pf["proceed"]:
                    logger.info(f"[pre-filter] Blocked before API: {pf['skip_reason']}")
                    return jsonify({
                        "success": False,
                        "filtered": True,
                        "filter_type": pf["filter_type"],
                        "error": pf["skip_reason"],
                    }), 200

            parsed = parse_job_input(jd_text)
            if not parsed["success"]:
                return jsonify({"success": False, "error": parsed.get("error", "Failed to parse input.")}), 400

            text = parsed["text"]
            url = parsed.get("url")

            # ── Layer 1 pre-flight: skip Gemini entirely if URL already seen ──
            if url:
                from models.database import ScanHistory as _SH
                if _SH.query.filter_by(url=url).first():
                    return jsonify({
                        "success": False,
                        "duplicate": True,
                        "duplicate_type": "exact_url",
                        "error": "This URL has already been evaluated and saved.",
                    }), 200

            result = evaluate_job(text)

            if not result["success"]:
                return jsonify({"success": False, "error": result.get("error", "Evaluation failed.")}), 500

            data = result["data"]
            company = data.get("company", "Unknown")
            role = data.get("role", "Unknown")
            location = data.get("location", "")

            # ── Layers 2 & 3: dedup check then auto-save ──────────────────────
            dedup = check_dup(url or "", company, role, location)

            if dedup["is_duplicate"]:
                existing = dedup["existing_posting"]
                dup_type = dedup["duplicate_type"]
                times_seen = getattr(existing, "times_seen", "?") if existing else "?"
                dup_messages = {
                    "exact_url":   "This exact URL has already been saved.",
                    "hash_match":  f"This job was already evaluated (seen {times_seen}×).",
                    "fuzzy_match": "A very similar job already exists in your tracker.",
                }
                existing_app_id = (
                    existing.applications[0].id
                    if existing and existing.applications
                    else None
                )
                return jsonify({
                    "success": True,
                    "data": data,
                    "saved": False,
                    "duplicate": True,
                    "duplicate_type": dup_type,
                    "duplicate_message": dup_messages.get(dup_type, "Duplicate detected."),
                    "existing_id": existing_app_id,
                    "meta": {
                        "company": company, "role": role,
                        "location": location, "url": url,
                    },
                    "jd_text": text,
                })

            # Auto-save: new job — create records
            posting = JobPosting(
                job_hash=dedup["job_hash"],
                company_normalized=_normalize(company),
                role_normalized=_normalize(role),
                location=location,
                first_seen_url=url,
                first_seen_platform=platform,
                first_seen_date=datetime.utcnow(),
                duplicate_urls="[]",
                times_seen=1,
            )
            db.session.add(posting)
            db.session.flush()

            score_raw = data.get("score")
            score = float(score_raw) if score_raw is not None else None
            grade = data.get("grade") or _score_to_grade(score)

            application = Application(
                job_posting_id=posting.id,
                company=company,
                role=role,
                location=location,
                url=url,
                platform=platform,
                score=score,
                grade=grade,
                archetype=data.get("archetype"),
                recommendation=data.get("recommendation"),
                visa_sponsorship=data.get("visa_sponsorship", "unclear"),
                evaluation_json=json.dumps(data),
                jd_text=text,
                status="Evaluated",
            )
            db.session.add(application)

            if url:
                if not ScanHistory.query.filter_by(url=url).first():
                    db.session.add(ScanHistory(
                        url=url,
                        platform=platform,
                        scanned_at=datetime.utcnow(),
                        result="new",
                    ))

            db.session.commit()

            return jsonify({
                "success": True,
                "data": data,
                "saved": True,
                "duplicate": False,
                "app_id": application.id,
                "meta": {
                    "company": company, "role": role,
                    "location": location, "url": url,
                    "title": parsed.get("title", ""),
                },
                "jd_text": text,
            })

        except Exception as e:
            logger.exception("Unhandled error in /evaluate")
            db.session.rollback()
            return jsonify({"success": False, "error": "Internal server error."}), 500

    @app.route("/api/save-job", methods=["POST"])
    def save_job():
        try:
            body = request.get_json(silent=True) or {}
            data = body.get("data", {})
            meta = body.get("meta", {})
            jd_text = body.get("jd_text", "")

            company = (meta.get("company") or data.get("company") or "Unknown").strip()
            role = (meta.get("role") or data.get("role") or "Unknown").strip()
            location = (meta.get("location") or data.get("location") or "").strip()
            url = meta.get("url") or data.get("url")
            platform = meta.get("platform", "manual")

            # ── 3-layer deduplication ──────────────────────────────────────
            from services.dedup import is_duplicate as check_dup
            dedup = check_dup(url or "", company, role, location)

            if dedup["is_duplicate"]:
                existing = dedup["existing_posting"]
                dup_type = dedup["duplicate_type"]
                times_seen = getattr(existing, "times_seen", "?") if existing else "?"
                msg = {
                    "exact_url": "This exact URL has already been saved.",
                    "hash_match": f"This job (normalised match) has been seen {times_seen}x already.",
                    "fuzzy_match": "A very similar job already exists in your tracker.",
                }.get(dup_type, "Duplicate detected.")
                return jsonify({
                    "success": False,
                    "duplicate": True,
                    "duplicate_type": dup_type,
                    "error": msg,
                    "existing_id": existing.applications[0].id
                    if existing and existing.applications
                    else None,
                })
            # ──────────────────────────────────────────────────────────────

            # Create JobPosting record
            from models.database import _normalize
            posting = JobPosting(
                job_hash=dedup["job_hash"],
                company_normalized=_normalize(company),
                role_normalized=_normalize(role),
                location=location,
                first_seen_url=url,
                first_seen_platform=platform,
                first_seen_date=datetime.utcnow(),
                duplicate_urls="[]",
                times_seen=1,
            )
            db.session.add(posting)
            db.session.flush()

            score_raw = data.get("score")
            score = float(score_raw) if score_raw is not None else None
            grade = data.get("grade") or _score_to_grade(score)

            application = Application(
                job_posting_id=posting.id,
                company=company,
                role=role,
                location=location,
                url=url,
                platform=platform,
                score=score,
                grade=grade,
                archetype=data.get("archetype"),
                recommendation=data.get("recommendation"),
                visa_sponsorship=data.get("visa_sponsorship", "unclear"),
                evaluation_json=json.dumps(data),
                jd_text=jd_text,
                status="Evaluated",
            )
            db.session.add(application)

            # Record URL in ScanHistory so Layer-1 catches it on future saves
            if url:
                existing_scan = ScanHistory.query.filter_by(url=url).first()
                if not existing_scan:
                    db.session.add(ScanHistory(
                        url=url,
                        platform=platform,
                        scanned_at=datetime.utcnow(),
                        result="new",
                    ))

            db.session.commit()
            return jsonify({"success": True, "id": application.id})

        except Exception as e:
            logger.exception("Unhandled error in /api/save-job")
            db.session.rollback()
            return jsonify({"success": False, "error": "Failed to save job."}), 500

    @app.route("/api/generate-cv", methods=["POST"])
    def generate_cv_route():
        try:
            body = request.get_json(silent=True) or {}
            jd_text = (body.get("jd_text") or "").strip()
            evaluation = body.get("evaluation") or {}
            app_id = body.get("app_id")

            if not jd_text:
                return jsonify({"success": False, "error": "No job description provided."}), 400

            from services.gemini_client import generate_cv

            # If app_id provided, fetch evaluation from DB
            if app_id and not evaluation:
                app_record = Application.query.get(app_id)
                if app_record:
                    evaluation = app_record.get_evaluation()
                    jd_text = jd_text or (app_record.jd_text or "")

            result = generate_cv(jd_text, evaluation)

            if "error" in result:
                return jsonify({"success": False, "error": result["error"]}), 500

            # Persist generated CV to the application record if app_id given
            if app_id:
                app_record = Application.query.get(app_id)
                if app_record:
                    app_record.cv_generated = True
                    db.session.commit()

            return jsonify({"success": True, "cv": result})

        except Exception as e:
            logger.exception("Unhandled error in /api/generate-cv")
            return jsonify({"success": False, "error": "CV generation failed."}), 500

    @app.route("/api/generate-cover", methods=["POST"])
    def generate_cover_route():
        try:
            body = request.get_json(silent=True) or {}
            jd_text = (body.get("jd_text") or "").strip()
            evaluation = body.get("evaluation") or {}
            app_id = body.get("app_id")

            if not jd_text and not app_id:
                return jsonify({"success": False, "error": "No job description or app_id provided."}), 400

            from services.gemini_client import generate_cover_letter

            if app_id:
                app_record = Application.query.get(app_id)
                if not app_record:
                    return jsonify({"success": False, "error": "Application not found."}), 404
                jd_text = jd_text or (app_record.jd_text or "")
                evaluation = evaluation or app_record.get_evaluation()

            result = generate_cover_letter(jd_text, evaluation)

            if "error" in result:
                return jsonify({"success": False, "error": result["error"]}), 500

            if app_id:
                app_record = Application.query.get(app_id)
                if app_record:
                    app_record.cover_generated = True
                    db.session.commit()

            return jsonify({"success": True, "cover": result})

        except Exception as e:
            logger.exception("Unhandled error in /api/generate-cover")
            return jsonify({"success": False, "error": "Cover letter generation failed."}), 500

    @app.route("/generate-cover/<int:app_id>", methods=["POST"])
    def gen_cover_html(app_id):
        """HTMX-compatible endpoint — returns an HTML fragment."""
        from services.gemini_client import generate_cover_letter

        application = Application.query.get(app_id)
        if not application:
            return '<div class="text-red-400 text-sm p-2">Application not found.</div>'

        evaluation = application.get_evaluation()
        cover = generate_cover_letter(application.jd_text or "", evaluation)

        if "error" in cover:
            return f'<div class="text-red-400 text-sm p-2">Error: {cover["error"]}</div>'

        application.cover_generated = True
        db.session.commit()

        full_text = cover.get("full_text", "")
        safe_text = full_text.replace("`", "'").replace("\\", "\\\\")

        return f'''<div class="bg-purple-900/40 border border-purple-700 rounded-lg p-4 mt-2">
  <div class="flex items-center justify-between mb-2">
    <h4 class="text-purple-300 text-sm font-semibold">✓ Cover Letter ({cover.get("word_count", "~")} words)</h4>
    <button onclick="navigator.clipboard.writeText(`{safe_text}`)"
      class="bg-purple-700 hover:bg-purple-600 px-3 py-1 rounded text-xs text-white">Copy</button>
  </div>
  <div class="text-xs text-gray-300 whitespace-pre-wrap leading-relaxed max-h-52 overflow-y-auto">{full_text}</div>
</div>'''

    @app.route("/api/apply-email", methods=["POST"])
    def apply_email():
        try:
            body = request.get_json(silent=True) or {}
            app_id = body.get("app_id")
            to_email = (body.get("to_email") or "").strip()
            cover_text = (body.get("cover_letter_text") or "").strip()

            if not app_id:
                return jsonify({"success": False, "error": "app_id required."}), 400

            app_record = Application.query.get(app_id)
            if not app_record:
                return jsonify({"success": False, "error": "Application not found."}), 404

            from services.email_applicator import email_applicator

            # Auto-extract email from JD if not provided
            if not to_email and app_record.jd_text:
                to_email = email_applicator.extract_email_from_jd(app_record.jd_text) or ""

            if not to_email:
                return jsonify({
                    "success": False,
                    "error": "No recruiter email found. Please provide one manually.",
                }), 400

            # Use stored cover letter text or generate a simple fallback
            if not cover_text:
                evaluation = app_record.get_evaluation()
                cover_text = evaluation.get("cover_letter_hook", "")
                if not cover_text:
                    return jsonify({
                        "success": False,
                        "error": "No cover letter text. Please generate a cover letter first.",
                    }), 400

            result = email_applicator.apply(
                to_email=to_email,
                company=app_record.company,
                role=app_record.role,
                cover_letter_text=cover_text,
            )

            if result["success"]:
                app_record.status = "Applied"
                app_record.applied_date = datetime.utcnow()
                db.session.commit()

            return jsonify(result)

        except Exception as e:
            logger.exception("Unhandled error in /api/apply-email")
            return jsonify({"success": False, "error": "Email send failed."}), 500

    @app.route("/api/email-test", methods=["GET"])
    def email_test():
        from services.email_applicator import email_applicator
        result = email_applicator.test_connection()
        return jsonify(result)

    @app.route("/agent/start", methods=["POST"])
    def start_agent():
        from services.agent import agent
        agent.start()
        return jsonify({"status": "started"})

    @app.route("/agent/stop", methods=["POST"])
    def stop_agent():
        from services.agent import agent
        agent.stop()
        return jsonify({"status": "stopped"})

    @app.route("/agent/status", methods=["GET"])
    def agent_status():
        from services.agent import agent
        return jsonify(agent.get_status())

    @app.route("/agent/queue/<int:app_id>", methods=["POST"])
    def queue_job(app_id):
        from services.agent import agent

        app_record = Application.query.get(app_id)
        if not app_record:
            return jsonify({"error": "Application not found"}), 404

        task_id = agent.add_task(
            app_record.id,
            app_record.company,
            app_record.role,
            app_record.url or "",
            app_record.jd_text or "",
        )

        if task_id is None:
            return jsonify({"status": "skipped", "reason": "LinkedIn jobs are not automated"})

        return jsonify({"status": "queued", "task_id": task_id})

    @app.route("/agent/queue-grade-a", methods=["POST"])
    def queue_all_grade_a():
        from services.agent import agent

        grade_a_apps = Application.query.filter_by(grade="A", status="Evaluated").all()
        queued = 0
        skipped = 0

        for app_record in grade_a_apps:
            task_id = agent.add_task(
                app_record.id,
                app_record.company,
                app_record.role,
                app_record.url or "",
                app_record.jd_text or "",
            )
            if task_id is not None:
                queued += 1
            else:
                skipped += 1

        return jsonify({"queued": queued, "skipped_linkedin": skipped})

    @app.route("/agent/resume/<int:task_id>", methods=["POST"])
    def resume_agent_task(task_id):
        from services.agent import agent
        success = agent.resume_task(task_id)
        return jsonify({"resumed": success})

    @app.route("/check-emails", methods=["POST"])
    def check_emails():
        from services.email_tracker import email_tracker

        replies = email_tracker.check_replies()

        if isinstance(replies, dict) and "error" in replies:
            return f'<div class="text-red-400 text-sm p-2">{replies["error"]}</div>'

        if not replies:
            return '<div class="text-gray-400 text-sm p-2">No job-related replies found in the last 30 days.</div>'

        badge_classes = {
            "interview": "bg-green-900 text-green-300",
            "rejection": "bg-red-900 text-red-300",
            "reply": "bg-blue-900 text-blue-300",
        }

        html = '<div class="space-y-2">'
        for r in replies[:10]:
            badge = badge_classes.get(r["type"], "bg-gray-700 text-gray-300")
            subject_short = r["subject"][:60] + ("…" if len(r["subject"]) > 60 else "")
            from_short = r["from"][:50] + ("…" if len(r["from"]) > 50 else "")
            html += f'''<div class="bg-gray-800 p-3 rounded-lg border border-gray-700">
  <div class="flex items-start justify-between gap-2">
    <span class="text-white text-sm">{subject_short}</span>
    <span class="px-2 py-0.5 {badge} rounded text-xs font-medium whitespace-nowrap">{r["type"].upper()}</span>
  </div>
  <div class="text-gray-500 text-xs mt-1">{from_short}</div>
  <div class="text-gray-600 text-xs mt-0.5">{r.get("date", "")[:25]}</div>
</div>'''
        html += "</div>"
        return html

    @app.route("/api/email-summary", methods=["GET"])
    def api_email_summary():
        from services.email_tracker import email_tracker
        summary = email_tracker.get_summary()
        return jsonify(summary)

    @app.route("/api/form-apply", methods=["POST"])
    def api_form_apply():
        try:
            body = request.get_json(silent=True) or {}
            url = (body.get("url") or "").strip()
            form_type = (body.get("form_type") or "auto").lower()

            if not url:
                return jsonify({"success": False, "error": "No URL provided."}), 400

            from services.form_applicator import form_applicator

            if form_type == "google" or "docs.google.com/forms" in url:
                result = form_applicator.apply_google_form(url)
            else:
                result = form_applicator.apply_web_form(url)

            app_id = body.get("app_id")
            if result.get("success") and app_id:
                app_record = Application.query.get(app_id)
                if app_record:
                    app_record.status = "Applied"
                    app_record.applied_date = datetime.utcnow()
                    db.session.commit()

            return jsonify(result)

        except Exception as e:
            logger.exception("Unhandled error in /api/form-apply")
            return jsonify({"success": False, "error": "Form application failed."}), 500

    @app.route("/api/sources")
    def api_sources():
        from services.sources import get_sources_for_scanning, count_sources
        region = request.args.get("region", "india")
        category = request.args.get("category")
        sources = get_sources_for_scanning(region, category)
        counts = count_sources()
        return jsonify({"sources": sources, "counts": counts, "returned": len(sources)})

    @app.route("/api/profile")
    def api_profile():
        profile = load_profile()
        if not profile:
            return jsonify({"error": "Profile not found"}), 404
        return jsonify(profile)

    @app.route("/api/health")
    def health():
        try:
            db.session.execute(db.text("SELECT 1"))
            db_status = "ok"
        except Exception as e:
            db_status = f"error: {e}"

        return jsonify({
            "status": "ok",
            "db": db_status,
            "timestamp": datetime.utcnow().isoformat(),
        })

    @app.errorhandler(404)
    def not_found(e):
        return jsonify({"error": "Not found"}), 404

    @app.errorhandler(500)
    def server_error(e):
        return jsonify({"error": "Internal server error"}), 500

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
