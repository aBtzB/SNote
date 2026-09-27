"""Short SQLite transactions; model inference never holds a database lock."""

from contextlib import contextmanager
import json
import sqlite3
from .domain import BUSY, Problem, now


class Store:
    def __init__(self, root):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / "notebook.sqlite3"
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY, document TEXT NOT NULL)")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        try:
            with db:
                yield db
        finally:
            db.close()

    def create(self, project):
        with self.connect() as db:
            db.execute("INSERT INTO projects VALUES (?, ?)",
                       (project["id"], json.dumps(project, ensure_ascii=False)))
        return project

    def get(self, project_id):
        with self.connect() as db:
            row = db.execute("SELECT document FROM projects WHERE id=?", (project_id,)).fetchone()
        if row is None:
            raise Problem("Recording not found.", 404)
        return json.loads(row[0])

    def list(self):
        with self.connect() as db:
            projects = [json.loads(row[0]) for row in db.execute("SELECT document FROM projects")]
        return sorted(projects, key=lambda p: p["created_at"], reverse=True)

    def mutate(self, project_id, change, expected_revision=None):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT document FROM projects WHERE id=?", (project_id,)).fetchone()
            if row is None:
                raise Problem("Recording not found.", 404)
            project = json.loads(row[0])
            if expected_revision is not None and expected_revision != project["revision"]:
                raise Problem("This recording changed in another tab. Reload before saving.", 409)
            change(project)
            project["revision"] += 1
            project["updated_at"] = now()
            db.execute("UPDATE projects SET document=? WHERE id=?",
                       (json.dumps(project, ensure_ascii=False), project_id))
        return project

    def update(self, project_id, **fields):
        return self.mutate(project_id, lambda p: p.update(fields))

    def delete(self, project_id):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT document FROM projects WHERE id=?", (project_id,)).fetchone()
            if row is None:
                raise Problem("Recording not found.", 404)
            if json.loads(row[0])["status"] in BUSY:
                raise Problem("Stop the job before deleting this recording.", 409)
            db.execute("DELETE FROM projects WHERE id=?", (project_id,))

    def recover(self):
        for project in self.list():
            if project["status"] in BUSY:
                self.update(project["id"], status="interrupted",
                            message="Processing stopped when the app closed. Saved lines are still available.")
