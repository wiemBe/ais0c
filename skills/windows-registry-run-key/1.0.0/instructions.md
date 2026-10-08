## Purpose

Investigate an offense that points to autostart persistence: a registry run
key or a startup folder entry was changed so that the attacker's program runs
at every logon (ATT&CK T1547.001, Registry Run Keys / Startup Folder). The
question is which key was changed, by whom, to run what, and from where the
binary lives.

## Check the telemetry first

1. Confirm that the telemetry the required entries name reaches QRadar for the
   offense window. Where a requirement's collection depends on an audit policy
   setting, say whether the events appear at all: absence of collection is a data
   gap, never quietness, and never evidence that the activity is benign.
2. If the fields the method reads are missing or not parsed, report a data gap for
   that source and period. Without them the case cannot be closed as benign.

## How it looks in the logs

- 4657 events under the run-key paths: the value name and the data are the
  whole story - what will run, from where, at every logon. The data's path
  decides: program directories fit software; user-writable paths fit implants.
- The same technique wearing startup folders: 4663 file writes into the user
  or common startup directories, with executable content.
- 4688 with a registry tool's argument shapes writing the same keys is the
  same change made by hand or by script.
- The subject being the user whose profile holds the key is ordinary; a
  subject writing into another user's or the machine's keys is the finding.

## Steps

1. Read the change event: key path, value name, data, subject and time. Say
   what will now run at every logon.
2. Assess the binary's path: system and program directories against user-
   writable paths (temp, profile, download).
3. Assess the subject: its role, its host, and whether the profile or machine
   area it wrote to is its own.
4. Wait for and read the first run: the entered binary's 4688 events, and what
   it did.
5. Check the fleet's shape: the same value on one host or on many; fleet-wide
   and identical is software, single-host and new is a finding.

## Attempt or impact

The autostart entry is persistence that exists from the moment it is written:
the impact is the foothold, whether or not the binary has run at a logon yet.

## Benign lookalikes

- Software and agents registering their autostart: program-directory paths,
  written by installers, fleet-wide and identical, matching the inventory.
- A user adding a tool to their own startup: their profile, their session, a
  path they use.

Authorization comes from the organization context together with the logs. A
value whose data lives in a user-writable path, written by another account, is
not explained by either.

## Verdict

- tp: an autostart entry written by an unexplained subject, or pointing into a
user-writable path
- fp: the documented software registration or the user's own entry, with the
inventory and history agreeing
- suspicious: the entry is new and thin context exists, and the binary has not
run

Cite the evidence_id of every event a claim rests on.

## Level

- medium: an unexplained entry on an ordinary host, not yet run.
- high: the entry's binary has run, or the subject is unexplained.
- critical: the binary reached other hosts, credentials or data, or the host
is a server.

## Urgent events

List first the registry or folder change, then the entered binary's first
execution, then the subject's logon that made the change.
