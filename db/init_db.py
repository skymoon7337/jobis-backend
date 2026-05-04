from config import load_environment

load_environment()

from db import models  # noqa: F401
from db.session import Base, engine
from sqlalchemy import text


def migrate_dev_schema() -> None:
    statements = [
        "ALTER TABLE telegram_users ALTER COLUMN chat_id TYPE BIGINT",
        "ALTER TABLE telegram_users ADD COLUMN IF NOT EXISTS analysis_summary TEXT DEFAULT ''",
        "ALTER TABLE interview_turns ADD COLUMN IF NOT EXISTS display_id VARCHAR(30) DEFAULT ''",
        "ALTER TABLE interview_turns ADD COLUMN IF NOT EXISTS question_type VARCHAR(50) DEFAULT ''",
        "ALTER TABLE interview_turns ADD COLUMN IF NOT EXISTS is_bonus BOOLEAN DEFAULT FALSE",
        "ALTER TABLE interview_turns ADD COLUMN IF NOT EXISTS bonus_type VARCHAR(30) DEFAULT ''",
        "ALTER TABLE interview_sessions ADD COLUMN IF NOT EXISTS weakness_summary TEXT DEFAULT ''",
        "ALTER TABLE interview_sessions ADD COLUMN IF NOT EXISTS current_display_id VARCHAR(30) DEFAULT ''",
        "ALTER TABLE interview_sessions ADD COLUMN IF NOT EXISTS awaiting_choice BOOLEAN DEFAULT FALSE",
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
