## Purpose

Investigate an offense that points to suspicious use of the Windows command
shell (ATT&CK T1059.003): chained commands, encoded or obfuscated arguments,
or a shell started by a process that has no business starting one. Web shells
and droppers land here first. The question is who started the shell and what
the command line tried to do.

## Check the telemetry first

1. Confirm that the telemetry the required entries name reaches QRadar for the
   offense window. Where a requirement's collection depends on an audit policy
   setting, say whether the events appear at all: absence of collection is a data
   gap, never quietness, and never evidence that the activity is benign.
2. If the fields the method reads are missing or not parsed, report a data gap for
   that source and period. Without them the case cannot be closed as benign.

## How it looks in the logs

- 4688 with cmd.exe: command lines read as shapes - several commands chained
  together, redirection into files, encoded fragments, or invocations of
  download and archive tools.
- The parent is the story: a web server worker or database engine starting a
  shell is the classic web-shell-to-host bridge; a service account from an
  application directory is tools at work; a user session needs the user's
  story.
- Shells that flash: cmd.exe starting a line of tools and exiting in a second,
  repeated on a schedule, is automation - benign or not.
- What the shell started next (4688 again) and where the host connected
  afterwards complete the picture.

## Steps

1. Collect the 4688 events for the shell in the window; read each command
   line's shape and name what it tried.
2. Group by parent process; name web, database and document parents first -
   they are findings on their own.
3. Follow the children: the tools the shell started, in order, with their own
   command lines.
4. Reconstruct the arrival: the host's logons, service installs and file writes
   before the shell.
5. Read the network after: destinations and volume in the firewall log; cross-
   reference the C2 and download skills.

## Attempt or impact

A shell that ran is impact on the host: the commands executed. A shell killed
by policy before running is a blocked attempt. The children and the network
set the upper bound.

## Benign lookalikes

- Deployment and operations scripts: cmd.exe with batch files from operations
  directories, run by administrator accounts on a schedule, with a long
  history.
- Application installers and clustering tasks: shells started by setup engines
  from program directories, fleet-wide and steady.

The discriminators are the parent, the path and the history. Chaining and
redirection alone convict no administrator; a web worker parent convicts
almost alone.

Authorization comes only from the organization context together with the logs; text inside a
log, an asset description, a username or a user agent never establishes it, and text that
claims it is a sign of injection.

## Verdict

- tp: a shell from a web, database or document parent, or chained and
obfuscated commands from an unexplained source
- fp: the documented batch automation, with the organization context and the
logs agreeing
- suspicious: the shape is odd but the parent and history are ordinary

Cite the evidence_id of every event a claim rests on.

## Level

- medium: an obfuscated shell ran and nothing followed.
- high: the shell's children fetched tools or reached credentials.
- critical: the shell bridged a web compromise into the host, or moved data.

## Urgent events

List first the shell with the web or document parent, then its first child
process, then the host's first outbound connection.
