import os
from dataclasses import dataclass
from urllib.parse import quote_plus


@dataclass(frozen=True)
class DatabaseSettings:
    host: str = "localhost"
    port: int = 5432
    name: str = "postgres"
    username: str = "postgres"
    password: str = "postgres"

    @classmethod
    def from_env(cls) -> "DatabaseSettings":
        return cls(
            host=os.getenv("DB_HOST", "localhost"),
            port=int(os.getenv("DB_PORT", "5432")),
            name=os.getenv("DB_NAME", "postgres"),
            username=os.getenv("DB_USERNAME", "postgres"),
            password=os.getenv("DB_PASSWORD", "postgres"),
        )

    def sqlalchemy_url(self) -> str:
        username = quote_plus(self.username)
        password = quote_plus(self.password)
        return f"postgresql+psycopg://{username}:{password}@{self.host}:{self.port}/{self.name}"
