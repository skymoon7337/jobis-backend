from config import load_environment

load_environment()

from db import models  # noqa: F401
from db.session import Base, engine


def main() -> None:
    Base.metadata.create_all(bind=engine)
    print("Database tables created.")


if __name__ == "__main__":
    main()
