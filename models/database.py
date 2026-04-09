from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()


class JobApplication(db.Model):
    __tablename__ = "job_applications"

    id = db.Column(db.Integer, primary_key=True)
    company = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(200), nullable=False)
    url = db.Column(db.String(500))
    status = db.Column(db.String(50), default="pending")
    score = db.Column(db.Float)
    notes = db.Column(db.Text)
    jd_text = db.Column(db.Text)
    evaluation = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "company": self.company,
            "role": self.role,
            "url": self.url,
            "status": self.status,
            "score": self.score,
            "notes": self.notes,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def __repr__(self):
        return f"<JobApplication {self.company} - {self.role}>"


class ScanHistory(db.Model):
    __tablename__ = "scan_history"

    id = db.Column(db.Integer, primary_key=True)
    source = db.Column(db.String(200))
    jobs_found = db.Column(db.Integer, default=0)
    jobs_new = db.Column(db.Integer, default=0)
    scanned_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<ScanHistory {self.source} @ {self.scanned_at}>"
