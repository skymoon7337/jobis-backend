from pathlib import Path

from dotenv import load_dotenv


ROOT_DIR = Path(__file__).resolve().parent
ENV_PATH = ROOT_DIR / ".env"


def load_environment() -> None:
    load_dotenv(dotenv_path=ENV_PATH)
