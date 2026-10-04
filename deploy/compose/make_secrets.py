"""Make the secret files of the dev stack's "qradar" profile (deploy/compose/README.md; T-018).

    uv run python deploy/compose/make_secrets.py

Writes every missing file under deploy/compose/secrets/ and never changes an existing one, so a
re-run keeps the tokens; delete a file to replace its token.

| File | Holds | Mounted into | Read outside Compose by |
|---|---|---|---|
| `agents/gateway-token-<profile>` | an agent profile's gateway token | mcp-gateway | the workers |
| `executor/gateway-token-<profile>` | the Action Executor's gateway token | mcp-gateway | the executor only |
| `mcp/mcp-token-<instance>` | the gateway's token towards an MCP instance | mcp-gateway, the instance | |
| `qradar/qradar-token-read`, `-note` | a QRadar authorized service token | its MCP instance | |

The profiles and instances come from config/connectors/qradar.yaml. Gateway and MCP tokens are
random. QRadar tokens come from QRadar: they are copied from AIS0C_QRADAR_READ_TOKEN and
AIS0C_QRADAR_NOTE_TOKEN when those are set; otherwise the script names the files still missing
and exits with 1. The read token should only read and the note token only add notes
(architecture §11.2); a lab with a single token may use it for both.

Directories get mode 0700 and files 0644: the containers read the files through bind mounts as
other users (the fork as 1001, the gateway as 10001), and no other user of this machine can
reach them. On an SELinux host the files get the container_file_t label, which Compose's file
secrets need. No token is ever printed.
"""

import argparse
import os
import re
import secrets
import shutil
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

import yaml

HERE: Final = Path(__file__).resolve().parent
CONNECTOR: Final = HERE.parents[1] / "config/connectors/qradar.yaml"
DEFAULT_DIRECTORY: Final = HERE / "secrets"
# Where a profile's token goes: agent profiles have no caller (registry.py in the gateway).
CALLER_DIRECTORIES: Final = {None: "agents", "action-executor": "executor"}
# Each MCP instance's QRadar token: the file, and the variable it is copied from.
QRADAR_TOKENS: Final = {
    "qradar-mcp-read": ("qradar/qradar-token-read", "AIS0C_QRADAR_READ_TOKEN"),
    "qradar-mcp-note": ("qradar/qradar-token-note", "AIS0C_QRADAR_NOTE_TOKEN"),
}
DIRECTORY_MODE: Final = 0o700
FILE_MODE: Final = 0o644
_TOKEN: Final = re.compile(r"[\x21-\x7e]+")


class SecretsError(ValueError):
    """The connector manifest or a QRadar token variable cannot be used."""


@dataclass(frozen=True)
class Plan:
    random: tuple[str, ...]
    """Files that hold a random token, relative to the secrets directory."""
    qradar: Mapping[str, str]
    """Files that hold a QRadar token, with the variable each is copied from."""


@dataclass
class Report:
    created: list[str] = field(default_factory=list[str])
    missing: list[tuple[str, str]] = field(default_factory=list[tuple[str, str]])
    """QRadar token files not written: the file and the variable to set."""


def plan(manifest: Mapping[str, Any]) -> Plan:
    """The secret files of the connector manifest's profiles and instances."""
    random: list[str] = []
    for name, entry in manifest["profiles"].items():
        callers = {tool.get("caller") for tool in entry["tools"]}
        if len(callers) != 1 or (caller := callers.pop()) not in CALLER_DIRECTORIES:
            raise SecretsError(f"profile {name} has no known caller")
        random.append(f"{CALLER_DIRECTORIES[caller]}/gateway-token-{name}")
    instances = sorted(profile["instance"] for profile in manifest["server_profiles"].values())
    random.extend(f"mcp/mcp-token-{instance}" for instance in instances)
    unknown = set(instances) - QRADAR_TOKENS.keys()
    if unknown:
        raise SecretsError(f"no QRadar token file for {', '.join(sorted(unknown))}")
    return Plan(random=tuple(random), qradar=dict(QRADAR_TOKENS[i] for i in instances))


def make_secrets(directory: Path, plan: Plan, environ: Mapping[str, str]) -> Report:
    """Write the files of `plan` that `directory` lacks."""
    report = Report()
    _make_directory(directory)
    for relative in plan.random:
        if _write_new(directory / relative, secrets.token_hex(32)):
            report.created.append(relative)
    for relative, variable in plan.qradar.items():
        if (directory / relative).exists():
            continue
        value = environ.get(variable, "").strip()
        if not value:
            report.missing.append((relative, variable))
            continue
        if not _TOKEN.fullmatch(value):
            raise SecretsError(f"{variable} must hold one token without spaces")
        _write_new(directory / relative, value)
        report.created.append(relative)
    return report


def label_for_containers(directory: Path) -> str | None:
    """Give the files SELinux's container_file_t label; what to run by hand if that fails."""
    if not Path("/sys/fs/selinux/enforce").exists():
        return None
    command = ["chcon", "-R", "-t", "container_file_t", str(directory)]
    chcon = shutil.which("chcon")
    if chcon is not None:
        done = subprocess.run([chcon, *command[1:]], capture_output=True, check=False)  # noqa: S603
        if done.returncode == 0:
            return None
    return f"label the files for containers by hand: {' '.join(command)}"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Make the dev stack's secret files.")
    parser.add_argument("--directory", type=Path, default=DEFAULT_DIRECTORY)
    args = parser.parse_args(argv)
    try:
        manifest = yaml.safe_load(CONNECTOR.read_text(encoding="utf-8"))
        report = make_secrets(args.directory, plan(manifest), os.environ)
    except (OSError, yaml.YAMLError, KeyError, TypeError, SecretsError) as error:
        print(f"make_secrets: {error}", file=sys.stderr)
        return 2
    for relative in report.created:
        print(f"created {relative}")
    for relative, variable in report.missing:
        print(f"missing {relative}: set {variable} to the QRadar token and run again")
    tokens = {os.environ.get(variable, "").strip() for _, variable in QRADAR_TOKENS.values()}
    if len(tokens) == 1 and tokens != {""}:
        print("note: the read and note instances share one QRadar token; acceptable in a lab only")
    problem = label_for_containers(args.directory)
    if problem is not None:
        print(problem)
    return 1 if report.missing else 0


def _make_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(DIRECTORY_MODE)


def _write_new(path: Path, value: str) -> bool:
    """Write `value` to `path` unless the file exists; True when it was written."""
    _make_directory(path.parent)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, FILE_MODE)
    except FileExistsError:
        return False
    with os.fdopen(descriptor, "w", encoding="utf-8") as file:
        # The umask may have narrowed the mode; the containers' users must read the file.
        os.fchmod(file.fileno(), FILE_MODE)
        file.write(value + "\n")
    return True


if __name__ == "__main__":
    sys.exit(main())
