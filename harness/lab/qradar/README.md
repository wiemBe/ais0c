# Lab QRadar rules (T-058, T-061)

The offense-opening rules of the lab scenarios. QRadar has no rule that turns the generator's
events into offenses, so each scenario of `harness/scenarios/` that the chain test selects
(`s2` and `s4`-`s9`) has a CRE event rule here, as source: one YAML file under `rules/`. A script
builds one extension zip from them.

```bash
uv run python harness/lab/qradar/build_extension.py --out ais0c-lab-rules.zip
```

The zip is in the shape of QRadar's own content export and holds two files: `ais0c-lab-rules.xml`
(`<content>` with one `<custom_rule>` per rule: `origin`, `rule_data`, `uuid`, `rule_type`, `id`,
`mod_date`, `create_date`; `rule_data` is the base64 of the rule's `<rule>` XML) and `manifest.txt`
(the extension manifest, JSON). The build is deterministic: the same sources give the same bytes.
The zip is never committed.

A rule's `uuid` is what QRadar matches on. The DCSync rule's `uuid` is written in its source (it is
the installed rule's); the other six are `uuid5(namespace, name)`. So installing the zip again
changes the installed rules in place and adds none. The `id` in the XML is a placeholder: QRadar
assigns its own.

## The rules

All names start with `AIS0C LAB - `. A rule adds an event to an offense indexed by the key
below when **all** its conditions hold. Offenses of different rules never share a key between
scenarios (each scenario has its own user or source address), so they do not merge.

| Rule | Scenario | Indexed by | Conditions |
|---|---|---|---|
| `AIS0C LAB - DCSync by a non-machine account` | `s2-dcsync` | username | Log source type Microsoft Windows Security Event Log; QID 5000849 (4662); payload contains `DS-Replication-Get-Changes`; username does not match `\$$` or `^MSOL_` (the conditions of PR-T-012; the rule the lab already has, id 100353) |
| `AIS0C LAB - Kerberoasting RC4 service tickets` | `s4-kerberoasting` | username | Windows log source type; QID 5000938 (4769); payload contains `Ticket Encryption Type: 0x17`; username does not match `\$$`; at least 5 events of the same username in 2 minutes (no "different service" counter: the rule engine cannot count on that field) |
| `AIS0C LAB - Password spraying from one source` | `s5-password-spraying` | source IP | Windows log source type; payload contains `An account failed to log on` or `Kerberos pre-authentication failed` (4625, 4771); at least 5 different usernames from the same source IP in 5 minutes (QRadar's counter holds one number, so event count and different-value count cannot be asked together) |
| `AIS0C LAB - WAF SQL injection not blocked` | `s6-waf-sqli-gecti` | source IP | Log source type F5 Networks BIG-IP ASM; payload contains `attack_type="SQL-Injection"` and `request_status="alerted"`; at least 5 events from the same source IP in 10 minutes |
| `AIS0C LAB - WAF cross-site scripting not blocked` | `s7-waf-xss-gecti` | source IP | F5 ASM; payload contains `attack_type="Cross Site Scripting (XSS)"` and `request_status="alerted"`; at least 4 events from the same source IP in 10 minutes |
| `AIS0C LAB - WAF signature volume from an external source` | `s8-waf-tarama-engellendi` | source IP | F5 ASM; payload contains `request_status="blocked"`; source IP not in 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16; at least 20 events from the same source IP in 5 minutes |
| `AIS0C LAB - WAF signature volume from an internal source` | `s9-onayli-tarayici` | source IP | F5 ASM; payload contains `request_status="blocked"`; source IP in the three private ranges; at least 20 events from the same source IP in 5 minutes |

Windows are whole minutes (QRadar's counter takes minutes); the source schema has `window_minutes`
and rejects seconds.

### `offenseMapping`

| `index_by` | Rule's `offenseMapping` | Offense indexed by |
|---|---|---|
| `username` | `3` | Username |
| `source_ip` | `0` | Source IP |

All rules have `forceOffenseCreation="true"`.

### What the offense holds

The rules with a counter (s4-s9) add to the offense only the event that crosses the threshold, not
the ones before it; the rest of the evidence is found with AQL. s5 (password spraying) also opens a
second offense, indexed by the target IP, through QRadar's stock login rules (T-82); the chain test
then has two cases for that scenario.

The two volume rules exist because `s8` and `s9` differ in where the source is, and an offense for
both is wanted: the chain, not the rule, decides that the internal scanner is harmless.

## Installing

Installing is not part of the task that built these files; it changes the lab and goes through
the user or, with the user's approval, the planner.

1. **Console.** Admin, Extensions Management, Add, choose the zip. In the install dialog use
   "Preview" first: it validates the content and lists what would change (rules already installed
   with the same `uuid` show as overwrites, not additions). Then Install, choosing "overwrite" for the
   rules that exist. Afterwards check under Offenses, Rules that the seven rules are enabled.
2. **API** (with the user's approval): upload with `POST /api/config/extension_management/extensions`
   (the zip as `file`, multipart), then `POST .../extensions_task_status` for the returned id with
   `install_action=PREVIEW`. The preview lists each rule as an addition or an overwrite (a rule
   whose `uuid` is installed is an overwrite). Then the same call with `install_action=INSTALL`
   and `overwrite=true` (the console's "overwrite"), once the preview shows nothing unexpected;
   poll the task until it completes.
