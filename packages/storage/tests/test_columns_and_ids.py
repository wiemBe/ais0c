"""Column types and UUIDv7 without a database."""

import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta, timezone
from itertools import pairwise

import pytest
from sqlalchemy.dialects import postgresql

from ais0c_contracts import AgentTask, CaseReport, Level
from ais0c_storage.columns import ContractJSONB, EnumText, UtcDateTime
from ais0c_storage.ids import Uuid7Generator, new_uuid7

DIALECT = postgresql.dialect()
ISTANBUL = timezone(timedelta(hours=3))


def test_uuid7_layout() -> None:
    before = time.time_ns() // 1_000_000
    value = new_uuid7()
    after = time.time_ns() // 1_000_000

    assert value.version == 7
    assert value.variant == uuid.RFC_4122
    assert before <= value.int >> 80 <= after


def test_uuid7_sorts_by_creation_time() -> None:
    first = new_uuid7()
    time.sleep(0.002)
    second = new_uuid7()

    assert first < second
    assert len({new_uuid7() for _ in range(1000)}) == 1000


def test_ids_of_one_millisecond_strictly_increase() -> None:
    generator = Uuid7Generator(clock=lambda: 1_000_000_000, randbits=lambda _: 17)

    values = [generator() for _ in range(1000)]

    assert all(first < second for first, second in pairwise(values))
    assert all(value.version == 7 for value in values)
    assert all(value.variant == uuid.RFC_4122 for value in values)
    assert {value.int >> 80 for value in values} == {1000}


def test_a_clock_step_back_keeps_the_order() -> None:
    times = iter((1_000_000_000, 990_000_000))
    generator = Uuid7Generator(clock=lambda: next(times), randbits=lambda _: 17)

    first = generator()
    second = generator()

    assert second > first
    assert second.int >> 80 == 1000


def test_random_bit_overflow_moves_to_the_next_millisecond() -> None:
    generator = Uuid7Generator(
        clock=lambda: 1_000_000_000,
        randbits=lambda _: (1 << 74) - 1,
    )

    first = generator()
    second = generator()

    assert second.int >> 80 == (first.int >> 80) + 1
    assert (second.int >> 64) & ((1 << 12) - 1) == 0
    assert second.int & ((1 << 62) - 1) == 0
    assert second.version == 7
    assert second.variant == uuid.RFC_4122


def test_a_new_millisecond_takes_fresh_random_bits() -> None:
    times = iter((1_000_000_000, 1_001_000_000))
    random_values = iter((17, 23))
    calls: list[int] = []

    def randbits(bit_count: int) -> int:
        calls.append(bit_count)
        return next(random_values)

    generator = Uuid7Generator(clock=lambda: next(times), randbits=randbits)

    generator()
    second = generator()

    assert calls == [74, 74]
    assert (second.int >> 64) & ((1 << 12) - 1) == 0
    assert second.int & ((1 << 62) - 1) == 23


def test_ids_from_threads_are_unique_and_ordered_per_thread() -> None:
    generator = Uuid7Generator(clock=lambda: 1_000_000_000, randbits=lambda _: 17)

    def make_ids(_: int) -> list[uuid.UUID]:
        return [generator() for _ in range(500)]

    with ThreadPoolExecutor(max_workers=8) as pool:
        values_by_thread = list(pool.map(make_ids, range(8)))

    values = [value for thread_values in values_by_thread for value in thread_values]
    assert len(set(values)) == 4000
    assert all(
        first < second
        for thread_values in values_by_thread
        for first, second in pairwise(thread_values)
    )


def test_timestamps_are_written_and_read_in_utc() -> None:
    column = UtcDateTime()
    local = datetime(2026, 10, 2, 17, 5, tzinfo=ISTANBUL)

    written = column.process_bind_param(local, DIALECT)
    read = column.process_result_value(local, DIALECT)

    assert written == read == datetime(2026, 10, 2, 14, 5, tzinfo=UTC)
    assert written is not None
    assert written.tzinfo is UTC
    assert column.process_bind_param(None, DIALECT) is None


def test_naive_and_non_datetime_values_are_refused() -> None:
    column = UtcDateTime()
    with pytest.raises(ValueError, match="naive"):
        column.process_bind_param(datetime(2026, 10, 2, 14, 5), DIALECT)  # noqa: DTZ001
    with pytest.raises(TypeError, match="datetime"):
        column.process_bind_param(date(2026, 10, 2), DIALECT)  # type: ignore[arg-type]


def test_enum_text_accepts_only_the_value_set() -> None:
    column = EnumText(Level, allowed=(Level.HIGH, Level.CRITICAL))

    assert column.process_bind_param(Level.HIGH, DIALECT) == "high"
    assert column.process_result_value("critical", DIALECT) is Level.CRITICAL
    with pytest.raises(ValueError, match="not allowed"):
        column.process_bind_param(Level.LOW, DIALECT)
    with pytest.raises(ValueError, match="'severe' is not a valid Level"):
        column.process_bind_param("severe", DIALECT)  # type: ignore[arg-type]


def test_column_types_can_be_cached() -> None:
    """`cache_ok` types need hashable cache keys that tell different settings apart."""
    first = EnumText(Level)._static_cache_key
    assert first == EnumText(Level)._static_cache_key
    assert hash(first) == hash(EnumText(Level)._static_cache_key)
    assert EnumText(Level, allowed=(Level.HIGH,))._static_cache_key != first
    report = ContractJSONB(CaseReport)._static_cache_key
    assert hash(report) == hash(ContractJSONB(CaseReport)._static_cache_key)
    assert report != ContractJSONB(AgentTask)._static_cache_key
