import hashlib
import json
import re
from datetime import datetime
from urllib.parse import urlparse as _urlparse

from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


def urlparse_netloc(url: str) -> str:
    """Return the lowercased netloc of a URL, stripping www."""
    try:
        return _urlparse(url).netloc.lower().lstrip("www.")
    except Exception:
        return url


class JobPosting(db.Model):
    __tablename__ = "job_postings"

    id = db.Column(db.Integer, primary_key=True)
    job_hash = db.Column(db.String(100), unique=True, index=True, nullable=False)
    company_normalized = db.Column(db.String(200))
    role_normalized = db.Column(db.String(200))
    location = db.Column(db.String(100))
    first_seen_url = db.Column(db.String(500))
    first_seen_platform = db.Column(db.String(50))
    first_seen_date = db.Column(db.DateTime, default=datetime.utcnow)
    duplicate_urls = db.Column(db.Text, default="[]")
    times_seen = db.Column(db.Integer, default=1)

    applications = db.relationship("Application", backref="job_posting", lazy=True)

    def get_duplicate_urls(self):
        try:
            return json.loads(self.duplicate_urls or "[]")
        except Exception:
            return []

    def add_duplicate_url(self, url):
        urls = self.get_duplicate_urls()
        if url and url not in urls:
            urls.append(url)
            self.duplicate_urls = json.dumps(urls)

    def __repr__(self):
        return f"<JobPosting {self.company_normalized} - {self.role_normalized}>"


class Application(db.Model):
    __tablename__ = "applications"

    id = db.Column(db.Integer, primary_key=True)
    job_posting_id = db.Column(db.Integer, db.ForeignKey("job_postings.id"), nullable=True)
    company = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(200), nullable=False)
    location = db.Column(db.String(200))
    url = db.Column(db.String(500))
    platform = db.Column(db.String(50))
    score = db.Column(db.Float)
    grade = db.Column(db.String(1))
    archetype = db.Column(db.String(50))
    recommendation = db.Column(db.String(20))
    visa_sponsorship = db.Column(db.String(20))
    evaluation_json = db.Column(db.Text)
    jd_text = db.Column(db.Text)
    cover_letter_text = db.Column(db.Text)
    status = db.Column(db.String(50), default="Evaluated")
    cv_generated = db.Column(db.Boolean, default=False)
    cover_generated = db.Column(db.Boolean, default=False)
    applied_date = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    notes = db.Column(db.Text)

    def get_evaluation(self):
        try:
            return json.loads(self.evaluation_json or "{}")
        except Exception:
            return {}

    def to_dict(self):
        return {
            "id": self.id,
            "company": self.company,
            "role": self.role,
            "location": self.location,
            "url": self.url,
            "platform": self.platform,
            "score": self.score,
            "grade": self.grade,
            "archetype": self.archetype,
            "recommendation": self.recommendation,
            "visa_sponsorship": self.visa_sponsorship,
            "status": self.status,
            "cv_generated": self.cv_generated,
            "cover_generated": self.cover_generated,
            "cover_letter_text": self.cover_letter_text,
            "applied_date": self.applied_date.isoformat() if self.applied_date else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "notes": self.notes,
        }

    def __repr__(self):
        return f"<Application {self.company} - {self.role}>"


class CompanySource(db.Model):
    """
    User-provided company watchlist — persisted in the DB so it survives
    redeployments and settings resets.
    """
    __tablename__ = "company_sources"

    id               = db.Column(db.Integer, primary_key=True)
    name             = db.Column(db.String(200))
    source_url       = db.Column(db.String(500), unique=True, index=True, nullable=False)
    careers_url      = db.Column(db.String(500))
    region           = db.Column(db.String(100), index=True)   # which region this company was discovered for
    is_validated     = db.Column(db.Boolean, default=False)    # passed PM relevance check
    added_at         = db.Column(db.DateTime, default=datetime.utcnow)
    last_scanned_at  = db.Column(db.DateTime)
    scan_count       = db.Column(db.Integer, default=0)
    job_url_snapshot = db.Column(db.Text, default="[]")

    def get_snapshot(self) -> set:
        try:
            return set(json.loads(self.job_url_snapshot or "[]"))
        except Exception:
            return set()

    def set_snapshot(self, urls: set) -> None:
        self.job_url_snapshot = json.dumps(sorted(urls))

    @classmethod
    def upsert(cls, name: str, source_url: str, region: str = None) -> "CompanySource":
        row = cls.query.filter_by(source_url=source_url).first()
        if row is None:
            row = cls(name=name, source_url=source_url, region=region)
            db.session.add(row)
            db.session.commit()
        else:
            changed = False
            if row.name != name:
                row.name = name; changed = True
            if region and not row.region:
                row.region = region; changed = True
            if changed:
                db.session.commit()
        return row

    @classmethod
    def all_as_list(cls, region: str = None) -> list:
        q = cls.query.order_by(cls.added_at)
        if region:
            q = q.filter_by(region=region)
        return [
            {
                "name": r.name,
                "careers": r.careers_url or r.source_url,
                "source_url": r.source_url,
                "region": r.region or "primary",
                "is_validated": r.is_validated,
                "scan_count": r.scan_count or 0,
            }
            for r in q.all()
        ]

    @classmethod
    def known_domains(cls) -> set:
        return {
            urlparse_netloc(r.source_url)
            for r in cls.query.with_entities(cls.source_url).all()
        }


class UserProfile(db.Model):
    """Single-row table that stores the user's profile / CV text."""
    __tablename__ = "user_profile"

    id      = db.Column(db.Integer, primary_key=True)
    cv_text = db.Column(db.Text, default="")
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    @classmethod
    def get(cls) -> "UserProfile":
        row = cls.query.first()
        if row is None:
            row = cls(cv_text="")
            db.session.add(row)
            db.session.commit()
        return row

    @classmethod
    def set_cv(cls, text: str) -> None:
        row = cls.get()
        row.cv_text = text
        row.updated_at = datetime.utcnow()
        db.session.commit()


class ScanHistory(db.Model):
    __tablename__ = "scan_history"

    id = db.Column(db.Integer, primary_key=True)
    url = db.Column(db.String(500), unique=True)
    platform = db.Column(db.String(50))
    scanned_at = db.Column(db.DateTime, default=datetime.utcnow)
    result = db.Column(db.String(20))

    def __repr__(self):
        return f"<ScanHistory {self.platform} - {self.result}>"


def _normalize(text: str) -> str:
    if not text:
        return ""
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9\s]", "", text)
    text = re.sub(r"\s+", " ", text)
    return text


def create_job_hash(company: str, role: str, location: str = "") -> str:
    key = f"{_normalize(company)}|{_normalize(role)}|{_normalize(location)}"
    return hashlib.sha256(key.encode()).hexdigest()[:64]


def check_duplicate(job_hash: str) -> bool:
    return db.session.query(
        db.exists().where(JobPosting.job_hash == job_hash)
    ).scalar()


def record_job_posting(job_data: dict) -> "JobPosting":
    company = job_data.get("company", "")
    role = job_data.get("role", "")
    location = job_data.get("location", "")
    job_hash = create_job_hash(company, role, location)

    posting = JobPosting(
        job_hash=job_hash,
        company_normalized=_normalize(company),
        role_normalized=_normalize(role),
        location=location,
        first_seen_url=job_data.get("url"),
        first_seen_platform=job_data.get("platform"),
        first_seen_date=datetime.utcnow(),
        duplicate_urls="[]",
        times_seen=1,
    )
    db.session.add(posting)
    db.session.commit()
    return posting


def get_or_create_posting(job_data: dict) -> tuple:
    company = job_data.get("company", "")
    role = job_data.get("role", "")
    location = job_data.get("location", "")
    job_hash = create_job_hash(company, role, location)

    posting = JobPosting.query.filter_by(job_hash=job_hash).first()
    if posting:
        posting.times_seen += 1
        url = job_data.get("url")
        if url:
            posting.add_duplicate_url(url)
        db.session.commit()
        return posting, False

    posting = record_job_posting(job_data)
    return posting, True
