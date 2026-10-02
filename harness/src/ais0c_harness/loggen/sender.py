"""Syslog transport for the generator.

Each rendered line is one RFC 3164 message. UDP sends one datagram per message;
TCP uses newline framing, which is what QRadar's TCP syslog listener expects.
The generator never opens a transport in ``--dry-run`` mode, so importing this
module has no side effects.
"""

from __future__ import annotations

import socket
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Protocol


class Target(Protocol):
    def send(self, line: str) -> None: ...


@dataclass
class _UdpTarget:
    sock: socket.socket
    address: tuple[str, int]

    def send(self, line: str) -> None:
        self.sock.sendto(line.encode("utf-8"), self.address)


@dataclass
class _TcpTarget:
    sock: socket.socket

    def send(self, line: str) -> None:
        self.sock.sendall(line.encode("utf-8") + b"\n")


def parse_target(value: str) -> tuple[str, int]:
    """Split ``host:port`` into its parts; the port is required."""
    host, separator, port = value.rpartition(":")
    if not separator or not host:
        raise ValueError(f"--target must be host:port, got {value!r}")
    try:
        return host, int(port)
    except ValueError:
        raise ValueError(f"--target port must be an integer, got {port!r}") from None


@contextmanager
def open_target(host: str, port: int, protocol: str) -> Iterator[Target]:
    """Open a UDP or TCP syslog transport to ``host:port``."""
    if protocol == "udp":
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            yield _UdpTarget(sock, (host, port))
        finally:
            sock.close()
    elif protocol == "tcp":
        sock = socket.create_connection((host, port), timeout=10)
        try:
            yield _TcpTarget(sock)
        finally:
            sock.close()
    else:
        raise ValueError(f"unknown protocol {protocol!r}; use 'udp' or 'tcp'")
