"""T-058: the lab scenario set s4-s9 and the log kinds it added (criteria 1 and 2)."""

from __future__ import annotations

import ipaddress
import random
import re
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ais0c_harness.loggen import synthetic
from ais0c_harness.loggen.__main__ import build
from ais0c_harness.loggen.formats import render
from ais0c_harness.loggen.scenario import GeneratedEvent, generate, load_scenario
from ais0c_harness.loggen.templates import DSM_BINDINGS, TEMPLATES, LogKind

BASE = datetime(2026, 2, 1, tzinfo=UTC)
NEW_SCENARIOS = (
    "s4-kerberoasting",
    "s5-password-spraying",
    "s6-waf-sqli-gecti",
    "s7-waf-xss-gecti",
    "s8-waf-tarama-engellendi",
    "s9-onayli-tarayici",
)
SCENARIOS_DIR = Path(__file__).resolve().parents[1] / "scenarios"


def events_of(name: str, seed: int = 1) -> list[GeneratedEvent]:
    return generate(load_scenario(name), seed=seed, base_time=BASE)


def step(events: list[GeneratedEvent], step_id: str) -> list[GeneratedEvent]:
    return [g for g in events if g.label.step == step_id]


def f5_field(line: str, key: str) -> str:
    match = re.search(rf'(?:^|[ ,:]){key}="([^"]*)"', line)
    assert match, f"{key} is not in {line}"
    return match.group(1)


# --- criterion 1: log kinds, DSM bindings, wire format ------------------------------------


def test_the_new_kinds_have_dsm_bindings() -> None:
    assert DSM_BINDINGS[LogKind.WINDOWS_KERBEROS_TGS].log_source_type == (
        "Microsoft Windows Security Event Log"
    )
    assert DSM_BINDINGS[LogKind.WINDOWS_KERBEROS_TGS].log_source_type_id == 12
    assert DSM_BINDINGS[LogKind.F5_ASM].log_source_type == "F5 Networks BIG-IP ASM"
    assert DSM_BINDINGS[LogKind.F5_ASM].log_source_type_id == 213
    assert set(DSM_BINDINGS) == set(LogKind), "every kind has a binding"


def test_kerberos_service_ticket_has_the_dsm_fields() -> None:
    event = TEMPLATES[LogKind.WINDOWS_KERBEROS_TGS](
        random.Random(  # noqa: S311
            1
        ),
        {
            "encryption_type": "0x17",
            "users": ["branch.user05"],
            "services": ["svc_sql"],
            "hosts": ["DC-LAB-01"],
        },
        BASE,
    )
    line = render(event)
    assert line.startswith("<134>Feb  1 00:00:00 DC-LAB-01 AgentDevice=WindowsLog\t")
    assert "\tEventID=4769\t" in line
    assert "A Kerberos service ticket was requested." in line
    assert "Account Name: branch.user05 " in line
    assert "Service Name: svc_sql " in line
    assert "Ticket Encryption Type: 0x17 " in line
    assert "Client Address: ::ffff:192.168." in line


def test_kerberos_ticket_rejects_an_unknown_cipher_and_a_real_looking_service() -> None:
    base = {"users": ["analyst1"], "services": ["svc_sql"], "hosts": ["DC-LAB-01"]}
    with pytest.raises(ValueError, match="encryption_type"):
        TEMPLATES[LogKind.WINDOWS_KERBEROS_TGS](
            random.Random(  # noqa: S311
                1
            ),
            {**base, "encryption_type": "0x1"},
            BASE,
        )
    with pytest.raises(synthetic.SyntheticDataError, match="service"):
        TEMPLATES[LogKind.WINDOWS_KERBEROS_TGS](
            random.Random(  # noqa: S311
                1
            ),
            {**base, "services": ["krbtgt_real"]},
            BASE,
        )


def test_kerberos_preauth_failure_has_the_dsm_fields() -> None:
    event = TEMPLATES[LogKind.WINDOWS_LOGON](
        random.Random(  # noqa: S311
            1
        ),
        {
            "event_id": "4771",
            "users": ["branch.user01"],
            "hosts": ["DC-LAB-01"],
            "src_pool": ["10.50.7.23"],
        },
        BASE,
    )
    line = render(event)
    assert "\tEventID=4771\t" in line
    assert "\tKeywords=Audit Failure\t" in line
    assert "Kerberos pre-authentication failed." in line
    assert "Account Name: branch.user01 " in line
    assert "Client Address: ::ffff:10.50.7.23 " in line
    assert "Failure Code: 0x18" in line


def test_failed_logon_names_the_account_where_the_dsm_reads_it() -> None:
    # The subject of a 4625 is "-"; the failed account is under "Account For Which
    # Logon Failed". A "New Logon" section left the lab's username empty.
    event = TEMPLATES[LogKind.WINDOWS_LOGON](
        random.Random(  # noqa: S311
            1
        ),
        {
            "event_id": "4625",
            "logon_type": "3",
            "users": ["branch.user03"],
            "hosts": ["DC-LAB-01"],
            "src_pool": ["10.50.7.23"],
        },
        BASE,
    )
    line = render(event)
    assert "\tEventID=4625\t" in line
    assert "\tKeywords=Audit Failure\t" in line
    assert "New Logon:" not in line
    assert re.search(
        r"Account For Which Logon Failed: Security ID: \S+ Account Name: branch\.user03 ", line
    )
    assert "Source Network Address: 10.50.7.23 " in line


def test_f5_asm_line_has_the_syslog_shape_the_dsm_expects() -> None:
    event = TEMPLATES[LogKind.F5_ASM](
        random.Random(  # noqa: S311
            1
        ),
        {"attack": "sqli", "request_status": "alerted", "src_pool": ["198.51.100.23"]},
        BASE,
    )
    line = render(event)
    assert line.startswith('<134>Feb  1 00:00:00 waf-prod-01 ASM:unit_hostname="')
    assert f5_field(line, "ip_client") == "198.51.100.23"
    assert f5_field(line, "request_status") == "alerted"
    assert f5_field(line, "attack_type") == "SQL-Injection"
    assert f5_field(line, "violations") == "Attack signature detected"
    assert f5_field(line, "date_time") == "2026-02-01 00:00:00"
    assert f5_field(line, "response_code") == "200"
    # key="value" pairs separated by commas, no stray double quote inside a value.
    body = line.split(" ASM:", 1)[1]
    assert re.fullmatch(r'(?:[a-z_]+="[^"]*",)*[a-z_]+="[^"]*"', body)


def test_f5_asm_blocked_request_has_no_application_response() -> None:
    event = TEMPLATES[LogKind.F5_ASM](
        random.Random(  # noqa: S311
            1
        ),
        {"attack": "xss", "request_status": "blocked", "src_pool": ["203.0.113.5"]},
        BASE,
    )
    line = render(event)
    assert f5_field(line, "request_status") == "blocked"
    assert f5_field(line, "response_code") == "0"
    assert f5_field(line, "attack_type") == "Cross Site Scripting (XSS)"


def test_f5_asm_rejects_bad_parameters() -> None:
    rng = random.Random(  # noqa: S311
        1
    )
    with pytest.raises(ValueError, match="attack"):
        TEMPLATES[LogKind.F5_ASM](rng, {"attack": "nope", "request_status": "blocked"}, BASE)
    with pytest.raises(ValueError, match="request_status"):
        TEMPLATES[LogKind.F5_ASM](rng, {"attack": "sqli", "request_status": "passed"}, BASE)
    with pytest.raises(synthetic.SyntheticDataError):
        TEMPLATES[LogKind.F5_ASM](
            rng,
            {"attack": "sqli", "request_status": "blocked", "src_pool": ["8.8.8.8"]},
            BASE,
        )
    with pytest.raises(synthetic.SyntheticDataError, match="host"):
        TEMPLATES[LogKind.F5_ASM](
            rng,
            {"attack": "sqli", "request_status": "blocked", "hosts": ["real-waf-1"]},
            BASE,
        )


@pytest.mark.parametrize("scenario", NEW_SCENARIOS)
def test_every_new_scenario_renders_synthetic_deterministic_lines(scenario: str) -> None:
    # build() scans each rendered line for non-synthetic data and raises if it finds any.
    first = build(scenario_name=scenario, seed=7, base_time=BASE)[1]
    second = build(scenario_name=scenario, seed=7, base_time=BASE)[1]
    assert first == second
    assert first != build(scenario_name=scenario, seed=8, base_time=BASE)[1]


# --- criterion 2: scenarios ---------------------------------------------------------------


def test_the_scenario_set_is_complete() -> None:
    names = {p.stem for p in SCENARIOS_DIR.glob("*.yaml")}
    assert {"s2-dcsync", "s3-vpn-yeni-ulke", *NEW_SCENARIOS} <= names


@pytest.mark.parametrize("scenario", NEW_SCENARIOS)
def test_every_scenario_states_its_expected_decision_in_its_header(scenario: str) -> None:
    header = (SCENARIOS_DIR / f"{scenario}.yaml").read_text(encoding="utf-8").split("name:", 1)[0]
    assert "Expected decision:" in header
    assert "Rationale:" in header


@pytest.mark.parametrize("scenario", NEW_SCENARIOS)
def test_background_and_attack_are_separate_steps(scenario: str) -> None:
    steps = load_scenario(scenario).steps
    assert len(steps) >= 2
    assert any(not s.malicious for s in steps), "no benign background step"


def test_kerberoasting_is_a_burst_of_rc4_tickets_for_many_services() -> None:
    for seed in range(1, 21):
        attack = step(events_of("s4-kerberoasting", seed), "kerberoast-requests")
        assert len(attack) == 16
        assert {g.event.username for g in attack} == {"branch.user05"}
        assert {g.event.extra["encryption_type"] for g in attack} == {"0x17"}
        assert len({g.event.extra["service"] for g in attack}) == 8, seed
        span = attack[-1].label.time - attack[0].label.time
        assert span.total_seconds() <= 30
        assert all(g.label.malicious and g.label.technique == "T1558.003" for g in attack)


def test_kerberoasting_baseline_has_aes_tickets_and_little_rc4() -> None:
    events = events_of("s4-kerberoasting")
    benign = [g for g in events if g.event.kind is LogKind.WINDOWS_KERBEROS_TGS]
    benign = [g for g in benign if not g.label.malicious]
    ciphers = Counter(g.event.extra["encryption_type"] for g in benign)
    assert ciphers["0x12"] == 12
    assert ciphers["0x17"] == 2
    assert not any(g.label.malicious for g in events if g.label.step.startswith("benign-"))


def test_password_spraying_is_one_source_against_many_accounts_then_a_success() -> None:
    events = events_of("s5-password-spraying")
    failures = step(events, "spray-failed-logons") + step(events, "spray-kerberos-preauth-failures")
    assert len(failures) == 36
    assert {g.event.src for g in failures} == {"10.50.7.23"}
    assert len({g.event.username for g in failures}) >= 8
    assert Counter(g.event.extra["event_id"] for g in failures) == {"4625": 24, "4771": 12}
    # A few attempts per account, not a run of attempts on one account.
    assert max(Counter(g.event.username for g in failures).values()) <= 8
    success = step(events, "spray-success")
    assert [(g.event.extra["event_id"], g.event.src) for g in success] == [("4624", "10.50.7.23")]
    assert success[0].label.time > max(g.label.time for g in failures)
    assert all(g.label.malicious for g in failures + success)


def test_password_spraying_baseline_failures_are_one_users_typos() -> None:
    typos = step(events_of("s5-password-spraying"), "benign-typos")
    assert len(typos) == 2
    assert {g.event.username for g in typos} == {"analyst1"}
    assert not any(g.label.malicious for g in typos)


@pytest.mark.parametrize(
    ("scenario", "step_id", "count", "attack_type", "source", "technique"),
    [
        ("s6-waf-sqli-gecti", "sqli-not-blocked", 8, "SQL-Injection", "198.51.100.23", "T1190"),
        (
            "s7-waf-xss-gecti",
            "xss-not-blocked",
            6,
            "Cross Site Scripting (XSS)",
            "203.0.113.61",
            "T1189",
        ),
    ],
)
def test_unblocked_web_attacks_are_alerted_not_blocked(
    scenario: str, step_id: str, count: int, attack_type: str, source: str, technique: str
) -> None:
    attack = step(events_of(scenario), step_id)
    assert len(attack) == count
    lines = [render(g.event) for g in attack]
    assert {f5_field(line, "request_status") for line in lines} == {"alerted"}
    assert {f5_field(line, "attack_type") for line in lines} == {attack_type}
    assert {f5_field(line, "ip_client") for line in lines} == {source}
    assert all(g.label.malicious and g.label.technique == technique for g in attack)


def test_web_attack_baseline_is_traffic_and_blocked_probes_from_other_sources() -> None:
    events = events_of("s6-waf-sqli-gecti")
    probes = step(events, "benign-blocked-probes")
    assert {f5_field(render(g.event), "request_status") for g in probes} == {"blocked"}
    assert "198.51.100.23" not in {g.event.src for g in probes}
    assert len(step(events, "web-traffic-background")) == 20
    assert not any(g.label.malicious for g in probes)


def test_external_scan_is_many_signatures_all_blocked_and_labelled_hostile() -> None:
    scan = step(events_of("s8-waf-tarama-engellendi"), "external-scan-blocked")
    lines = [render(g.event) for g in scan]
    assert len(scan) == 40
    assert {f5_field(line, "request_status") for line in lines} == {"blocked"}
    assert {f5_field(line, "response_code") for line in lines} == {"0"}
    assert len({f5_field(line, "attack_type") for line in lines}) >= 3
    source = {f5_field(line, "ip_client") for line in lines}
    assert source == {"192.0.2.88"}
    assert synthetic.ip_is_synthetic("192.0.2.88")
    assert all(g.label.malicious and g.label.technique == "T1595.002" for g in scan)


def test_approved_scanner_is_harmless_by_its_evidence_not_by_a_note() -> None:
    events = events_of("s9-onayli-tarayici")
    scan = step(events, "approved-scan-blocked")
    lines = [render(g.event) for g in scan]
    source = {f5_field(line, "ip_client") for line in lines}
    # The source is inside the bank's network (RFC 1918), not a documentation address.
    assert source == {"10.30.5.10"}
    assert any(ipaddress.ip_address("10.30.5.10") in net for net in synthetic.INTERNAL_NETWORKS)
    # Every request is blocked, none reached the application.
    assert len(scan) == 40
    assert {f5_field(line, "request_status") for line in lines} == {"blocked"}
    assert {f5_field(line, "response_code") for line in lines} == {"0"}
    # Every request matches an attack signature; there is no other request from that source.
    assert all(f5_field(line, "sig_ids") != "" for line in lines)
    other = [g for g in events if g.event.src == "10.30.5.10" and g not in scan]
    assert other == []
    # The scanner names itself and the change ticket of its maintenance window.
    assert all("maintenance-window CHG-LAB-0042" in line for line in lines)
    assert not any(g.label.malicious for g in events)


def test_the_two_blocked_scan_scenarios_differ_in_where_the_source_is() -> None:
    external = {
        g.event.src for g in step(events_of("s8-waf-tarama-engellendi"), "external-scan-blocked")
    }
    internal = {g.event.src for g in step(events_of("s9-onayli-tarayici"), "approved-scan-blocked")}
    assert not any(ip.startswith("10.") for ip in external)
    assert all(ip.startswith("10.") for ip in internal)
