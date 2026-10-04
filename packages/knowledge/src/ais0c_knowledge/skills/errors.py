"""Errors of skill loading. Every one is a SkillError, so a caller can catch them together."""


class SkillError(ValueError):
    """A skill is invalid, cannot be read, or clashes with another skill."""


class SkillPermissionError(SkillError):
    """A manifest field reads like a grant of tools, permissions or scripts.

    A skill grants nothing (architecture §7): effective permissions are the agent's toolset ∩
    the workflow policy ∩ the gateway policy.
    """


class SkillIntegrityError(SkillError):
    """An approved skill's files no longer match the content_hash it was approved with."""


class SkillInjectionError(SkillError):
    """Skill text tries to override the agent's rules, imitates a trust-layer tag, or hides
    content from review."""
