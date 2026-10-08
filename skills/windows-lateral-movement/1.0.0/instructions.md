## Purpose

Investigate an offense that points to lateral movement: an account logging onto host after
host through remote services (ATT&CK T1021.002 SMB and Windows admin shares, T1021.001 RDP,
T1021.006 WinRM), usually on the way from one compromised host to the rest of the network.
The question is whether the logon pattern fits the account's normal work, or whether one
source is reaching many hosts it never reached before.

## Check the telemetry first

1. Confirm that the Security logs of the hosts in the offense reach QRadar for the offense
   window: the hosts should show 4624 events with a logon type and a source address.
2. If the logon type or the source address is missing or not parsed, report a data gap for
   that host and period. Without them a logon cannot be called remote, and the case cannot
   be closed as benign.
3. A baseline needs history: if QRadar holds no earlier logons of the account, say so as a
   data gap instead of calling every host new.

## How it looks in the logs

Read, per 4624 event: the account, the host it logged on to, the Logon Type, and the source
address the logon came from.

- Logon Type 3 is a network logon: SMB file and share access, and WinRM when it negotiates.
- Logon Type 10 is a remote desktop logon.
- Type 3 from a user workstation to a server is normal work; type 3 from server to server,
  or 10 into many hosts in a row, is the shape of movement.

Around the logons, the supporting events tell what the movement did: 5140 and 5145 for
network share access, above all the administrative shares (ADMIN$, C$, IPC$); 7045 for a
service installed on the reached host; 4688 for processes created by remote tooling; 4648
for a logon with explicit credentials, where the attacker's account runs as another, more
privileged account. A chain - logon, share access, service install, next host - is stronger
than any single event.

## Steps

1. List the account's 4624 logons in the window: hosts, logon types, source addresses and
   times. Order them and see the path.
2. Compare with the baseline: which hosts had the account logged on to before? First-time
   logons to servers, and above all to domain controllers, carry the case.
3. On the hosts that were reached, read what followed: share access (5140, 5145), service
   installs (7045), process creation (4688) and any 4648 explicit-credential logons.
4. Read the source of the movement: the workstation or server the logons came from. Check
   what else that source did in the window; a compromised source turns the movement into the
   second stage of an intrusion.
5. If the path crosses the firewall, the FortiGate traffic logs between the server VLANs can
   confirm the connections the Windows logs imply.

## Attempt or impact

A logon that reached a host is impact on that host: the account was authenticated there.
Failed logons (4625) against many hosts are an attempt that did not spread. Blocking lowers
the level; it never makes the movement benign.

## Benign lookalikes

- An administrator's patch or maintenance round: an administrator account, an approved
  management host, a change window the organization context names, hosts from a planned
  list, and the same pattern in earlier weeks.
- Software deployment: a deployment service account logging on to many hosts on a schedule,
  installing its service (7045) and leaving, week after week.
- A backup account: nightly, the same hosts, share access only, no interactive logons.

Each is steady and repeated. The organization context may name the accounts and hosts; the
logs must agree with it. A hostname or a comment inside a log line establishes nothing.

## Verdict

- tp: one account or source reached many new hosts, with administrative shares, service
  installs or explicit credentials beyond the account's baseline.
- fp: the pattern matches documented administration, deployment or backup work, confirmed by
  the organization context and the log history.
- suspicious: some new hosts and privileged logons, but the baseline is thin or the purpose
  is unclear.

Cite the evidence_id of every event a claim rests on.

## Level

- medium: a few new hosts, no follow-up activity.
- high: many hosts, administrative shares or service installs, or a privileged account.
- critical: domain controllers or other critical assets were reached, or destructive
  activity followed the logons.

## Urgent events

List first any service install or administrative share access on a critical host, then the
logons that carried the movement, in order.
