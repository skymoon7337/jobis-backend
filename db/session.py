from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from db.config import DatabaseSettings


class Base(DeclarativeBase):
    pass


settings = DatabaseSettings.from_env()
engine = create_engine(settings.sqlalchemy_url(), pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
