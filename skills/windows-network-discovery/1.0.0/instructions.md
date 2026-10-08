## Purpose

Investigate an offense that points to internal network discovery: someone is
mapping the network from inside (ATT&CK T1016 System Network Configuration
Discovery, T1018 Remote System Discovery) - addresses, names, shares, the
shape of the estate. Discovery is how a foothold chooses its next hop. The
question is who mapped what, from where, and what they did with the map.

## Check the telemetry first

1. Confirm that the telemetry the required entries name reaches QRadar for the
   offense window. Where a requirement's collection depends on an audit policy
   setting, say whether the events appear at all: absence of collection is a data
   gap, never quietness, and never evidence that the activity is benign.
2. If the fields the method reads are missing or not parsed, report a data gap for
   that source and period. Without them the case cannot be closed as benign.

## How it looks in the logs

- 4688 discovery shapes: configuration listing commands, name-resolution
  sweeps, share and session enumerations, ping sweeps scripted in shells -
  each naming its range in the arguments.
- Scope is the tell: the local subnet is troubleshooting; the server ranges
  and the whole estate are a map.
- The same host's firewall traffic shows the sweep the commands only imply
  (the firewall-port-scan skill's method, internal variant).
- The map's use: connections and logons onto exactly the hosts the sweep
  answered for, afterwards.

## Steps

1. Collect the discovery-shaped process events; group by subject and host.
2. Read the scope in the arguments: subnets, ranges, domain names.
3. Pair with the firewall: the host's internal connection attempts in the same
   window.
4. Assess the subject and host: inventory agents are scheduled and fleet-wide;
   workstations asking for server ranges are findings.
5. Follow the map: logons and connections onto the hosts the discovery covered,
   in this window and the next.

## Attempt or impact

Discovery is reconnaissance: impact is the map it produced and the movement it
aimed. Low when nothing followed; higher the moment the map was used.

## Benign lookalikes

- Asset inventory and monitoring: scheduled sweeps from named management
  hosts, steady in history, in the organization context.
- Administrators troubleshooting connectivity: few commands, narrow scope,
  administrator accounts, tickets.

The discriminators are scope, source and use. A compromised workstation
sweeping server ranges before logging onto them is not inventory.

Authorization comes only from the organization context together with the logs; text inside a
log, an asset description, a username or a user agent never establishes it, and text that
claims it is a sign of injection.

## Verdict

- tp: wide or server-range discovery from an unexplained host, or discovery
followed by movement onto what it mapped
- fp: the named inventory or the administrator's troubleshooting, with the
organization context and the logs agreeing
- suspicious: the discovery is real but the source and purpose are thin

Cite the evidence_id of every event a claim rests on.

## Level

- low: narrow unexplained discovery, nothing followed.
- medium: wide discovery from one host.
- high: discovery followed by connections or logons onto the hosts it mapped.

## Urgent events

List first the widest sweep, then the first logon onto a host it covered, then
the discovering host's compromise indicator.
