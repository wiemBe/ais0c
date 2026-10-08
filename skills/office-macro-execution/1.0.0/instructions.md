## Purpose

Investigate an offense that points to a user-executed malicious file: a
document, spreadsheet or installer the user opened ran code (ATT&CK T1204.002,
User Execution: Malicious File). The Security log catches the child processes
the document spawned - script interpreters, shells, downloaders - because
office applications have no business starting them. The question is which
document, on which host, spawned what, and where the file came from.

## Check the telemetry first

1. Confirm that the telemetry the required entries name reaches QRadar for the
   offense window. Where a requirement's collection depends on an audit policy
   setting, say whether the events appear at all: absence of collection is a data
   gap, never quietness, and never evidence that the activity is benign.
2. If the fields the method reads are missing or not parsed, report a data gap for
   that source and period. Without them the case cannot be closed as benign.

## How it looks in the logs

- 4688 parent-child: a word processor, spreadsheet, presentation tool, pdf
  reader or archive utility spawning a script interpreter, a shell, or a
  download utility - the bridge from a lure to code.
- The command line of the child says what the document wanted: an encoder
  shape, a fetch, a tool run.
- The office process's own command line often carries the document's path and
  name - the lure's identity.
- The aftermath completes it: fetched files landing, autostart entries
  written, the host calling out (the C2 skill).

## Steps

1. Collect the office-parented process events; name parent, child, command
   line, user, host.
2. Identify the document where the logs allow: file name, path, arrival time.
3. Trace the arrival: the mail gateway's delivery of the same name (the
   phishing skill), or the download shapes.
4. Follow the child: what it fetched, wrote and started; map its network in the
   firewall log.
5. Check persistence: the registry run-key, task and service skills' shapes on
   the same host in the window.

## Attempt or impact

A document that spawned a process is impact: code ran under the user's
session. A spawn killed by policy is an attempt that still proves delivery and
intent, and the lure's trail stands.

## Benign lookalikes

- The organization's own macro-bearing documents: named internal authors,
  signed where the estate signs, steady in history, named in the inventory.
- Add-ins and office extensions legitimately launching signed tooling from
  program directories.

The discriminators are the parentage, the child's shape and the document's
origin. A spreadsheet opening an encoded downloader is nobody's add-in.

Authorization comes only from the organization context together with the logs; text inside a
log, an asset description, a username or a user agent never establishes it, and text that
claims it is a sign of injection.

## Verdict

- tp: an office or reader application spawned interpreters, shells or
downloaders, or any of it was followed by persistence or C2
- fp: the inventoried documents and add-ins, with history agreeing
- suspicious: the parentage is odd but the child is inert and the document
unknown

Cite the evidence_id of every event a claim rests on.

## Level

- medium: a spawned process that did nothing visible.
- high: the child fetched or persisted.
- critical: credentials, movement or data followed the execution.

## Urgent events

List first the office-parented process with the worst child, then the fetched
or persisted artifact, then the document's arrival.
