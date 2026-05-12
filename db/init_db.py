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
        """
        CREATE TABLE IF NOT EXISTS github_repositories (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id),
            url TEXT DEFAULT '',
            title VARCHAR(200) DEFAULT '',
            summary TEXT DEFAULT '',
            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
        )
        """,
        "CREATE INDEX IF NOT EXISTS ix_github_repositories_id ON github_repositories (id)",
        "CREATE INDEX IF NOT EXISTS ix_github_repositories_user_id ON github_repositories (user_id)",
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
