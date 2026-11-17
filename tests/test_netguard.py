from __future__ import annotations

import socket

import httpx
import pytest

from tillsync import BlockedURLError, check_url, guarded_client


def public(host: str) -> list[str]:
    return ["93.184.215.14"]


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://localhost:8000/",
        "http://10.0.0.5/api",
        "http://192.168.1.10/",
        "http://172.16.0.1/",
        "http://[::1]/",
        "http://[::ffff:127.0.0.1]/",
        "http://169.254.169.254/latest/meta-data/",
        "http://0.0.0.0/",
        "http://224.0.0.1/",
        "http://100.64.0.1/",
        "ftp://example.com/file.csv",
        "file:///etc/passwd",
        "http:///nohost",
    ],
)
def test_blocks_non_public_destinations(url: str) -> None:
    with pytest.raises(BlockedURLError):
        check_url(url)


def test_public_ip_and_resolved_host_pass() -> None:
    check_url("https://93.184.215.14/")
    check_url("https://sheets.example.com/x.csv", resolver=public)


def test_hostname_resolving_to_private_is_blocked() -> None:
    with pytest.raises(BlockedURLError):
        check_url("https://internal.example.com/", resolver=lambda h: ["10.1.2.3"])


def test_unresolvable_host_is_blocked() -> None:
    def fail(host: str) -> list[str]:
        raise socket.gaierror("nope")

    with pytest.raises(BlockedURLError):
        check_url("https://nowhere.invalid/", resolver=fail)
    with pytest.raises(BlockedURLError):
        check_url("https://weird.example.com/", resolver=lambda h: ["not-an-ip"])


def test_allow_flags() -> None:
    check_url("http://127.0.0.1:8765/", allow_private=True)
    check_url("http://192.168.1.10/", allow_private=True)
    with pytest.raises(BlockedURLError):
        check_url("http://169.254.169.254/", allow_private=True)
    check_url("http://169.254.169.254/", allow_link_local=True)


def test_guarded_client_checks_every_redirect_hop() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "93.184.215.14":
            return httpx.Response(302, headers={"Location": "http://127.0.0.1/admin"})
        return httpx.Response(200, text="secret")

    seen: list[str] = []
    client = guarded_client(
        transport=httpx.MockTransport(handler),
        follow_redirects=True,
        event_hooks={"request": [lambda r: seen.append(str(r.url))]},
    )
    with pytest.raises(BlockedURLError):
        client.get("http://93.184.215.14/start")
    assert seen == ["http://93.184.215.14/start"]


def test_guarded_client_allows_public() -> None:
    client = guarded_client(transport=httpx.MockTransport(lambda r: httpx.Response(200, text="ok")))
    assert client.get("http://93.184.215.14/").text == "ok"
