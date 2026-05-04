from urllib.parse import urlparse

import httpx


def parse_github_repo_url(url: str) -> tuple[str, str]:
    parsed = urlparse(url.strip())
    parts = [part for part in parsed.path.split("/") if part]

    if parsed.netloc not in {"github.com", "www.github.com"} or len(parts) < 2:
        raise ValueError("GitHub 레포 URL 형식이 아니에요. 예: https://github.com/user/repo")

    return parts[0], parts[1].removesuffix(".git")


async def fetch_repo_readme(url: str) -> str:
    owner, repo = parse_github_repo_url(url)
    api_url = f"https://api.github.com/repos/{owner}/{repo}/readme"

    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(
            api_url,
            headers={"Accept": "application/vnd.github.raw"},
        )

    if response.status_code == 404:
        return f"{owner}/{repo} 레포에서 README를 찾지 못했습니다."

    response.raise_for_status()
    readme = response.text.strip()

    if len(readme) > 12000:
        readme = readme[:12000] + "\n\n[README가 길어서 앞부분만 사용했습니다.]"

    return f"레포: {owner}/{repo}\nREADME:\n{readme}"
