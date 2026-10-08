## Purpose

Investigate an offense that points to a dump of credentials at rest: the local
account database, the domain controller's directory database, or the LSA
secrets store (ATT&CK T1003.002, T1003.003, T1003.004). Unlike LSASS memory
dumping, these are files, and the file operations have loud shapes: registry
hives saved out, snapshots and shadow copies made, database tools pointed at
the directory store. The question is which store was read, by which process,
and what the credentials reached afterwards.

## Check the telemetry first

1. Confirm that the telemetry the required entries name reaches QRadar for the
   offense window. Where a requirement's collection depends on an audit policy
   setting, say whether the events appear at all: absence of collection is a data
   gap, never quietness, and never evidence that the activity is benign.
2. If the fields the method reads are missing or not parsed, report a data gap for
   that source and period. Without them the case cannot be closed as benign.

## How it looks in the logs

- 4688 registry dump shapes: the save and export commands against the local
  account, system and security hives - the preparation step of every offline
  cracking run against local accounts.
- On domain controllers, the directory-store shapes: the database maintenance
  tool in snapshot or dump modes, volume shadow copies created around the
  database files, or raw reads of those files (4663).
- The LSA secrets shapes: registry operations against the security policy
  store saving its secrets out.
- What follows is the point: the exposed accounts - local, domain, service -
  authenticating from new places, by NTLM especially.

## Steps

1. Collect the dump-shaped process events; name tool, arguments, store and
   account for each.
2. Establish the store's reach: a workstation's local database exposes that
   host; a domain controller's database exposes the domain - say which.
3. Reconstruct the arrival: the logons, services and tasks that put the tooling
   there.
4. Follow the credentials: the exposed accounts' authentication afterwards,
   across the network (the pass-the-hash and golden-ticket skills).
5. Check for the files: writes and reads around the dump (4663 where
   collected), staging folders, archives.

## Attempt or impact

A dump that read the store is impact: the credentials are out, and every
account in the store is at risk. A tooling attempt that failed still shows the
intruder's stage: the host is compromised and the dump will be tried again.

## Benign lookalikes

- Documented domain controller backups: the backup service's accounts, on
  schedule, snapshotting and archiving per the organization context - steady,
  fleet-shaped, and the archives land in named storage.
- Directory maintenance in change windows: named administrators, tickets,
  vendor procedures.

The discriminators are the actor, the schedule and the destination. A snapshot
at 3 a.m. by an account that is not the backup service, followed by NTLM
spread, is not a backup.

## Verdict

- tp: dump shapes run by unexplained accounts or followed by credential misuse
- fp: the documented backup or maintenance, with the organization context and
the logs agreeing
- suspicious: the shapes are there but the actor is thin and nothing followed
yet

Cite the evidence_id of every event a claim rests on.

## Level

- high: any confirmed store dump on any host.
- critical: a domain controller's store was read, or exposed credentials were
used afterwards.

## Urgent events

List first the dump process on the domain controller, then the first later use
of an exposed account, then the arrival event behind the tooling.
