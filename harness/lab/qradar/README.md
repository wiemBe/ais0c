# Lab QRadar rules (T-058)

The offense-opening rules of the lab scenarios. QRadar has no rule that turns the generator's
events into offenses, so each scenario of `harness/scenarios/` that the chain test selects
(`s2` and `s4`-`s9`) has a CRE event rule here, as source: one YAML file under `rules/`. A script
builds one extension zip from them.

```bash
uv run python harness/lab/qradar/build_extension.py --out ais0c-lab-rules.zip
```

The zip holds `info.json` (name, version, rule names) and `content.xml` (the rules). The build is
deterministic: the same sources give the same bytes. The zip is never committed.

## The rules

All names start with `AIS0C LAB - `. A rule adds an event to an offense indexed by the key
below when **all** its conditions hold. Offenses of different rules never share a key between
scenarios (each scenario has its own user or source address), so they do not merge.

| Rule | Scenario | Indexed by | Conditions |
|---|---|---|---|
| `AIS0C LAB - DCSync by a non-machine account` | `s2-dcsync` | username | Log source type Microsoft Windows Security Event Log; QID 5000849 (4662); payload contains `DS-Replication-Get-Changes`; username does not end with `$` and does not start with `MSOL_` (the conditions of PR-T-012; the rule the lab already has, id 100353) |
| `AIS0C LAB - Kerberoasting RC4 service tickets` | `s4-kerberoasting` | username | Windows log source type; QID 5000938 (4769); payload contains `Ticket Encryption Type: 0x17`; username does not end with `$`; at least 5 events of the same username with 5 different service names in 120 seconds |
| `AIS0C LAB - Password spraying from one source` | `s5-password-spraying` | source IP | Windows log source type; payload contains `An account failed to log on` or `Kerberos pre-authentication failed` (4625, 4771); at least 10 events from the same source IP with 5 different usernames in 300 seconds |
| `AIS0C LAB - WAF SQL injection not blocked` | `s6-waf-sqli-gecti` | source IP | Log source type F5 Networks BIG-IP ASM; payload contains `attack_type="SQL-Injection"` and `request_status="alerted"`; at least 5 events from the same source IP in 600 seconds |
| `AIS0C LAB - WAF cross-site scripting not blocked` | `s7-waf-xss-gecti` | source IP | F5 ASM; payload contains `attack_type="Cross Site Scripting (XSS)"` and `request_status="alerted"`; at least 4 events from the same source IP in 600 seconds |
| `AIS0C LAB - WAF signature volume from an external source` | `s8-waf-tarama-engellendi` | source IP | F5 ASM; payload contains `request_status="blocked"`; source IP not in 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16; at least 20 events from the same source IP in 300 seconds |
| `AIS0C LAB - WAF signature volume from an internal source` | `s9-onayli-tarayici` | source IP | F5 ASM; payload contains `request_status="blocked"`; source IP in the three private ranges; at least 20 events from the same source IP in 300 seconds |

The two volume rules exist because `s8` and `s9` differ in where the source is, and an offense for
both is wanted: the chain, not the rule, decides that the internal scanner is harmless.

## Installing

Installing is not part of the task that built these files; it changes the lab and goes through
the user or, with the user's approval, the planner.

1. **Console.** Admin, Extensions Management, Add, choose the zip, Install. Use "Preview" first:
   it validates the content before anything changes. Afterwards check under Offenses, Rules that
   the seven rules are enabled.
2. **API** (with the user's approval): `POST /api/config/extension_management/extensions` with the
   zip as `file` (multipart), then `POST .../extensions_task_status` for the returned id with
   `install_action=PREVIEW`, and `INSTALL` after the preview shows no error. The rule that
   already exists (the DCSync rule) is overwritten in place by the install; choose "overwrite"
   when the console asks.

## Not verified

`content.xml` follows the structure of QRadar's content export, written without an installed
extension to copy from (the earlier DCSync zip of T-012 was not kept). **It has not been through
a console's Preview yet.** If Preview rejects it, the fix is in `build_extension.py`
(`_rule_element`); the rule sources and the tests of their conditions stay as they are.
