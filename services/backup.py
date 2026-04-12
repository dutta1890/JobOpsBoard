"""
backup.py — Automatic database and data backup for Career-Ops.

Keeps the last 7 daily backups; older ones are pruned automatically.
DB path checked against the Flask instance folder first, then data/.
"""

import logging
import shutil
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

BACKUP_DIR = Path("backups")
MAX_BACKUPS = 7

# Possible DB locations (instance folder takes priority)
_DB_CANDIDATES = [
    Path("instance/career_ops.db"),
    Path("data/career_ops.db"),
]


class BackupManager:
    """Manages automatic rolling backups."""

    def __init__(self):
        BACKUP_DIR.mkdir(exist_ok=True)

    def backup_database(self) -> str | None:
        """Copy the SQLite database to backups/ with a timestamp."""
        db_path = next((p for p in _DB_CANDIDATES if p.exists()), None)
        if not db_path:
            logger.warning("backup_database: no database file found")
            return None

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest = BACKUP_DIR / f"db_backup_{timestamp}.db"
        shutil.copy2(db_path, dest)
        logger.info(f"Database backed up to {dest}")
        self._cleanup_old_backups("db_backup_", ".db")
        return str(dest)

    def backup_applications(self) -> str | None:
        """Zip the data/ directory to backups/ with a timestamp."""
        data_dir = Path("data")
        if not data_dir.exists():
            logger.warning("backup_applications: data/ directory not found")
            return None

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        archive_base = BACKUP_DIR / f"data_backup_{timestamp}"
        shutil.make_archive(str(archive_base), "zip", data_dir)
        dest = Path(str(archive_base) + ".zip")
        logger.info(f"Data backed up to {dest}")
        self._cleanup_old_backups("data_backup_", ".zip")
        return str(dest)

    def run_full_backup(self) -> dict:
        """Run both backup jobs and return their paths."""
        return {
            "database": self.backup_database(),
            "data": self.backup_applications(),
            "timestamp": datetime.now().isoformat(),
        }

    # ── Internal ───────────────────────────────────────────────────────────────

    def _cleanup_old_backups(self, prefix: str, suffix: str) -> None:
        files = sorted(
            BACKUP_DIR.glob(f"{prefix}*{suffix}"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for old in files[MAX_BACKUPS:]:
            try:
                old.unlink()
                logger.debug(f"Removed old backup: {old}")
            except Exception as exc:
                logger.warning(f"Could not remove {old}: {exc}")


# ── Singleton ──────────────────────────────────────────────────────────────────
backup_manager = BackupManager()
