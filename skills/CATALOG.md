# Skill catalog

The investigation skills, grouped internal and external (decision T-90). A skill is a method
for investigating one kind of event from the defense side: what it looks like in the logs,
how attempt is told from success, what its benign lookalikes are, and how verdict and level
are decided. The format and the rules are in the [README](README.md); how to write the
content is in `docs/impl/skill-authoring.md`.

- **internal**: attacks that appear in the internal network or the identity system: Active
  Directory and Windows authentication, lateral movement, privilege changes, persistence,
  log destruction, credential theft, command-and-control and exfiltration channels, cloud
  identity. Endpoint (Falcon) skills join this group in phase 2.
- **external**: attacks on the internet-facing surface: web applications (WAF logs, OWASP
  classes), internet scanning, login abuse, mail-borne delivery and VPN remote access.

The methods are written against the log types an enterprise SOC collects (Windows Security
and PowerShell events, firewall and VPN traffic, WAF request logs, mail gateway verdicts,
cloud identity sign-in and audit logs); field names are the current products' and swap when
products change, while the method stays. A skill is offered to the investigation agent only
after approval; until then it is a draft below. Approval requires the suites in its manifest
to pass against a lab scenario, so skills without a scenario stay drafts no matter how
complete their text is.

## Internal

| id | version | ATT&CK techniques | telemetry | lab scenario | status | suite |
|---|---|---|---|---|---|---|
| entra-illicit-consent | 1.0.0 | T1528 | Microsoft Entra ID Audit Log, Microsoft Entra ID Sign-in Log, E-mail Security Appliance | - | draft | skill-entra-illicit-consent |
| entra-impossible-travel | 1.0.0 | T1078.004 | Microsoft Entra ID Sign-in Log, E-mail Security Appliance | - | draft | skill-entra-impossible-travel |
| entra-mfa-fatigue | 1.0.0 | T1621, T1621.001 | Microsoft Entra ID Sign-in Log, E-mail Security Appliance | - | draft | skill-entra-mfa-fatigue |
| entra-role-assignment | 1.0.0 | T1098.003 | Microsoft Entra ID Audit Log, Microsoft Entra ID Sign-in Log | - | draft | skill-entra-role-assignment |
| network-anonymizer-use | 1.0.0 | T1090.003 | Fortinet FortiGate Security Gateway, Microsoft Windows Security Event Log | - | draft | skill-network-anonymizer-use |
| network-c2-beaconing | 1.0.0 | T1041, T1071, T1071.001 | Fortinet FortiGate Security Gateway | - | draft | skill-network-c2-beaconing |
| network-data-exfiltration | 1.0.0 | T1041, T1048 | Fortinet FortiGate Security Gateway | - | draft | skill-network-data-exfiltration |
| office-macro-execution | 1.0.0 | T1204.002 | Microsoft Windows Security Event Log | - | draft | skill-office-macro-execution |
| password-spraying | 1.0.0 | T1110, T1110.003 | Microsoft Windows Security Event Log | s5 | draft | skill-password-spraying |
| remote-access-tool-usage | 1.0.0 | T1219 | Fortinet FortiGate Security Gateway, Microsoft Windows Security Event Log | - | draft | skill-remote-access-tool-usage |
| windows-account-creation | 1.0.0 | T1136, T1136.001, T1136.002 | Microsoft Windows Security Event Log | - | draft | skill-windows-account-creation |
| windows-account-discovery | 1.0.0 | T1069, T1069.002, T1087, T1087.002 | Microsoft Windows Security Event Log | - | draft | skill-windows-account-discovery |
| windows-asrep-roasting | 1.0.0 | T1558.004 | Microsoft Windows Security Event Log | - | draft | skill-windows-asrep-roasting |
| windows-brute-force | 1.0.0 | T1110, T1110.001 | Microsoft Windows Security Event Log | - | draft | skill-windows-brute-force |
| windows-certificate-abuse | 1.0.0 | T1649 | Microsoft Windows Security Event Log | - | draft | skill-windows-certificate-abuse |
| windows-command-shell-anomaly | 1.0.0 | T1059.003 | Microsoft Windows Security Event Log, Fortinet FortiGate Security Gateway | - | draft | skill-windows-command-shell-anomaly |
| windows-credential-dumping | 1.0.0 | T1003, T1003.001 | Microsoft Windows Security Event Log | - | draft | skill-windows-credential-dumping |
| windows-credential-store-dump | 1.0.0 | T1003.002, T1003.003, T1003.004 | Microsoft Windows Security Event Log | - | draft | skill-windows-credential-store-dump |
| windows-dcshadow | 1.0.0 | T1207 | Microsoft Windows Security Event Log | - | draft | skill-windows-dcshadow |
| windows-dcsync | 1.0.0 | T1003.006 | Microsoft Windows Security Event Log | s2 | draft | skill-windows-dcsync |
| windows-golden-ticket | 1.0.0 | T1558.001 | Microsoft Windows Security Event Log | - | draft | skill-windows-golden-ticket |
| windows-gpo-modification | 1.0.0 | T1484.001 | Microsoft Windows Security Event Log | - | draft | skill-windows-gpo-modification |
| windows-kerberoasting | 1.0.0 | T1558.003 | Microsoft Windows Security Event Log | s4 | draft | skill-windows-kerberoasting |
| windows-lateral-movement | 1.0.0 | T1021.001, T1021.002, T1021.006 | Microsoft Windows Security Event Log, Fortinet FortiGate Security Gateway | - | draft | skill-windows-lateral-movement |
| windows-lateral-tool-transfer | 1.0.0 | T1570 | Microsoft Windows Security Event Log | - | draft | skill-windows-lateral-tool-transfer |
| windows-masquerading | 1.0.0 | T1036, T1036.003, T1036.005 | Microsoft Windows Security Event Log | - | draft | skill-windows-masquerading |
| windows-network-discovery | 1.0.0 | T1016, T1018 | Microsoft Windows Security Event Log, Fortinet FortiGate Security Gateway | - | draft | skill-windows-network-discovery |
| windows-ntlm-relay | 1.0.0 | T1557.001 | Microsoft Windows Security Event Log | - | draft | skill-windows-ntlm-relay |
| windows-pass-the-hash | 1.0.0 | T1550.002 | Microsoft Windows Security Event Log | - | draft | skill-windows-pass-the-hash |
| windows-pass-the-ticket | 1.0.0 | T1550.003 | Microsoft Windows Security Event Log | - | draft | skill-windows-pass-the-ticket |
| windows-powershell-anomaly | 1.0.0 | T1059, T1059.001 | Microsoft Windows Security Event Log, Microsoft Windows PowerShell, Fortinet FortiGate Security Gateway | - | draft | skill-windows-powershell-anomaly |
| windows-privileged-group-change | 1.0.0 | T1098 | Microsoft Windows Security Event Log | - | draft | skill-windows-privileged-group-change |
| windows-proxy-binary-execution | 1.0.0 | T1218 | Microsoft Windows Security Event Log, Fortinet FortiGate Security Gateway | - | draft | skill-windows-proxy-binary-execution |
| windows-registry-run-key | 1.0.0 | T1547, T1547.001 | Microsoft Windows Security Event Log | - | draft | skill-windows-registry-run-key |
| windows-scheduled-task | 1.0.0 | T1053, T1053.005 | Microsoft Windows Security Event Log | - | draft | skill-windows-scheduled-task |
| windows-security-log-cleared | 1.0.0 | T1070.001 | Microsoft Windows Security Event Log | - | draft | skill-windows-security-log-cleared |
| windows-security-tool-disabled | 1.0.0 | T1562, T1562.001 | Microsoft Windows Security Event Log | - | draft | skill-windows-security-tool-disabled |
| windows-service-install | 1.0.0 | T1543.003 | Microsoft Windows Security Event Log | - | draft | skill-windows-service-install |
| windows-silver-ticket | 1.0.0 | T1558.002 | Microsoft Windows Security Event Log | - | draft | skill-windows-silver-ticket |
| windows-token-manipulation | 1.0.0 | T1134, T1134.001 | Microsoft Windows Security Event Log | - | draft | skill-windows-token-manipulation |
| windows-tool-download | 1.0.0 | T1105 | Microsoft Windows Security Event Log, Fortinet FortiGate Security Gateway | - | draft | skill-windows-tool-download |

## External

| id | version | ATT&CK techniques | telemetry | lab scenario | status | suite |
|---|---|---|---|---|---|---|
| email-phishing | 1.0.0 | T1566, T1566.002 | E-mail Security Appliance, Fortinet FortiGate Security Gateway, Microsoft Windows Security Event Log | - | draft | skill-email-phishing |
| email-sender-spoofing | 1.0.0 | T1566.002 | E-mail Security Appliance, Fortinet FortiGate Security Gateway | - | draft | skill-email-sender-spoofing |
| firewall-port-scan | 1.0.0 | T1046, T1595.001 | Fortinet FortiGate Security Gateway | - | draft | skill-firewall-port-scan |
| network-non-standard-port | 1.0.0 | T1571 | Fortinet FortiGate Security Gateway, Microsoft Windows Security Event Log | - | draft | skill-network-non-standard-port |
| network-tunneling | 1.0.0 | T1071.004, T1572 | Fortinet FortiGate Security Gateway, Microsoft Windows Security Event Log | - | draft | skill-network-tunneling |
| vpn-brute-force | 1.0.0 | T1110 | Fortinet FortiGate Security Gateway | - | draft | skill-vpn-brute-force |
| vpn-new-country | 1.0.0 | T1133 | Fortinet FortiGate Security Gateway | s3 | draft | skill-vpn-new-country |
| web-command-injection | 1.0.0 | T1190 | F5 Networks BIG-IP ASM | - | draft | skill-web-command-injection |
| web-credential-stuffing | 1.0.0 | T1110, T1110.004 | F5 Networks BIG-IP ASM | - | draft | skill-web-credential-stuffing |
| web-deserialization | 1.0.0 | T1190 | F5 Networks BIG-IP ASM, Fortinet FortiGate Security Gateway | - | draft | skill-web-deserialization |
| web-file-upload | 1.0.0 | T1190, T1505.003 | F5 Networks BIG-IP ASM | - | draft | skill-web-file-upload |
| web-flood | 1.0.0 | T1498 | F5 Networks BIG-IP ASM | - | draft | skill-web-flood |
| web-open-redirect | 1.0.0 | T1566.002 | F5 Networks BIG-IP ASM, Fortinet FortiGate Security Gateway | - | draft | skill-web-open-redirect |
| web-path-traversal | 1.0.0 | T1190 | F5 Networks BIG-IP ASM | - | draft | skill-web-path-traversal |
| web-scanning | 1.0.0 | T1595.002 | F5 Networks BIG-IP ASM | s8, s9 | draft | skill-web-scanning |
| web-service-exfiltration | 1.0.0 | T1567, T1567.002 | Fortinet FortiGate Security Gateway, F5 Networks BIG-IP ASM, Microsoft Windows Security Event Log | - | draft | skill-web-service-exfiltration |
| web-sql-injection | 1.0.0 | T1190 | F5 Networks BIG-IP ASM | s6 | draft | skill-web-sql-injection |
| web-ssrf | 1.0.0 | T1190 | F5 Networks BIG-IP ASM | - | draft | skill-web-ssrf |
| web-xss | 1.0.0 | T1189 | F5 Networks BIG-IP ASM | s7 | draft | skill-web-xss |

Several skills share a technique (T1190 across the web family, T1110 across the
login-abuse family): the router offers all of them; today the
orchestrator sees each candidate's ID, required evidence and budget, not its
Purpose, so the choice between them rests on the ID until T-067 adds a
one-sentence summary. Skills whose method
crosses skill boundaries name each other (lateral movement names the service and
share checks; credential dumping names pass-the-hash and golden ticket; phishing
names the consent and stuffing skills). Lab scenario ids refer to
`harness/scenarios/` (s1-s9); a dash means no scenario yet, and with it no
approval.

Entra skills: the bank runs on-prem Active Directory (2026-10-08); this telemetry
does not exist today. The skills stay as drafts with the lowest priority.

E-mail skills: the bank's mail products are Trellix EX, Brightmail and OPSWAT; how
they enter QRadar is open question S-14.
