from html.parser import HTMLParser
from ipaddress import ip_address
from socket import getaddrinfo
from urllib.parse import urljoin, urlparse

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


def is_blocked_host(hostname: str) -> bool:
    normalized = hostname.strip().strip("[]").lower()
    if not normalized:
        return True

    if normalized == "localhost" or normalized.endswith(".localhost"):
        return True

    try:
        address = ip_address(normalized)
    except ValueError:
        return False

    return (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def assert_safe_fetch_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("http 또는 https URL만 읽을 수 있습니다.")

    if parsed.username or parsed.password:
        raise ValueError("사용자 정보가 포함된 URL은 읽을 수 없습니다.")

    if is_blocked_host(parsed.hostname):
        raise ValueError("localhost 또는 내부 네트워크 주소는 읽을 수 없습니다.")

    try:
        address_infos = getaddrinfo(parsed.hostname, parsed.port)
    except OSError as exc:
        raise ValueError("URL 호스트 주소를 확인하지 못했습니다.") from exc

    for address_info in address_infos:
        resolved_host = address_info[4][0]
        if is_blocked_host(resolved_host):
            raise ValueError("내부 네트워크로 연결되는 URL은 읽을 수 없습니다.")


async def fetch_page_text(url: str) -> str:
    async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
        current_url = url
        for _ in range(5):
            assert_safe_fetch_url(current_url)
            response = await client.get(
                current_url,
                follow_redirects=False,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
                    )
                },
            )
            if not response.is_redirect:
                break

            location = response.headers.get("location")
            if not location:
                break
            current_url = urljoin(str(response.url), location)
        else:
            raise ValueError("리다이렉트가 너무 많습니다.")

    response.raise_for_status()
    parser = TextExtractor()
    parser.feed(response.text)
    text = parser.text()

    if len(text) < 300:
        raise ValueError("페이지 본문을 충분히 읽지 못했습니다.")

    if len(text) > 12000:
        text = text[:12000] + "\n\n[본문이 길어서 앞부분만 사용했습니다.]"

    return text
