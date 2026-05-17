"""Regression test for issue #628.

Python's `urllib.request.urlopen` (via `socket.create_connection`) sets
the SAME timeout on every resolved address attempt. When `getaddrinfo`
returns an unreachable address first (e.g. an IPv6 record that black-holes
at TCP connect), the connect timeout fires before later, reachable
addresses are tried. This blocks `agent.py --command setup` even though
`curl` (which uses Happy Eyeballs) and Seren MCP succeed from the same
machine.

The fix iterates resolved addresses with a SHORT per-address connect
timeout and falls through to the next address on `socket.timeout` /
`OSError`. This test pins that behavior at the helper level: the second
address is reached even when the first one hangs.

This is the critical regression test for #628. We do not test the full
HTTPS-over-fallback path here — `db._http_request` is exercised by
`test_db_auto_create_issue573.py` which patches `_http_get`/`_http_post`
above the transport seam, and integration coverage is owned by the live
`agent.py --command setup` smoke run gated by the issue.
"""

from __future__ import annotations

import socket
from typing import Any

import pytest

import db


class _FakeSocket:
    """Stand-in for `socket.socket`.

    Records the `(family, addr)` it was connected with and either raises
    `socket.timeout` (to simulate the unreachable IPv6 case) or returns
    cleanly. Tracks close() so the test can assert the timed-out socket
    is released before falling through.
    """

    def __init__(self, *, family: int, behavior: str) -> None:
        self.family = family
        self.behavior = behavior
        self.connected_to: tuple[Any, ...] | None = None
        self.timeout_seconds: float | None = None
        self.closed = False

    def settimeout(self, timeout: float | None) -> None:
        self.timeout_seconds = timeout

    def connect(self, addr: tuple[Any, ...]) -> None:
        if self.behavior == "timeout":
            raise socket.timeout("connect hung")
        if self.behavior == "refused":
            raise ConnectionRefusedError("connection refused")
        self.connected_to = addr

    def close(self) -> None:
        self.closed = True


def test_connect_with_fallback_skips_unreachable_address(monkeypatch) -> None:
    """First address (IPv6) hangs; second (IPv4) succeeds.

    Asserts the helper:
      1. Walks `getaddrinfo` results in order.
      2. Closes the failed socket before moving on.
      3. Returns the second socket, connected to the IPv4 address.
      4. Applies the per-address timeout, not the overall budget, to each
         connect attempt.
    """

    ipv6_addr = ("2600:1f18:beef::1", 443, 0, 0)
    ipv4_addr = ("203.0.113.10", 443)

    fake_addrinfo = [
        (socket.AF_INET6, socket.SOCK_STREAM, 0, "", ipv6_addr),
        (socket.AF_INET, socket.SOCK_STREAM, 0, "", ipv4_addr),
    ]
    monkeypatch.setattr(
        db.socket,
        "getaddrinfo",
        lambda host, port, **kwargs: fake_addrinfo,
    )

    created: list[_FakeSocket] = []

    def fake_socket_ctor(family: int, *_args: Any, **_kwargs: Any) -> _FakeSocket:
        behavior = "timeout" if family == socket.AF_INET6 else "ok"
        sock = _FakeSocket(family=family, behavior=behavior)
        created.append(sock)
        return sock

    monkeypatch.setattr(db.socket, "socket", fake_socket_ctor)

    sock = db._connect_with_fallback(
        "api.serendb.com",
        443,
        per_address_timeout=0.05,
    )

    assert len(created) == 2, "both addresses must be attempted"
    ipv6_sock, ipv4_sock = created
    assert ipv6_sock.family == socket.AF_INET6
    assert ipv6_sock.closed, "unreachable socket must be closed before fallback"
    assert ipv6_sock.timeout_seconds == pytest.approx(0.05), (
        "per-address timeout must be applied, not the overall HTTP budget"
    )
    assert sock is ipv4_sock
    assert ipv4_sock.connected_to == ipv4_addr


def test_connect_with_fallback_raises_when_all_addresses_fail(monkeypatch) -> None:
    """All addresses fail → the helper surfaces the most recent error.

    Without this guarantee a configuration / DNS regression would be
    silently swallowed and look like a generic timeout deeper in the
    stack. Bootstrap should fail loudly with the underlying connect
    error.
    """

    fake_addrinfo = [
        (socket.AF_INET6, socket.SOCK_STREAM, 0, "", ("2600::1", 443, 0, 0)),
        (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("203.0.113.10", 443)),
    ]
    monkeypatch.setattr(
        db.socket,
        "getaddrinfo",
        lambda host, port, **kwargs: fake_addrinfo,
    )

    def fake_socket_ctor(family: int, *_args: Any, **_kwargs: Any) -> _FakeSocket:
        return _FakeSocket(family=family, behavior="timeout")

    monkeypatch.setattr(db.socket, "socket", fake_socket_ctor)

    with pytest.raises((socket.timeout, OSError)):
        db._connect_with_fallback(
            "api.serendb.com",
            443,
            per_address_timeout=0.01,
        )
