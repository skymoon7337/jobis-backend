import os
from dataclasses import dataclass
from urllib.parse import quote_plus


@dataclass(frozen=True)
class DatabaseSettings:
    host: str
    port: int
    name: str
    username: str
    password: str

    @classmethod
    def from_env(cls) -> "DatabaseSettings":
        required_keys = ("DB_HOST", "DB_PORT", "DB_NAME", "DB_USERNAME", "DB_PASSWORD")
        values = {key: os.getenv(key, "").strip() for key in required_keys}
        missing_keys = [key for key, value in values.items() if not value]
        if missing_keys:
            raise RuntimeError(f"필수 DB 환경변수가 없습니다: {', '.join(missing_keys)}")

        try:
            port = int(values["DB_PORT"])
        except ValueError as exc:
            raise RuntimeError("DB_PORT는 숫자여야 합니다.") from exc

        return cls(
            host=values["DB_HOST"],
            port=port,
            name=values["DB_NAME"],
            username=values["DB_USERNAME"],
            password=values["DB_PASSWORD"],
        )

    def sqlalchemy_url(self) -> str:
        username = quote_plus(self.username)
        password = quote_plus(self.password)
        return f"postgresql+psycopg://{username}:{password}@{self.host}:{self.port}/{self.name}"
