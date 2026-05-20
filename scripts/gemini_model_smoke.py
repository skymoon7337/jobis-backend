from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types


ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))
load_dotenv(ROOT_DIR / ".env")


TEXT_MODEL_ENV_NAMES = (
    "GEMINI_MODEL",
    "JOBIS_MAIN_CHAT_MODEL",
    "JOBIS_ANALYSIS_MODEL",
)
EMBEDDING_MODEL_ENV_NAME = "JOBIS_EMBEDDING_MODEL"


def unique_models(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            result.append(value)
            seen.add(value)
    return result


def model_names(client: genai.Client) -> set[str]:
    names: set[str] = set()
    for model in client.models.list():
        raw_name = getattr(model, "name", "") or ""
        names.add(raw_name.removeprefix("models/"))
    return names


def assert_available(available_models: set[str], model: str) -> None:
    if model not in available_models:
        raise AssertionError(f"{model} was not returned by Gemini models.list()")


def run_smoke(skip_calls: bool) -> None:
    provider = os.getenv("JOBIS_PROVIDER", "gemini").lower()
    if provider != "gemini":
        raise AssertionError(f"JOBIS_PROVIDER is {provider!r}; this smoke only verifies Gemini models")

    if not os.getenv("GEMINI_API_KEY"):
        raise AssertionError("GEMINI_API_KEY is not set")

    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
    available_models = model_names(client)

    text_models = unique_models([os.getenv(name, "") for name in TEXT_MODEL_ENV_NAMES])
    embedding_model = os.getenv(EMBEDDING_MODEL_ENV_NAME, "")

    if not text_models:
        raise AssertionError("No text Gemini model env values are set")

    for model in text_models:
        assert_available(available_models, model)
        if not skip_calls:
            response = client.models.generate_content(
                model=model,
                contents="Reply with OK only.",
                config=types.GenerateContentConfig(
                    max_output_tokens=64,
                    thinking_config=types.ThinkingConfig(thinking_budget=0),
                ),
            )
            text = (response.text or "").strip()
            if not text:
                raise AssertionError(f"{model} returned an empty text response")
        print(f"text model ok: {model}")

    if embedding_model:
        assert_available(available_models, embedding_model)
        if not skip_calls:
            response = client.models.embed_content(
                model=embedding_model,
                contents="jobis model smoke",
                config=types.EmbedContentConfig(output_dimensionality=768),
            )
            embeddings = getattr(response, "embeddings", None) or []
            if not embeddings or len(embeddings[0].values) != 768:
                raise AssertionError(f"{embedding_model} did not return a 768-dimensional embedding")
        print(f"embedding model ok: {embedding_model}")

    print("gemini model smoke passed")


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify Gemini model IDs configured in .env.")
    parser.add_argument(
        "--skip-calls",
        action="store_true",
        help="Only verify that configured models appear in models.list(); do not generate text or embeddings.",
    )
    args = parser.parse_args()

    run_smoke(skip_calls=args.skip_calls)


if __name__ == "__main__":
    main()
