# harness — eval suites, scenarios and the synthetic log generator

This package (`ais0c_harness`) holds the evaluation harness. Task T-008 adds the
**synthetic log generator** (`ais0c_harness.loggen`): it produces fully synthetic
but realistically-formatted logs, sends them to the lab QRadar over syslog, and
writes a ground-truth label file for each run. Because production data never
reaches the test environment (decision D-13), this generator is the main source
of the golden dataset.

## Synthetic log generator

```bash
uv run python -m ais0c_harness.loggen run \
    --scenario s2-dcsync \
    --target 192.0.2.10:514 \
    --seed 1 \
    --speed 60
```

- `--scenario` — a scenario name from `harness/scenarios/` (run `... loggen list`).
- `--target host:port` — syslog destination. Required unless `--dry-run`.
- `--seed` — RNG seed. The same scenario and seed always produce the same events.
- `--speed` — time compression: `60` sends a one-hour scenario in one minute.
- `--protocol` — `tcp` (default) or `udp`. See **Transport** below.
- `--dry-run` — write the files but send nothing (`--target` not required).
- `--out-dir` — where the output files go (default: the current directory).
- `--base-time` — ISO-8601 base time. Default: a fixed anchor for `--dry-run`
  (so a dry run is reproducible from the seed alone), the current time for a
  live run.

Each run writes two files to `--out-dir`:

- `<scenario>.<seed>.events.log` — the exact syslog lines, one per line.
- `<scenario>.<seed>.labels.jsonl` — one JSON object per event with
  `event_id`, `time`, `scenario`, `step`, `kind`, `technique` (ATT&CK ID or
  `null`) and `malicious`. This is the ground truth the eval harness scores
  against.

### Determinism

A run is reproducible from `(scenario, seed, base_time)`: one RNG is seeded once
and consumed in a fixed order, event times are `base_time + offset`, and the
output is sorted by `(time, generation index)`. `test_loggen_determinism.py`
asserts two `--dry-run` runs are byte-identical.

### Synthetic-data guarantee (AGENTS.md hard rule 6)

`loggen/synthetic.py` is the single source of truth for what "synthetic" means
and the scanner that proves it. Every rendered line is scanned before it is
written or sent; the generator raises `SyntheticDataError` and sends nothing if
a line contains an IPv4 address outside the RFC 5737 documentation ranges
(`192.0.2.0/24`, `198.51.100.0/24`, `203.0.113.0/24`) or the internal/loopback
ranges, a domain outside the RFC 2606/6761 reserved suffixes (`bank.example` and
friends), or an account or host outside the fixed synthetic pools. Human
usernames avoid a DNS-name shape (dotted name ending in a letter) so they do not
trip the domain scanner.

## Scenarios

Scenarios are YAML files in `harness/scenarios/`. A scenario is a list of steps;
each step emits one or more blocks of events of a single kind and carries the
ground truth (`malicious`, `technique`) for its events.

```yaml
name: s2-dcsync
description: ...
steps:
  - id: dcsync-attack
    malicious: true
    technique: T1003.006
    events:
      - kind: windows_dcsync      # one of the kinds in the table below
        count: 3
        start: 2400               # seconds from base time
        interval: 5               # even spacing; or `spread: N` for seeded jitter
        params:
          account_kind: non_machine
          hosts: [DC-LAB-01]
```

The first three scenarios (acceptance criterion 5):

| Scenario | What it is |
|---|---|
| `s1-arka-plan` | Benign background: normal VPN sessions, firewall allow/deny, Windows logons. The true-negative floor. |
| `s2-dcsync` | A non-machine account using directory-replication rights on a DC (event 4662, hunt pack H2 / T1003.006). Includes the benign replication the H2 rule must not flag: the `MSOL_` Azure AD Connect account and the DCs' own machine accounts. |
| `s3-vpn-yeni-ulke` | A user's first VPN login from a never-before-seen country (T1133), followed by that account reaching internal hosts (T1078). Hunt pack H1. |

## Log source types, DSMs and formats (acceptance criterion 6)

The bank's real firewall and VPN vendors are not yet known, so the first version
uses formats that the lab QRadar's stock DSMs parse. Each was verified on the
lab QRadar 7.6.0 FP1 (API 29.0): the events map to a real QID, not the unparsed
`Event 0` (qid 0).

| Generator kind | Vendor format | QRadar DSM (log source type) | Verified QID(s) |
|---|---|---|---|
| `fortigate_vpn` | FortiOS `key=value` event log, SSL VPN tunnel up/down (`logid` 0101039947 / 0101039948) | Fortinet FortiGate Security Gateway (73) | Tunnel Up, Tunnel Down |
| `fortigate_traffic` | FortiOS `key=value` forward-traffic log (`logid` 0000000013) | Fortinet FortiGate Security Gateway (73) | Firewall Permit, Firewall Deny |
| `windows_logon` | WinCollect MSEVEN6 syslog (Security 4624 / 4625) | Microsoft Windows Security Event Log (12) | Success/Failed logon (4624/4625) |
| `windows_dcsync` | WinCollect MSEVEN6 syslog (Security 4662) | Microsoft Windows Security Event Log (12) | An operation was performed on an object (4662) |

Notes from the lab, important for anyone changing a format:

- **FortiGate auto-discovers.** Sending FortiGate events with a `devname` creates
  the log source automatically (`FortiGate @ VPN-GW-01`). On first discovery,
  QRadar briefly also stores the events under the generic `SIM Generic Log DSM`
  while it detects the source; this does not happen once the source exists.
- **WinCollect fields are tab-delimited, and the DSM requires it.** The same
  Windows fields joined with spaces land as `Event 0` (qid 0). The RFC 3164
  header stays space-separated from the body.
- **The Windows DSM does not auto-discover.** Windows events route by the syslog
  hostname / `Computer` field to an *existing* log source, so the generator's
  host names match the lab's Windows log sources (`DC-LAB-01`, `DC-LAB-02`,
  `WEB-SRV-01`, `FILE-SRV-01`, `SQL-SRV-01`, `APP-SRV-01`). Events for an unknown
  Windows identifier are dropped into `SIM Generic Log DSM`, not parsed.
- FortiGate custom fields such as `srccountry` are **not** QRadar catalog fields
  until a custom property is defined; AQL can read them from the payload until
  the Sigma pipeline (`config/sigma/`, T-005) maps them.

### Transport: TCP vs UDP

Both work. The default is **TCP**, because a fast burst over a single UDP socket
can drop events, and the golden dataset needs every event delivered. UDP is kept
for parity with real appliances and low-rate sends.

### Historical (back-dated) data

QRadar **parses and keeps the event's device time** (`devicetime`) from the
payload, but the event's indexed time (`starttime`, what Ariel's `LAST N` window
and the default time filter use) is the **receipt time**. A back-dated payload is
therefore found at the time it was received, not at its device time: setting the
payload timestamp to three days ago does not place the event in a three-day-old
search window (verified on the lab).

Consequence for long hunts: you cannot seed "12 months ago" data by timestamp
alone. Run long-hunt tests over **scaled windows** (e.g. 12 days standing in for
12 months), or query explicitly on `devicetime` rather than the default event
time.

## Tests

- `test_loggen_determinism.py` — same scenario + seed ⇒ byte-identical output.
- `test_loggen_synthetic.py` — the synthetic-data scanner and generator, with
  negative tests that forbidden input is rejected and the generator fails closed.
- `test_loggen_scenarios.py` — scenario loading/validation and the label file.
- `test_loggen_lab.py` — `@pytest.mark.lab`; sends to the lab and asserts the
  DSMs parse the events (skipped unless `QRADAR_LAB_URL` and `QRADAR_LAB_TOKEN`
  are set).

Reproduce the lab parse check by hand:

```bash
set -a; . ~/.config/ais0c/lab.env; set +a
uv run python -m ais0c_harness.loggen run --scenario s2-dcsync --target "${QRADAR_LAB_URL##*/}:514" --seed 1 --speed 100000
# then, in QRadar, over the last few minutes:
#   SELECT QIDNAME(qid), COUNT(*) FROM events
#   WHERE LOGSOURCETYPENAME(devicetype)='Microsoft Windows Security Event Log'
#   GROUP BY qid LAST 10 MINUTES
# every row should be a real QID (4624/4625/4662), none should be "Event 0".
```
