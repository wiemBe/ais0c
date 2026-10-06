"""The random sample of operator review (architecture §9, "Zorunlu operatör kontrolü"; decisions
S-10, D-35 and T-42 (5)).

Low and medium FP decisions are sampled: 10% of them, or 30% when a rule of the offense is
undefined in the Analysis Catalog or missing from it. The choice is deterministic, so a replay
or a retry chooses the same: the first 8 bytes of sha256("<case_id>:<evaluation_no>"), as a
big-endian number, modulo 10000, is below the rate in hundredths of a percent.
"""

import hashlib
from typing import Final

from ais0c_contracts import CaseVerdict, Level

# Whole percent to the modulus's hundredths of a percent.
_BASIS_POINTS: Final = 100
_MODULUS: Final = 10000
SAMPLED_LEVELS: Final = frozenset({Level.LOW, Level.MEDIUM})


def sample_value(case_id: str, evaluation_no: int) -> int:
    """The evaluation's place in [0, 10000)."""
    digest = hashlib.sha256(f"{case_id}:{evaluation_no}".encode()).digest()
    return int.from_bytes(digest[:8], "big") % _MODULUS


def sampled(case_id: str, evaluation_no: int, percent: int) -> bool:
    """Whether the evaluation falls inside a `percent` sample."""
    return sample_value(case_id, evaluation_no) < percent * _BASIS_POINTS


def sample_applies(verdict: CaseVerdict, notify_level: Level) -> bool:
    """Only low and medium FP decisions are sampled."""
    return verdict is CaseVerdict.FP and notify_level in SAMPLED_LEVELS
