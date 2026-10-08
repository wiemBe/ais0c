## Purpose

Investigate an offense that points to data leaving the network: an internal host sent far
more bytes to an external destination than it ever sends (ATT&CK T1048, Exfiltration Over
Alternative Protocol; T1041 when the channel is a command-and-control link). The volume
is the signal; the story around it decides. Backups, updates and video calls are big and
legitimate; an archive walking out of a server at night is not. The question is what
left, from where, to where, and what the sending host was doing before it did.

The telemetry here is the firewall's traffic log; field names below are FortiGate's, and
any firewall, proxy or flow collector carries the same facts under its own names.

## Check the telemetry first

1. Confirm that the firewall's traffic log reaches QRadar for the offense window and for
   a baseline period before it. Volume without a baseline is just a big number: report a
   data gap if the history is missing.
2. If the internal host address, the destination or the byte counts are missing, report a
   data gap. Without them nothing can be compared, and the case cannot be closed as
   benign.

## How it looks in the logs

Read, per flow: the internal host, the destination and port, the byte counts both ways,
the duration and the time.

- Volume beyond baseline: the host's outbound bytes in the window against its own history
  and its peers'. State the ratio; ten times a host's busiest day is a finding whatever
  the absolute number.
- The shape of the send: one long transfer, or many sessions spread over hours (styled to
  look like traffic), or a burst at a specific hour. Small session counts with big bytes
  are archive moves; thousands of small sessions are staged theft.
- The destination: newly contacted, or known; a consumer service, a hosting provider, or
  an unknown address; the same destination receiving from several internal hosts is a
  collection point.
- The port and protocol: expected ones hide in plain sight; unusual ports and protocols
  are both louder and more suspicious.
- On the web path, the WAF's request log shows the same story in responses: one client
  pulling far more from the application than any client does.

## Steps

1. Quantify the transfer: internal host, destination, bytes, sessions, time span, and the
   ratio to the host's baseline and to its peers'.
2. Assess the destination: first contact (find it in the history), geolocation and
   reputation where the tooling offers them, other internal hosts sending there, and
   whether the organization context knows it - a partner, a backup provider, a cloud
   tenant.
3. Read the sending host's window: compromise indicators before the transfer (the
   internal skills' signs - logons, services, tasks, beaconing), and which process could
   have produced the volume where the host's telemetry shows it.
4. Look for staging: before big sends, intruders collect and compress. Archive tool
   process events, sudden reads of file shares, database dumps - what the host telemetry
   offers; say data gap where it does not.
5. Check both directions: the inbound side of the same channel tells whether instructions
   came back (the network-c2-beaconing skill's method applies).

## Attempt or impact

Data that left the network is impact; there is no blocked variant of the offense itself -
a blocked transfer appears in a different case (denies, dropped connections) and the
exfiltration intent is read from it. The level follows what the data was: the logs
rarely show content, so the host's role and the volume's shape carry the assessment, and
what cannot be known is said plainly.

## Benign lookalikes

- Backups, replication and sync: scheduled, large, one-directional, to destinations the
  organization context names, from hosts whose job it is.
- Software distribution and updates: many hosts sending similar volumes to known update
  infrastructure, at patch hours.
- Video calls, migration projects, media work: big, but matching the host's human
  pattern and history.

The discriminators are the destination's documentation, the schedule's explanation, the
host's role - and the absence of compromise indicators on the sending host. A byte count
alone convicts no one; a new destination plus a compromised host plus an archive shape
does.

## Verdict

- tp: outbound volume far beyond baseline to an unexplained destination, above all from a
  host with compromise indicators or with staging before the send.
- fp: the documented backup, update or project transfer, with the organization context
  and the logs agreeing.
- suspicious: the volume is abnormal but the destination is thinly known and the host
  shows no indicators.

Cite the evidence_id of every event a claim rests on.

## Level

- medium: an unexplained transfer of ordinary-looking volume from a workstation.
- high: large or shaped (staged, archived, off-hours) transfers, or any transfer from a
  server.
- critical: transfers from servers holding customer or credentials data, from domain
  controllers, or during an active incident.

## Urgent events

List first the largest single session of the transfer, then the transfer's first
connection, then the sending host's earliest compromise indicator.
