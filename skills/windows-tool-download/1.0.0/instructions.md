## Purpose

Investigate an offense that points to ingress tool transfer: a host fetched a
file from outside with tools that need no browser (ATT&CK T1105, Ingress Tool
Transfer). Stage two of almost every intrusion is a download. The question is
what was fetched, with what, from where, and what ran afterwards.

## Check the telemetry first

1. Confirm that the telemetry the required entries name reaches QRadar for the
   offense window. Where a requirement's collection depends on an audit policy
   setting, say whether the events appear at all: absence of collection is a data
   gap, never quietness, and never evidence that the activity is benign.
2. If the fields the method reads are missing or not parsed, report a data gap for
   that source and period. Without them the case cannot be closed as benign.

## How it looks in the logs

- 4688 download shapes: the certificate utility verifying or fetching a remote
  file, the background transfer service queueing one, the transfer utility
  pulling one, archive tools fetching and extracting - each with a remote
  source and a local destination in the arguments.
- The destination path decides the suspicion: program directories receive
  installs; temp, profile and download paths receive implants.
- The follow-through: the fetched file executing (4688), archives unpacking
  into staging folders, and the host's traffic to the source.
- Repeat business: the same source or the same fetched name across hosts is
  distribution - of malware or of software, and the inventory decides which.

## Steps

1. Collect the download-shaped process events; for each: tool, source,
   destination, subject, time.
2. Assess destinations: user-writable paths stand out; name them.
3. Follow the file: its execution or extraction afterwards, and under which
   account.
4. Assess the sources: hosting, reputation, and which other hosts in the
   network fetch from them.
5. Reconstruct the arrival: the session or compromise that led to the fetch
   (the earlier skills' indicators).

## Attempt or impact

A fetch that landed is half an impact: the tool is on the host. Its execution
is the other half. A blocked fetch is an attempt that still maps the
intruder's staging.

## Benign lookalikes

- Software distribution: fleet agents pulling signed packages from named
  repositories, program destinations, inventory match.
- Administrators fetching tools from the organization's known mirrors for
  ticketed work: administrator accounts, management hosts, change records.

The discriminators are the source, the destination and the execution. A system
utility fetching an executable into temp, then running it, is not
distribution.

## Verdict

- tp: a system utility fetched an executable or script into a user-writable
path, above all one that then ran
- fp: the documented distribution or the administrator's mirror fetch, with
the organization context and the logs agreeing
- suspicious: the fetch is real but the file never appeared again

Cite the evidence_id of every event a claim rests on.

## Level

- medium: a fetch into temp that never executed.
- high: the fetched file executed.
- critical: the fetched tool reached credentials, moved laterally or opened a
channel.

## Urgent events

List first the fetch whose file executed, then the fetch itself, then the
host's traffic to the source.
