from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlparse

import httpx

MAX_README_CHARS = 12000
MAX_FILE_CHARS = 6000
MAX_TOTAL_FILE_CHARS = 50000
MAX_SELECTED_FILES = 18
MAX_TREE_PATHS = 250
ProgressCallback = Callable[[str, str, int, int], Awaitable[None]]


@dataclass(frozen=True)
class GithubRepoMetadata:
    owner: str
    repo: str
    repo_key: str
    url: str
    title: str
    default_branch: str
    commit_sha: str
    commit_date: datetime | None


@dataclass(frozen=True)
class GithubRepoContext:
    text: str
    metadata: GithubRepoMetadata

IGNORED_PATH_PARTS = {
    ".git",
    ".github",
    ".idea",
    ".next",
    ".venv",
    "__pycache__",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "out",
    "target",
}

IGNORED_SUFFIXES = {
    ".7z",
    ".avif",
    ".class",
    ".db",
    ".DS_Store",
    ".gif",
    ".ico",
    ".jar",
    ".jpeg",
    ".jpg",
    ".lock",
    ".log",
    ".min.css",
    ".min.js",
    ".mp4",
    ".pdf",
    ".png",
    ".pyc",
    ".sqlite",
    ".svg",
    ".webp",
    ".zip",
}

DEPENDENCY_FILE_NAMES = {
    ".env.example",
    "Dockerfile",
    "compose.yml",
    "compose.yaml",
    "docker-compose.yml",
    "docker-compose.yaml",
    "go.mod",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "settings.gradle",
    "settings.gradle.kts",
    "package.json",
    "pyproject.toml",
    "requirements.txt",
    "Cargo.toml",
    "Gemfile",
}

CONFIG_FILE_NAMES = {
    "application.yml",
    "application.yaml",
    "application.properties",
    "next.config.js",
    "next.config.ts",
    "vite.config.js",
    "vite.config.ts",
    "tsconfig.json",
}

SOURCE_SUFFIXES = (
    "Controller.java",
    "Service.java",
    "Repository.java",
    "Entity.java",
    "Config.java",
    "ExceptionHandler.java",
    "SecurityConfig.java",
    "Application.java",
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
)


def parse_github_repo_url(url: str) -> tuple[str, str]:
    parsed = urlparse(url.strip())
    parts = [part for part in parsed.path.split("/") if part]

    if parsed.netloc not in {"github.com", "www.github.com"} or len(parts) < 2:
        raise ValueError("GitHub 레포 URL 형식이 아니에요. 예: https://github.com/user/repo")

    return parts[0], parts[1].removesuffix(".git")


def normalize_repo_url(owner: str, repo: str) -> str:
    return f"https://github.com/{owner}/{repo}"


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def truncate(text: str, max_chars: int, label: str) -> str:
    text = text.strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n\n[{label} 길어서 앞부분만 사용했습니다.]"


def should_ignore_path(path: str) -> bool:
    parts = set(path.split("/"))
    if parts & IGNORED_PATH_PARTS:
        return True

    return any(path.endswith(suffix) for suffix in IGNORED_SUFFIXES)


def score_file(path: str) -> int:
    name = path.rsplit("/", maxsplit=1)[-1]
    if name in DEPENDENCY_FILE_NAMES:
        return 100
    if name in CONFIG_FILE_NAMES:
        return 80
    if path.startswith("src/test/") or "/test/" in path or "/tests/" in path:
        return 45
    if path.startswith("src/") and path.endswith(SOURCE_SUFFIXES):
        return 60
    if path.endswith(SOURCE_SUFFIXES):
        return 40
    return 0


def select_interview_files(paths: list[str]) -> list[str]:
    candidates = [path for path in paths if not should_ignore_path(path)]
    scored = [(score_file(path), path) for path in candidates]
    selected = [path for score, path in sorted(scored, key=lambda item: (-item[0], item[1])) if score > 0]
    return selected[:MAX_SELECTED_FILES]


async def fetch_text(client: httpx.AsyncClient, url: str, *, accept: str | None = None) -> str:
    headers = {"Accept": accept} if accept else None
    response = await client.get(url, headers=headers)
    response.raise_for_status()
    return response.text


async def report_progress(
    progress: ProgressCallback | None,
    stage: str,
    message: str,
    current: int,
    total: int,
) -> None:
    if progress:
        await progress(stage, message, current, total)


async def fetch_repo_context(url: str, progress: ProgressCallback | None = None) -> GithubRepoContext:
    owner, repo = parse_github_repo_url(url)

    async with httpx.AsyncClient(timeout=10) as client:
        await report_progress(progress, "fetching_repo", "저장소 정보를 확인하는 중입니다.", 1, 7)
        repo_response = await client.get(f"https://api.github.com/repos/{owner}/{repo}")
        repo_response.raise_for_status()
        repo_data = repo_response.json()
        default_branch = repo_data.get("default_branch", "main")

        commit_sha = ""
        commit_date = None
        commit_response = await client.get(f"https://api.github.com/repos/{owner}/{repo}/commits/{default_branch}")
        if commit_response.status_code == 200:
            commit_data = commit_response.json()
            commit_sha = commit_data.get("sha", "") or ""
            commit_date = parse_datetime(
                commit_data.get("commit", {}).get("committer", {}).get("date")
                or commit_data.get("commit", {}).get("author", {}).get("date")
            )
        elif commit_response.status_code != 404:
            commit_response.raise_for_status()

        await report_progress(progress, "fetching_repo", "저장소 언어 정보를 확인하는 중입니다.", 1, 7)
        languages_response = await client.get(f"https://api.github.com/repos/{owner}/{repo}/languages")
        languages_response.raise_for_status()
        languages = languages_response.json()

        await report_progress(progress, "reading_readme", "README와 프로젝트 설명을 읽는 중입니다.", 2, 7)
        readme = f"{owner}/{repo} 레포에서 README를 찾지 못했습니다."
        readme_response = await client.get(
            f"https://api.github.com/repos/{owner}/{repo}/readme",
            headers={"Accept": "application/vnd.github.raw"},
        )
        if readme_response.status_code == 200:
            readme = truncate(readme_response.text, MAX_README_CHARS, "README가")
        elif readme_response.status_code != 404:
            readme_response.raise_for_status()

        await report_progress(progress, "reading_tree", "파일 구조를 살펴보는 중입니다.", 3, 7)
        tree_response = await client.get(
            f"https://api.github.com/repos/{owner}/{repo}/git/trees/{default_branch}?recursive=1"
        )
        tree_response.raise_for_status()
        tree_data = tree_response.json()
        file_paths = [
            item["path"]
            for item in tree_data.get("tree", [])
            if item.get("type") == "blob" and isinstance(item.get("path"), str)
        ]
        total_file_count = len(file_paths)

        visible_paths = [path for path in file_paths if not should_ignore_path(path)]
        selected_files = select_interview_files(file_paths)
        await report_progress(progress, "selecting_files", "면접에 사용할 주요 코드 파일을 고르는 중입니다.", 4, 7)

        file_blocks = []
        total_chars = 0
        for index, path in enumerate(selected_files, start=1):
            if total_chars >= MAX_TOTAL_FILE_CHARS:
                break

            await report_progress(
                progress,
                "reading_files",
                f"전체 {total_file_count}개 파일 중 선별한 주요 코드 파일 {index}/{len(selected_files)}개를 읽는 중입니다.",
                5,
                7,
            )
            raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{default_branch}/{path}"
            try:
                content = await fetch_text(client, raw_url)
            except httpx.HTTPStatusError:
                continue

            content = truncate(content, MAX_FILE_CHARS, f"{path} 파일이")
            total_chars += len(content)
            file_blocks.append(f"[파일: {path}]\n{content}")

    language_summary = ", ".join(f"{name}: {bytes_count}" for name, bytes_count in languages.items()) or "없음"
    topic_summary = ", ".join(repo_data.get("topics", [])) or "없음"
    selected_file_summary = "\n".join(f"- {path}" for path in selected_files) or "선택된 파일 없음"
    tree_summary = "\n".join(f"- {path}" for path in visible_paths[:MAX_TREE_PATHS]) or "파일 트리를 읽지 못했습니다."
    file_context = "\n\n".join(file_blocks) or "읽은 주요 파일이 없습니다."

    text = (
        f"[레포 메타데이터]\n"
        f"이름: {owner}/{repo}\n"
        f"설명: {repo_data.get('description') or '없음'}\n"
        f"기본 브랜치: {default_branch}\n"
        f"최신 커밋: {commit_sha or '확인 실패'}\n"
        f"커밋 날짜: {commit_date.isoformat() if commit_date else '확인 실패'}\n"
        f"주 언어: {repo_data.get('language') or '없음'}\n"
        f"토픽: {topic_summary}\n"
        f"언어 비율 원본(bytes): {language_summary}\n\n"
        f"[README]\n{readme}\n\n"
        f"[선별된 면접 분석 파일]\n{selected_file_summary}\n\n"
        f"[파일 트리 요약]\n{tree_summary}\n\n"
        f"[주요 파일 내용]\n{file_context}"
    )
    return GithubRepoContext(
        text=text,
        metadata=GithubRepoMetadata(
            owner=owner,
            repo=repo,
            repo_key=f"{owner}/{repo}",
            url=normalize_repo_url(owner, repo),
            title=f"{owner}/{repo}",
            default_branch=default_branch,
            commit_sha=commit_sha,
            commit_date=commit_date,
        ),
    )


async def fetch_repo_readme(url: str) -> str:
    return (await fetch_repo_context(url)).text
