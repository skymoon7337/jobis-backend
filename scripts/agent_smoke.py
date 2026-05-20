from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv


ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))
load_dotenv(ROOT_DIR / ".env")

from db.repository import delete_user_data  # noqa: E402


DEFAULT_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_USER_KEY = "test-agent-smoke-script"


def assert_ok(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def request_json(client: httpx.Client, method: str, path: str, **kwargs: object) -> dict:
    response = client.request(method, path, **kwargs)
    try:
        payload = response.json()
    except ValueError as exc:
        raise AssertionError(f"{method} {path} returned non-JSON response: {response.text}") from exc

    assert_ok(response.status_code == 200, f"{method} {path} returned {response.status_code}: {payload}")
    return payload


def run_smoke(base_url: str, user_key: str, keep_data: bool) -> None:
    headers = {"X-Jobis-User-Key": user_key}

    if not keep_data:
        delete_user_data(user_key)

    try:
        with httpx.Client(base_url=base_url, headers=headers, timeout=10.0) as client:
            agent_response = request_json(
                client,
                "POST",
                "/api/agent",
                json={"message": "공고 목록 보여줘"},
            )
            assert_ok(agent_response.get("ok") is True, f"agent response was not ok: {agent_response}")
            assert_ok(
                agent_response.get("action") == "list_job_postings",
                f"unexpected action: {agent_response.get('action')}",
            )
            assert_ok(
                agent_response.get("data", {}).get("intent_source") == "rule",
                f"unexpected intent source: {agent_response.get('data')}",
            )

            messages_response = request_json(client, "GET", "/api/agent/messages?limit=5")
            messages = messages_response.get("data", {}).get("messages", [])
            assert_ok(len(messages) >= 2, f"expected chat messages, got: {messages}")
            assert_ok(messages[-2].get("role") == "user", f"missing user message: {messages}")
            assert_ok(messages[-1].get("role") == "assistant", f"missing assistant message: {messages}")
            assert_ok(
                messages[-1].get("action") == "list_job_postings",
                f"assistant action was not saved: {messages[-1]}",
            )

            actions_response = request_json(client, "GET", "/api/agent/actions?limit=5")
            actions = actions_response.get("data", {}).get("actions", [])
            assert_ok(actions, "expected at least one action log")
            assert_ok(actions[-1].get("action") == "list_job_postings", f"unexpected action log: {actions[-1]}")
            assert_ok(actions[-1].get("status") == "completed", f"action did not complete: {actions[-1]}")

            pending_response = request_json(client, "GET", "/api/agent/pending?limit=5")
            pending_commands = pending_response.get("data", {}).get("pending_commands", [])
            assert_ok(isinstance(pending_commands, list), f"pending commands was not a list: {pending_response}")

        print("agent smoke passed")
        print(f"base_url={base_url}")
        print(f"user_key={user_key}")
    finally:
        if not keep_data:
            delete_user_data(user_key)


def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke test the running Jobis agent API.")
    parser.add_argument(
        "--base-url",
        default=os.getenv("JOBIS_SMOKE_BASE_URL", DEFAULT_BASE_URL),
        help=f"Running backend base URL. Default: {DEFAULT_BASE_URL}",
    )
    parser.add_argument(
        "--user-key",
        default=os.getenv("JOBIS_SMOKE_USER_KEY", DEFAULT_USER_KEY),
        help=f"Temporary user key for smoke data. Default: {DEFAULT_USER_KEY}",
    )
    parser.add_argument(
        "--keep-data",
        action="store_true",
        help="Do not delete smoke user data before and after the run.",
    )
    args = parser.parse_args()

    run_smoke(args.base_url.rstrip("/"), args.user_key, args.keep_data)


if __name__ == "__main__":
    main()
