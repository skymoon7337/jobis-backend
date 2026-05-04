from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from config import load_environment
from db.config import DatabaseSettings

load_environment()


class Base(DeclarativeBase):
    pass


settings = DatabaseSettings.from_env()
engine = create_engine(settings.sqlalchemy_url(), pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
