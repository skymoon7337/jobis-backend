import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import load_environment

load_environment()

from db import models  # noqa: F401
from db.session import Base, engine
from sqlalchemy import text


def migrate_dev_schema() -> None:
    statements = [
        "ALTER TABLE interview_turns ADD COLUMN IF NOT EXISTS display_id VARCHAR(30) DEFAULT ''",
        "ALTER TABLE interview_turns ADD COLUMN IF NOT EXISTS question_type VARCHAR(50) DEFAULT ''",
        "ALTER TABLE interview_turns ADD COLUMN IF NOT EXISTS is_bonus BOOLEAN DEFAULT FALSE",
        "ALTER TABLE interview_turns ADD COLUMN IF NOT EXISTS bonus_type VARCHAR(30) DEFAULT ''",
        "ALTER TABLE interview_sessions ADD COLUMN IF NOT EXISTS weakness_summary TEXT DEFAULT ''",
        "ALTER TABLE interview_sessions ADD COLUMN IF NOT EXISTS current_display_id VARCHAR(30) DEFAULT ''",
        "ALTER TABLE interview_sessions ADD COLUMN IF NOT EXISTS awaiting_choice BOOLEAN DEFAULT FALSE",
        "ALTER TABLE interview_sessions ADD COLUMN IF NOT EXISTS context_profile TEXT DEFAULT ''",
        "ALTER TABLE interview_sessions ADD COLUMN IF NOT EXISTS context_resume TEXT DEFAULT ''",
        "ALTER TABLE interview_sessions ADD COLUMN IF NOT EXISTS context_job_title VARCHAR(200) DEFAULT ''",
        "ALTER TABLE interview_sessions ADD COLUMN IF NOT EXISTS context_job_summary TEXT DEFAULT ''",
        "ALTER TABLE interview_sessions ADD COLUMN IF NOT EXISTS context_github_repositories TEXT DEFAULT '[]'",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS profile_updated_at TIMESTAMP WITH TIME ZONE",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS resume_updated_at TIMESTAMP WITH TIME ZONE",
        "ALTER TABLE job_postings ADD COLUMN IF NOT EXISTS alias VARCHAR(200) DEFAULT ''",
        """
        CREATE TABLE IF NOT EXISTS github_projects (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id),
            repo_key VARCHAR(300) DEFAULT '',
            url TEXT DEFAULT '',
            alias VARCHAR(200) DEFAULT '',
            title VARCHAR(200) DEFAULT '',
            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
        )
        """,
        "CREATE INDEX IF NOT EXISTS ix_github_projects_id ON github_projects (id)",
        "CREATE INDEX IF NOT EXISTS ix_github_projects_user_id ON github_projects (user_id)",
        "CREATE INDEX IF NOT EXISTS ix_github_projects_repo_key ON github_projects (repo_key)",
        """
        CREATE TABLE IF NOT EXISTS github_snapshots (
            id SERIAL PRIMARY KEY,
            project_id INTEGER REFERENCES github_projects(id),
            version INTEGER DEFAULT 1,
            summary TEXT DEFAULT '',
            change_summary TEXT DEFAULT '',
            default_branch VARCHAR(200) DEFAULT '',
            commit_sha VARCHAR(80) DEFAULT '',
            commit_date TIMESTAMP WITH TIME ZONE,
            analyzed_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            is_latest BOOLEAN DEFAULT TRUE
        )
        """,
        "CREATE INDEX IF NOT EXISTS ix_github_snapshots_id ON github_snapshots (id)",
        "CREATE INDEX IF NOT EXISTS ix_github_snapshots_project_id ON github_snapshots (project_id)",
        """
        CREATE TABLE IF NOT EXISTS analysis_jobs (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id),
            kind VARCHAR(50) DEFAULT '',
            status VARCHAR(30) DEFAULT 'queued',
            stage VARCHAR(80) DEFAULT '',
            message TEXT DEFAULT '',
            progress_current INTEGER DEFAULT 0,
            progress_total INTEGER DEFAULT 0,
            input_json TEXT DEFAULT '{}',
            result_json TEXT DEFAULT '{}',
            error_type VARCHAR(80) DEFAULT '',
            error_message TEXT DEFAULT '',
            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            finished_at TIMESTAMP WITH TIME ZONE
        )
        """,
        "CREATE INDEX IF NOT EXISTS ix_analysis_jobs_id ON analysis_jobs (id)",
        "CREATE INDEX IF NOT EXISTS ix_analysis_jobs_user_id ON analysis_jobs (user_id)",
        "CREATE INDEX IF NOT EXISTS ix_analysis_jobs_kind ON analysis_jobs (kind)",
        "CREATE INDEX IF NOT EXISTS ix_analysis_jobs_status ON analysis_jobs (status)",
    ]
    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))


def main() -> None:
    Base.metadata.create_all(bind=engine)
    migrate_dev_schema()
    print("Database tables created.")


if __name__ == "__main__":
    main()
