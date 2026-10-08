## Purpose

Investigate an offense that points to credential dumping: a tool on a Windows host reads
the memory of the local security authority process, where the system keeps the
credentials of everyone who recently logged on (ATT&CK T1003.001, OS Credential Dumping:
LSASS Memory). What is dumped is used silently: pass-the-hash and pass-the-ticket
elsewhere, days later, from other hosts. The dump is the quiet hinge of many intrusions.
The question is which process touched the credential store on which host, how it got
there, and what was done with what it took.

## Check the telemetry first

1. Confirm what the host actually reports. The two roads are process creation events
   (4688, with command lines) and object access events on the LSASS process (4656 and
   4663, which need the object-access audit policy turned on). Say plainly which of the
   two the host collects; a host that collects neither cannot support this case, and that
   is a data gap, not quietness.
2. If the command line or the subject account is missing from the events the host does
   collect, report a data gap. Without them the tool cannot be judged, and the case
   cannot be closed as benign.

## How it looks in the logs

Three shadows, because the act itself is a memory read that the Security log sees only
sideways:

- A process whose command line carries a dump tool's shape: a system library loaded with
  a dump argument, a known tool's name, or a shell redirecting a dump file into a path.
  Read the command line as a field pattern - what program, what argument shape - and name
  what it matches.
- Object access on the LSASS process (4656, 4663): a process requested a handle to the
  credential store. Legitimate software rarely does; the account that ran the process and
  the process's name decide what it means.
- The follow-up: dumped credentials show up as authentication the pass-the-hash and
  golden-ticket skills describe - NTLM spread from the host, logons of accounts that had
  no session there, service accounts working from new places.

A dump often sits minutes behind the foothold: a service install (7045), a scheduled
task, or a logon of a compromised account (4624) on the same host, earlier in the window.

## Steps

1. Collect the host's dump-shaped events: 4688 command lines that match the known shapes,
   and 4656 and 4663 events on the LSASS process. Name the process, the subject and the
   time of each.
2. Reconstruct the arrival: the host's earlier window - logons, service installs,
   scheduled tasks (the other internal skills' methods). The dump tool was put there by
   something; find the something before closing.
3. Read the process's identity as far as the logs allow: its path (system directories
   against user-writable ones), its parent process, and whether its name mimics system
   binaries. Impersonation of system names is part of the finding.
4. Follow the credentials: the accounts that had sessions on the host at dump time -
   their logons afterwards, from new sources, by NTLM (the pass-the-hash skill's method)
   and by Kerberos (the golden-ticket skill's method). Say which accounts were exposed:
   everyone who recently logged on there.
5. Check the neighborhood: the same shapes on other hosts in the window. Dumping spreads
   with the intruder, and one host's event list becomes the network's question list.

## Attempt or impact

A dump attempt the security software killed at the handle request is a blocked attack:
still tp, at a lower level - the host is compromised either way, only the credentials
stayed. A completed dump is impact: everything those accounts could reach is at risk, and
the case's scope is the account list, not the one host.

## Benign lookalikes

- Legitimate administration and support tools that open LSASS handles or read memory:
  antivirus, endpoint agents, some backup and debugging software. They are signed, they
  run from system paths, they sit in the inventory, and they have been doing it on every
  host for months. The organization context names them; the history agrees.
- A vendor engineer running a documented diagnostic during a ticketed session: the
  organization context and the ticket explain the session, and the logs show the
  engineer's logon before the tool ran.

The discriminators are the signature, the path, the history and the arrival: a signed
agent from the inventory with weeks of history is noise; an unsigned binary from a
user-writable path, hours after a suspicious logon, is the case. A process name that
claims to be a system tool proves nothing.

## Verdict

- tp: a dump-shaped process or an LSASS access from an unexplained source, or any of it
  followed by credential misuse elsewhere.
- fp: the named endpoint or administration software, with the inventory and history
  agreeing.
- suspicious: the events point at a tool that could be either, and the coverage cannot
  settle its identity or its arrival.

Cite the evidence_id of every event a claim rests on.

## Level

- high: any confirmed dump attempt on any host - the host is compromised.
- critical: a completed dump on a server, a domain controller or any host where
  privileged accounts had sessions, or dumped credentials used afterwards.

## Urgent events

List first the LSASS access event and its process, then the arrival event behind it, then
the first later use of an exposed account.
