"""Skill loading (architecture §7, "Skill'ler"; decision T-21).

Layout: skills/<id>/<version>/ holds exactly skill.yaml and instructions.md. There is no
registry service; the registry is the approved skills in the repository. Files at the top of
skills/ (README.md) are not skills, and symbolic links are refused below it.

Loading one skill:

1. skill.yaml is a valid manifest (SkillManifest) that grants nothing and names the directory
   it sits in, skills/<id>/<version>/.
2. The content hash is computed over skill.yaml and instructions.md (`content_hash`).
3. An approved skill's hash must equal its manifest's content_hash: an approved version is
   never changed in place; a change is a new version.
4. The text that reaches a prompt passes the injection scan (scan.py).

In mode "prod" drafts are not loaded: their manifest is validated, since the status is read
from it, and nothing else. Mode "dev" loads them so the harness can evaluate them before
approval. The router never offers a draft in either mode.
"""

import hashlib
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Final, Literal

import yaml

from ais0c_contracts import SkillRef
from ais0c_knowledge._yaml import load_yaml_text
from ais0c_knowledge.skills.errors import SkillError, SkillInjectionError, SkillIntegrityError
from ais0c_knowledge.skills.manifest import AgentRole, SkillManifest, parse_manifest
from ais0c_knowledge.skills.scan import scan_instructions, scan_text

MANIFEST_FILE: Final = "skill.yaml"
INSTRUCTIONS_FILE: Final = "instructions.md"
# Also the order in which the files are hashed.
SKILL_FILES: Final = (MANIFEST_FILE, INSTRUCTIONS_FILE)
CONTENT_HASH_FIELD: Final = "content_hash"
# Findings listed in one SkillInjectionError.
_MAX_FINDINGS: Final = 10
# Manifest fields that name people or teams.
_NAME_FIELDS: Final = frozenset({"owner", "approved_by"})

Mode = Literal["dev", "prod"]


@dataclass(frozen=True)
class Skill:
    """A loaded skill: its manifest, its instructions and the hash of both."""

    manifest: SkillManifest
    instructions: str
    """instructions.md with LF line endings: the text an agent's prompt includes."""
    content_hash: str
    """The computed hash; equal to manifest.content_hash for an approved skill."""
    directory: Path

    @property
    def ref(self) -> SkillRef:
        """What an agent run records about the skill it used."""
        return SkillRef(
            skill_id=self.manifest.id,
            version=self.manifest.version,
            content_hash=self.content_hash,
        )

    def usable_by(self, agent_role: AgentRole, now: datetime) -> bool:
        """Whether the skill is approved, not expired at `now` and allowed for `agent_role`."""
        manifest = self.manifest
        return (
            manifest.status == "approved"
            and not manifest.is_expired(now)
            and agent_role in manifest.allowed_agent_roles
        )


def version_key(version: str) -> tuple[int, ...]:
    """Sort key of a MAJOR.MINOR.PATCH version."""
    return tuple(int(part) for part in version.split("."))


class SkillRegistry:
    """Loaded skills by ID and version, iterated by ID and then by version."""

    def __init__(self, skills: Iterable[Skill]) -> None:
        """Raises SkillError if two skills have the same ID and version."""
        by_key: dict[tuple[str, str], Skill] = {}
        for skill in skills:
            key = (skill.manifest.id, skill.manifest.version)
            if (other := by_key.get(key)) is not None:
                raise SkillError(
                    f"two skills are {key[0]} {key[1]}: {other.directory} and {skill.directory}"
                )
            by_key[key] = skill
        self._by_key = by_key
        self._skills = tuple(
            sorted(
                by_key.values(),
                key=lambda skill: (skill.manifest.id, version_key(skill.manifest.version)),
            )
        )

    def __iter__(self) -> Iterator[Skill]:
        return iter(self._skills)

    def __len__(self) -> int:
        return len(self._skills)

    def get(self, skill_id: str, version: str) -> Skill | None:
        return self._by_key.get((skill_id, version))

    def latest_approved(self) -> list[Skill]:
        """The highest approved version of each skill, by skill ID.

        A newer approved version replaces every older one, also once it has expired. Drafts do
        not replace anything.
        """
        latest: dict[str, Skill] = {}
        for skill in self._skills:
            if skill.manifest.status == "approved":
                latest[skill.manifest.id] = skill
        return list(latest.values())


def load_skills(root: Path, *, mode: Mode) -> SkillRegistry:
    """Load every skill under `root`, the skills/ directory.

    Raises SkillError at the first skill that cannot be loaded: a broken skill stops the
    loading instead of being left out.
    """
    if mode not in ("dev", "prod"):
        raise ValueError(f"mode must be 'dev' or 'prod', not {mode!r}")
    if not root.is_dir():
        raise SkillError(f"skills directory {root} does not exist")
    skills: list[Skill] = []
    for skill_dir in sorted(root.iterdir()):
        if skill_dir.is_symlink():
            raise SkillError(f"{skill_dir} is a symbolic link; skills are plain directories")
        if not skill_dir.is_dir():
            continue
        versions = sorted(skill_dir.iterdir())
        if not versions:
            raise SkillError(f"{skill_dir} has no versions; skills live in skills/<id>/<version>/")
        for version_dir in versions:
            if version_dir.is_symlink() or not version_dir.is_dir():
                raise SkillError(
                    f"{version_dir}: a skill's directory holds only version directories; "
                    "skills live in skills/<id>/<version>/"
                )
            manifest_text, data, manifest = _read_manifest(version_dir)
            if mode == "prod" and manifest.status == "draft":
                continue
            skills.append(_complete(version_dir, manifest_text, data, manifest))
    return SkillRegistry(skills)


def load_skill(directory: Path) -> Skill:
    """Load one skill, a draft too, from its skills/<id>/<version>/ directory."""
    return _complete(directory, *_read_manifest(directory))


def read_content_hash(directory: Path) -> str:
    """The content hash of the skill in `directory`, whatever its status and manifest."""
    _check_entries(directory)
    manifest_text, instructions = (_read_text(directory / name) for name in SKILL_FILES)
    return content_hash(manifest_text, instructions)


def content_hash(manifest_text: str, instructions_text: str) -> str:
    """The `sha256:` hash of a skill's two files, the same on every machine.

    Line endings become LF and the files go in a fixed order, skill.yaml and then
    instructions.md, each as its name, its length in bytes and its UTF-8 bytes. skill.yaml is
    hashed without its `content_hash:` line, the one field the hash cannot cover; every other
    byte counts, comments included.

    Raises SkillError unless skill.yaml has exactly one line that starts with `content_hash:`
    and that line is the whole top-level field.
    """
    parts = (
        (MANIFEST_FILE, _without_content_hash(normalize_newlines(manifest_text))),
        (INSTRUCTIONS_FILE, normalize_newlines(instructions_text)),
    )
    digest = hashlib.sha256()
    for name, text in parts:
        data = text.encode("utf-8")
        digest.update(f"{name}\0{len(data)}\0".encode())
        digest.update(data)
    return f"sha256:{digest.hexdigest()}"


def normalize_newlines(text: str) -> str:
    """CRLF and lone CR become LF."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _without_content_hash(manifest_text: str) -> str:
    lines = manifest_text.split("\n")
    prefix = f"{CONTENT_HASH_FIELD}:"
    found = [index for index, line in enumerate(lines) if line.startswith(prefix)]
    if len(found) != 1:
        raise SkillError(
            f"skill.yaml needs exactly one line that starts with {prefix!r}, found {len(found)}"
        )
    rest = "\n".join(line for index, line in enumerate(lines) if index != found[0])
    full = _parse_yaml(manifest_text)
    try:
        # An empty document is None: the manifest held nothing but content_hash.
        without = load_yaml_text(rest) or {}
    except yaml.YAMLError:
        without = None
    if not isinstance(full, dict) or without != {
        key: value for key, value in full.items() if key != CONTENT_HASH_FIELD
    }:
        raise SkillError(
            "content_hash must be a top-level field on one line: `content_hash: null` or "
            "`content_hash: sha256:<64 hex digits>`"
        )
    return rest


def _read_manifest(directory: Path) -> tuple[str, object, SkillManifest]:
    """skill.yaml's text, its parsed data and the manifest; the layout is checked first."""
    _check_entries(directory)
    manifest_path = directory / MANIFEST_FILE
    manifest_text = _read_text(manifest_path)
    data = _parse_yaml(manifest_text)
    try:
        manifest = parse_manifest(data)
    except SkillError as error:
        raise type(error)(f"{manifest_path}: {error}") from error
    return manifest_text, data, manifest


def _complete(directory: Path, manifest_text: str, data: object, manifest: SkillManifest) -> Skill:
    """Steps 1 to 4 of loading, once the manifest is read."""
    if (directory.parent.name, directory.name) != (manifest.id, manifest.version):
        raise SkillError(
            f"{directory}: the manifest is {manifest.id} {manifest.version}, so it belongs in "
            f"skills/{manifest.id}/{manifest.version}/"
        )
    instructions = _read_text(directory / INSTRUCTIONS_FILE)
    digest = content_hash(manifest_text, instructions)
    if manifest.status == "approved" and digest != manifest.content_hash:
        raise SkillIntegrityError(
            f"{directory}: the files hash to {digest}, not to the approved content_hash "
            f"{manifest.content_hash}. An approved skill is never changed in place; a change "
            "is a new version."
        )
    if not instructions.strip():
        raise SkillError(f"{directory / INSTRUCTIONS_FILE} is empty")
    _scan(directory, data, instructions)
    return Skill(
        manifest=manifest, instructions=instructions, content_hash=digest, directory=directory
    )


def _check_entries(directory: Path) -> None:
    if directory.is_symlink() or not directory.is_dir():
        raise SkillError(f"{directory} is not a skill directory")
    names = {entry.name for entry in directory.iterdir()}
    if missing := sorted(set(SKILL_FILES) - names):
        raise SkillError(f"{directory}: {', '.join(missing)} missing")
    if extra := sorted(names - set(SKILL_FILES)):
        raise SkillError(
            f"{directory}: a skill holds only {' and '.join(SKILL_FILES)} and runs no scripts; "
            f"remove {', '.join(extra)}"
        )
    for name in SKILL_FILES:
        path = directory / name
        if path.is_symlink() or not path.is_file():
            raise SkillError(f"{path} must be a regular file, not a link or a directory")


def _read_text(path: Path) -> str:
    try:
        data = path.read_bytes()
    except OSError as error:
        raise SkillError(f"cannot read {path}: {error}") from error
    try:
        return normalize_newlines(data.decode("utf-8"))
    except UnicodeDecodeError as error:
        raise SkillError(f"{path} is not UTF-8: {error}") from error


def _parse_yaml(text: str) -> object:
    try:
        return load_yaml_text(text)
    except yaml.YAMLError as error:
        raise SkillError(f"skill.yaml is not valid YAML: {error}") from error


def _scan(directory: Path, data: object, instructions: str) -> None:
    problems = [f"{INSTRUCTIONS_FILE} {finding}" for finding in scan_instructions(instructions)]
    for where, value in _strings(data):
        # Names may be Turkish; the rest of the manifest is English, like the instructions.
        # This includes every nested required_telemetry and required_evidence string because
        # their contents also enter the prompt's trusted Skill section (T-36, T-44).
        scan = scan_text if where in _NAME_FIELDS else scan_instructions
        problems += [f"{MANIFEST_FILE} {where}: {finding.reason}" for finding in scan(value)]
    if problems:
        listed = "; ".join(problems[:_MAX_FINDINGS])
        more = len(problems) - _MAX_FINDINGS
        raise SkillInjectionError(
            f"{directory}: refused by the injection scan: {listed}"
            + (f"; and {more} more" if more > 0 else "")
        )


def _strings(data: object, path: str = "") -> Iterator[tuple[str, str]]:
    """Every string value in parsed YAML, with where it is."""
    if isinstance(data, str):
        yield path, data
    elif isinstance(data, dict):
        for key, value in data.items():
            yield from _strings(value, f"{path}.{key}" if path else str(key))
    elif isinstance(data, list):
        for index, item in enumerate(data):
            yield from _strings(item, f"{path}[{index}]")
