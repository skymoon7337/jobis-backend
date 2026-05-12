from typing import Annotated

from fastapi import Header

from services.workflow import default_user_key


def request_user_key(x_jobis_user_key: Annotated[str | None, Header()] = None) -> str:
    candidate = (x_jobis_user_key or "").strip()
    if not candidate:
        return default_user_key()
    return candidate[:100]
