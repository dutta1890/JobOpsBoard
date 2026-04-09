import json
import logging
import os
from datetime import datetime, date

from flask import Flask, jsonify, render_template, request, flash
from models.database import db, JobApplication, ScanHistory
from config import Config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    db.init_app(app)

    with app.app_context():
        db.create_all()
        logger.info("Database tables created/verified.")

    def load_json(path):
        try:
            with open(os.path.join(os.path.dirname(__file__), path)) as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Could not load {path}: {e}")
            return {}

    @app.route("/")
    def index():
        applications = JobApplication.query.order_by(
            JobApplication.created_at.desc()
        ).all()

        total = len(applications)
        applied = sum(1 for a in applications if a.status == "applied")
        interviews = sum(1 for a in applications if a.status == "interview")
        scores = [a.score for a in applications if a.score is not None]
        avg_score = round(sum(scores) / len(scores)) if scores else 0

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

            from services.job_parser import parse_job_input, extract_company_role
            from services.claude_client import evaluate_job

            parsed = parse_job_input(jd_text)
            if not parsed["success"]:
                return jsonify({"success": False, "error": parsed.get("error", "Failed to parse input.")}), 400

            text = parsed["text"]
            meta = extract_company_role(text)

            profile = load_json("data/profile.json")
            result = evaluate_job(text, profile)

            if not result["success"]:
                return jsonify({"success": False, "error": result.get("error", "Evaluation failed.")}), 500

            return jsonify({
                "success": True,
                "data": result["data"],
                "meta": {
                    "company": meta.get("company", ""),
                    "role": meta.get("role", ""),
                    "url": parsed.get("url"),
                    "title": parsed.get("title", ""),
                },
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

            company = meta.get("company") or "Unknown"
            role = meta.get("role") or "Unknown"

            from services.dedup import is_duplicate
            existing = JobApplication.query.all()
            dup = is_duplicate(company, role, existing)

            if dup["is_duplicate"]:
                return jsonify({
                    "success": False,
                    "error": f"Duplicate detected: similar job already tracked (similarity={dup['similarity']}%).",
                })

            job = JobApplication(
                company=company,
                role=role,
                url=meta.get("url"),
                score=data.get("score"),
                status="pending",
                evaluation=json.dumps(data),
                jd_text=body.get("jd_text", ""),
            )
            db.session.add(job)
            db.session.commit()

            return jsonify({"success": True, "id": job.id})

        except Exception as e:
            logger.exception("Unhandled error in /api/save-job")
            db.session.rollback()
            return jsonify({"success": False, "error": "Failed to save job."}), 500

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
