"""Evidence and call records (architecture §13.1, §21; T-011 criterion 7).

Every call becomes a `tool_calls` row; every successful call also becomes an `evidence` row,
and the evidence ID the agent gets back is that row's key. Both rows are written in one
transaction, so the agent never receives an ID the gateway did not store.

The evidence row says how to find the data again at the source: the query and its hash, the
time range, the tool and its arguments. Its excerpt is short and masked: payload fields are
left out, credential-like fields are masked, and every text value longer than 80 characters is
replaced by its length. The last rule keeps free text out even when a query renamed it
(`UTF8(payload) AS raw`), while IPs, user names and event names stay readable.
"""

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Final

from pydantic import JsonValue
from sqlalchemy.ext.asyncio import AsyncSession

from ais0c_contracts import EvidenceRef, EvidenceSource, TimeWindow, ToolIntent, ToolStatus
from ais0c_policy import FieldFilter, filter_rows, normalize_field_name
from ais0c_storage import PolicyDecision, new_uuid7
from ais0c_storage.repositories import record_evidence, record_tool_call

EVIDENCE_ID_PREFIX: Final = "ev_"
MAX_EXCERPT_LENGTH: Final = 500
MAX_QUERY_TEXT_LENGTH: Final = 4000
MAX_IDENTIFIER_LENGTH: Final = 200
MASK: Final = "***"
MAX_EXCERPT_TEXT: Final = 80

# Payload fields never go into an excerpt.
_EXCERPT_FILTER: Final = FieldFilter(
    drop_fields=frozenset({"payload"}), drop_fields_containing=frozenset({"payload"})
)
# Fields whose values are masked in an excerpt, matched on the normalized field name.
_CREDENTIAL_FRAGMENTS: Final = (
    "password",
    "passwd",
    "secret",
    "token",
    "apikey",
    "authorization",
    "cookie",
    "credential",
)


def new_evidence_id() -> str:
    """`ev_` and a UUIDv7: unique and sortable by creation time."""
    return f"{EVIDENCE_ID_PREFIX}{new_uuid7().hex}"


def tool_query(tool_id: str, arguments: Mapping[str, JsonValue]) -> tuple[str, str]:
    """Query text and hash of a call that is not an AQL query: the tool and its arguments."""
    canonical = json.dumps(
        {"tool": tool_id, "arguments": arguments}, sort_keys=True, separators=(",", ":")
    )
    text = f"{tool_id} {json.dumps(arguments, sort_keys=True, ensure_ascii=False)}"
    return _cut(text, MAX_QUERY_TEXT_LENGTH), hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_evidence(
    *,
    source: EvidenceSource,
    tool_id: str,
    arguments: Mapping[str, JsonValue],
    query_text: str,
    query_hash: str,
    window: TimeWindow,
    rows: Sequence[Mapping[str, JsonValue]],
    retrieved_at: datetime,
    extra_identifiers: Mapping[str, str] | None = None,
) -> EvidenceRef:
    identifiers = {"tool": tool_id, "rows": str(len(rows))}
    for name, value in arguments.items():
        if name != "query_expression" and isinstance(value, str | int | float | bool):
            identifiers[name] = _cut(str(value), MAX_IDENTIFIER_LENGTH)
    identifiers.update(extra_identifiers or {})
    return EvidenceRef(
        evidence_id=new_evidence_id(),
        source=source,
        query_hash=query_hash,
        query_text=_cut(query_text, MAX_QUERY_TEXT_LENGTH),
        time_start=window.start,
        time_end=window.end,
        identifiers=identifiers,
        excerpt=excerpt(rows),
        retrieved_at=retrieved_at,
    )


def excerpt(rows: Sequence[Mapping[str, JsonValue]]) -> str:
    """The rows as masked, compact JSON, cut to 500 characters."""
    masked = [_mask(row) for row in filter_rows(rows, _EXCERPT_FILTER)]
    text = json.dumps(masked, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return _cut(text, MAX_EXCERPT_LENGTH)


async def record_call(
    session: AsyncSession,
    *,
    run_id: str,
    intent: ToolIntent,
    decision: PolicyDecision,
    status: ToolStatus,
    latency_ms: int,
    deny_reason: str | None,
    evidence: EvidenceRef | None,
) -> None:
    """Write the call and, for a successful one, its evidence; the caller owns the transaction."""
    if evidence is not None:
        await record_evidence(session, evidence)
    await record_tool_call(
        session,
        run_id=run_id,
        intent=intent,
        policy_decision=decision,
        status=status,
        latency_ms=latency_ms,
        deny_reason=deny_reason,
        evidence_id=None if evidence is None else evidence.evidence_id,
    )


def _mask(value: JsonValue) -> JsonValue:
    if isinstance(value, dict):
        return {
            key: MASK if _is_credential(key) and item is not None else _mask(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_mask(item) for item in value]
    if isinstance(value, str) and len(value) > MAX_EXCERPT_TEXT:
        return f"[{len(value)} characters]"
    return value


def _is_credential(name: str) -> bool:
    normalized = normalize_field_name(name)
    return any(fragment in normalized for fragment in _CREDENTIAL_FRAGMENTS)


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"
