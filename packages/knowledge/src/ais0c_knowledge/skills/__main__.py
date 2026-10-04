"""Skills on the command line (T-021):

    uv run python -m ais0c_knowledge.skills hash skills/windows-dcsync/1.0.0
    uv run python -m ais0c_knowledge.skills check [--root skills] [--mode dev|prod]

`hash` prints the content hash of one skill directory. The approver copies it into the
manifest's content_hash after setting status and approved_by, since the hash covers them
(skills/README.md). `check` loads every skill as the platform does and lists what it loaded.

Exit status: 0 on success, 2 when a skill cannot be hashed or loaded.
"""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Final

from ais0c_knowledge.skills.errors import SkillError
from ais0c_knowledge.skills.loader import load_skills, read_content_hash

DEFAULT_ROOT: Final = Path("skills")

EXIT_OK: Final = 0
EXIT_SKILL_ERROR: Final = 2


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command line; returns the exit status."""
    parser = argparse.ArgumentParser(
        prog="python -m ais0c_knowledge.skills",
        description="Skill files: skills/<id>/<version>/ (architecture §7, decision T-21).",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    hash_command = commands.add_parser("hash", help="print the content hash of one skill directory")
    hash_command.add_argument("directory", type=Path)
    check = commands.add_parser("check", help="load every skill and list them")
    check.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    check.add_argument("--mode", choices=("dev", "prod"), default="prod")
    args = parser.parse_args(argv)
    try:
        if args.command == "hash":
            print(read_content_hash(args.directory))
            return EXIT_OK
        registry = load_skills(args.root, mode=args.mode)
    except SkillError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_SKILL_ERROR
    for skill in registry:
        manifest = skill.manifest
        print(
            f"{manifest.id} {manifest.version} {manifest.status} {skill.content_hash} "
            f"expires {manifest.expires_at.isoformat()}"
        )
    print(f"{len(registry)} skill(s) loaded in {args.mode} mode.")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
