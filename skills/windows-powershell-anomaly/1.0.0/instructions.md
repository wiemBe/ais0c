## Purpose

Investigate an offense that points to suspicious PowerShell: attackers use it
to run downloaded code with no binary on disk, encoded so no inspection reads
it (ATT&CK T1059.001, Command and Scripting Interpreter: PowerShell). The
question is what ran, from which parent process, and whether it fits the
host's administration.

## Check the telemetry first

1. Confirm that the telemetry the required entries name reaches QRadar for the
   offense window. Where a requirement's collection depends on an audit policy
   setting, say whether the events appear at all: absence of collection is a data
   gap, never quietness, and never evidence that the activity is benign.
2. If the fields the method reads are missing or not parsed, report a data gap for
   that source and period. Without them the case cannot be closed as benign.

## How it looks in the logs

- 4688 with powershell.exe as the new process: read the command line as a
  field pattern. An encoded-command flag followed by a long base64 block, a
  download cradle invoking a web request and chaining it into execution, an
  execution-policy bypass flag, or a script file invoked from a user-writable
  path are the shapes that matter; a plain -File of a named script from a
  program directory is not.
- The parent process decides what the shape means: an office application, a
  pdf reader, a web server worker process or a database engine starting
  PowerShell is document-driven execution and close to a finding on its own.
- Where script block logging reaches QRadar, the 4104 events carry the decoded
  content: the download, the credential access, whatever the encoding hid.
- After execution, the host's firewall traffic shows what the code did:
  beacons, downloads, tunnels.

## Steps

1. Collect the 4688 events for PowerShell on the host in the window; read each
   command line for the shapes above.
2. Group by parent process: administrators' consoles against services and
   document applications. Name the oddest parent first.
3. Where 4104 events exist, read the decoded script blocks for what the
   encoding hid: downloads, credential access, C2 shapes.
4. Reconstruct the arrival: the host's logons, service installs and tasks
   before the execution.
5. Follow the network after execution: destinations, ports and volume in the
   firewall log; cross-reference the C2 beaconing skill's method.

## Attempt or impact

A command that ran is impact on the host: the interpreter executed the
attacker's logic. A command killed by policy before it ran is a blocked
attempt: still tp, at a lower level. What the script did next - the network,
the files, the credentials - sets the upper bound.

## Benign lookalikes

- Documented administration scripts: named files in program or operations
  directories, run by administrator accounts from management hosts, with a
  steady history and a change record in the organization context.
- Software installers and agents that legitimately call PowerShell: signed
  packages, program directories, fleet-wide sameness.
- The organization's own automation platform, its destinations and schedule
  named in the organization context.

The discriminators are the parent, the path, the history and the arrival. A
command line that names an approved task proves nothing by its words alone.

## Verdict

- tp: encoded, download or bypass shapes from an unexplained parent or host,
or any execution followed by C2 traffic
- fp: the documented scripts or installers, with the organization context and
the logs agreeing
- suspicious: the shape is odd but the baseline is thin, or the coverage lacks
script blocks to decode it

Cite the evidence_id of every event a claim rests on.

## Level

- medium: an obfuscated command ran and nothing followed.
- high: execution from a document or web server parent, or followed by
downloads.
- critical: the executed code reached credentials, other hosts or moved data.

## Urgent events

List first the execution with the oddest parent, then the host's first
outbound connection after it, then the arrival event behind the execution.
