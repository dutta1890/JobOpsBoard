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

            if not jd_text:
                return jsonify({"success": False, "error": "No job description provided."}), 400

            from services.job_parser import parse_job_input
            from services.claude_client import evaluate_job

            parsed = parse_job_input(jd_text)
            if not parsed["success"]:
                return jsonify({"success": False, "error": parsed.get("error", "Failed to parse input.")}), 400

            text = parsed["text"]
            result = evaluate_job(text)

            if not result["success"]:
                return jsonify({"success": False, "error": result.get("error", "Evaluation failed.")}), 500

            data = result["data"]
            return jsonify({
                "success": True,
                "data": data,
                "meta": {
                    "company": data.get("company", ""),
                    "role": data.get("role", ""),
                    "location": data.get("location", ""),
                    "url": parsed.get("url"),
                    "title": parsed.get("title", ""),
                },
                "jd_text": text,
            })

        except Exception as e:
            logger.exception("Unhandled error in /evaluate")
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
