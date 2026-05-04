from html.parser import HTMLParser

import httpx


class TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._chunks: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript"}:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return

        text = " ".join(data.split())
        if text:
            self._chunks.append(text)

    def text(self) -> str:
        return "\n".join(self._chunks)


async def fetch_page_text(url: str) -> str:
    async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
        response = await client.get(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
                )
            },
        )

    response.raise_for_status()
    parser = TextExtractor()
    parser.feed(response.text)
    text = parser.text()

    if len(text) < 300:
        raise ValueError("페이지 본문을 충분히 읽지 못했습니다.")

    if len(text) > 12000:
        text = text[:12000] + "\n\n[본문이 길어서 앞부분만 사용했습니다.]"

    return text
