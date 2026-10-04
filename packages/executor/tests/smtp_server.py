"""A small SMTP server for the e-mail tests: the parts of RFC 5321 that smtplib uses.

It keeps what it receives and answers as told: `replies` maps "MAIL", "DATA" or a recipient's
address to the reply line it gets instead of "250 OK". It can offer STARTTLS, run implicit TLS
and require AUTH PLAIN. `make_certificates` writes a throwaway CA and a server certificate for
127.0.0.1 and localhost.
"""

import asyncio
import base64
import contextlib
import ssl
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email import message_from_bytes, policy
from email.message import EmailMessage as MimeMessage
from ipaddress import IPv4Address
from pathlib import Path
from types import TracebackType
from typing import Self

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


@dataclass(frozen=True)
class Certificates:
    ca: Path
    """The CA certificate, what a client trusts (PEM)."""
    certificate: Path
    key: Path

    def server_context(self) -> ssl.SSLContext:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(self.certificate, self.key)
        return context


def make_certificates(directory: Path) -> Certificates:
    now = datetime.now(UTC)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "ais0c test CA")])
    ca = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(_key_usage(certificate_sign=True), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), False)
        .sign(ca_key, hashes.SHA256())
    )
    key = ec.generate_private_key(ec.SECP256R1())
    names = [x509.DNSName("localhost"), x509.IPAddress(IPv4Address("127.0.0.1"))]
    certificate = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")]))
        .issuer_name(ca_name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName(names), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(_key_usage(certificate_sign=False), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), False
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), False)
        .sign(ca_key, hashes.SHA256())
    )
    paths = Certificates(
        ca=directory / "ca.pem", certificate=directory / "server.pem", key=directory / "key.pem"
    )
    paths.ca.write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    paths.certificate.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    paths.key.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return paths


def _key_usage(*, certificate_sign: bool) -> x509.KeyUsage:
    return x509.KeyUsage(
        digital_signature=not certificate_sign,
        content_commitment=False,
        key_encipherment=False,
        data_encipherment=False,
        key_agreement=False,
        key_cert_sign=certificate_sign,
        crl_sign=certificate_sign,
        encipher_only=False,
        decipher_only=False,
    )


@dataclass(frozen=True)
class Received:
    """An e-mail the server took."""

    sender: str
    recipients: list[str]
    data: bytes
    tls: bool
    user: str | None

    @property
    def message(self) -> MimeMessage:
        parsed = message_from_bytes(self.data, policy=policy.default)
        assert isinstance(parsed, MimeMessage)
        return parsed


class SmtpServer:
    """An SMTP server on 127.0.0.1 for one test: `async with SmtpServer() as server: ...`."""

    def __init__(
        self,
        *,
        certificates: Certificates | None = None,
        starttls: bool = False,
        implicit_tls: bool = False,
        login: tuple[str, str] | None = None,
        replies: dict[str, str] | None = None,
    ) -> None:
        if (starttls or implicit_tls) and certificates is None:
            raise ValueError("TLS needs certificates")
        self.received: list[Received] = []
        self.commands: list[str] = []
        self.port = 0
        self._certificates = certificates
        self._starttls = starttls
        self._implicit_tls = implicit_tls
        self._login = login
        self._replies = dict(replies or {})
        self._server: asyncio.Server | None = None
        self._writers: set[asyncio.StreamWriter] = set()

    async def __aenter__(self) -> Self:
        context = None
        if self._implicit_tls and self._certificates is not None:
            context = self._certificates.server_context()
        self._server = await asyncio.start_server(self._serve, "127.0.0.1", 0, ssl=context)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._server is None:
            return
        self._server.close()
        for writer in list(self._writers):
            writer.close()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._server.wait_closed(), 5)

    @property
    def mail_commands(self) -> int:
        return self.commands.count("MAIL")

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._writers.add(writer)
        session = _Session(tls=self._implicit_tls)

        async def reply(line: str) -> None:
            writer.write(line.encode("ascii") + b"\r\n")
            await writer.drain()

        try:
            await reply("220 relay.example.com ESMTP test")
            while raw := await reader.readline():
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                verb, _, argument = line.partition(" ")
                verb = verb.upper()
                self.commands.append(verb)
                if verb == "QUIT":
                    await reply("221 Bye")
                    break
                if verb == "STARTTLS" and self._starttls and not session.tls:
                    await reply("220 Go ahead")
                    assert self._certificates is not None
                    await writer.start_tls(self._certificates.server_context())
                    session.tls = True
                    continue
                if verb == "AUTH":
                    await reply(await self._authenticate(session, argument, reader, reply))
                    continue
                if verb == "DATA" and session.recipients:
                    await reply("354 End data with <CR><LF>.<CR><LF>")
                    data = await _read_data(reader)
                    answer = self._replies.get("DATA", "250 OK queued")
                    if answer.startswith("250"):
                        self.received.append(
                            Received(
                                session.sender or "",
                                list(session.recipients),
                                data,
                                session.tls,
                                session.user,
                            )
                        )
                    await reply(answer)
                    continue
                for answer in self._answer(session, verb, argument):
                    await reply(answer)
        except (ConnectionError, ssl.SSLError, asyncio.IncompleteReadError):
            pass  # The client went away, for example after it refused the certificate.
        finally:
            self._writers.discard(writer)
            writer.close()
            with contextlib.suppress(ConnectionError, ssl.SSLError):
                await writer.wait_closed()

    def _answer(self, session: "_Session", verb: str, argument: str) -> list[str]:
        if verb in ("EHLO", "HELO"):
            lines = ["relay.example.com"]
            if self._starttls and not session.tls:
                lines.append("STARTTLS")
            if self._login is not None and session.tls:
                lines.append("AUTH PLAIN")
            lines.append("SIZE 10485760")
            return [
                f"250{'-' if n < len(lines) - 1 else ' '}{text}" for n, text in enumerate(lines)
            ]
        if verb == "MAIL":
            if self._login is not None and session.user is None:
                return ["530 Authentication required"]
            answer = self._replies.get("MAIL", "250 OK")
            if answer.startswith("250"):
                session.sender, session.recipients = _path(argument), []
            return [answer]
        if verb == "RCPT":
            address = _path(argument)
            answer = self._replies.get(address, "250 OK")
            if answer.startswith("250"):
                session.recipients.append(address)
            return [answer]
        if verb == "DATA":
            return ["554 No valid recipients"]
        if verb in ("RSET", "NOOP"):
            if verb == "RSET":
                session.sender, session.recipients = None, []
            return ["250 OK"]
        return ["502 Command not implemented"]

    async def _authenticate(
        self,
        session: "_Session",
        argument: str,
        reader: asyncio.StreamReader,
        reply: Callable[[str], Awaitable[None]],
    ) -> str:
        mechanism, _, initial = argument.partition(" ")
        if self._login is None or not session.tls or mechanism.upper() != "PLAIN":
            return "504 Unrecognized authentication type"
        if not initial:
            await reply("334 ")
            initial = (await reader.readline()).decode("ascii").strip()
        _, user, password = base64.b64decode(initial).decode("utf-8").split("\0")
        if (user, password) != self._login:
            return "535 Authentication credentials invalid"
        session.user = user
        return "235 Authentication successful"


@dataclass
class _Session:
    tls: bool
    sender: str | None = None
    recipients: list[str] = field(default_factory=list[str])
    user: str | None = None


def _path(argument: str) -> str:
    """The address in `FROM:<a@example.com> SIZE=12` or `TO:<a@example.com>`."""
    start, end = argument.find("<"), argument.find(">")
    return argument[start + 1 : end]


async def _read_data(reader: asyncio.StreamReader) -> bytes:
    lines: list[bytes] = []
    while (line := await reader.readline()) not in (b".\r\n", b""):
        lines.append(line[1:] if line.startswith(b".") else line)
    return b"".join(lines)
