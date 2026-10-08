## Purpose

Investigate an offense that points to a new Windows service: a service installed on a host
is both persistence (it starts at boot, under a chosen account) and execution (installing
it runs its binary at once) (ATT&CK T1543.003, Create or Modify System Process: Windows
Service). Remote admin tools install their own services too, so the event alone says
little; the binary's path, the installer and the moment decide. The question is who
installed which service where, from which image, and whether that fits the host's
administration.

## Check the telemetry first

1. Confirm that the host's System log reaches QRadar for the offense window: services
   appear as 7045 in the System log, not the Security log. If the host's System log is not
   collected, say so as a data gap; the Security log alone cannot carry this case.
2. If the service name, the image path or the run-as account is missing or not parsed,
   report a data gap for that host and period. Without them the install cannot be judged,
   and the case cannot be closed as benign.

## How it looks in the logs

The 7045 event carries the whole story in one line:

- Service Name: what the service calls itself. Names of remote-admin support services are
  the classic marker of tool-based movement; a name imitating system services is a
  disguise.
- Image Path: where the binary lives. A system directory or an installed program's
  directory fits administration; a user-writable path - temp directories, profile
  directories, download folders - fits an implant.
- Start Type: automatic services start at every boot, which is persistence.
- Account Name: the account the service runs as. A service running as a privileged
  account, installed by an ordinary one, has already escalated.

Around the install: the logons (4624) and remote sessions that preceded it say how the
installer arrived (the lateral-movement skill's method); the process events (4688) of the
binary itself say what it did first.

## Steps

1. Read the 7045 event: host, service name, image path, start type, run-as account and
   time. Say plainly what the service is and what it can reach.
2. Assess the image path: system and program directories against user-writable paths.
   State which it is; the path is often the whole case.
3. Reconstruct the arrival: the logons onto the host before the install, from which
   sources, by which accounts; the remote-execution tools whose support services appear in
   the same minutes.
4. Follow the service's first run: the binary's processes (4688), what they touched, and
   any network they opened.
5. Check the surroundings: other installs on the host in the window, and the host's other
   intrusion indicators before the install.

## Attempt or impact

The install itself is impact: the binary ran once to register, and it will run again on
its trigger. A service caught before it ever started is persistence prepared, still tp at
a lower level.

## Benign lookalikes

- Software deployment: installation services created by deployment tools or installers,
  with image paths inside program directories, matching the host's software inventory, on
  many hosts in the same wave.
- Administrator tools: remote-admin support services installed by administrators from
  approved management hosts inside a change window, with the organization context naming
  the tool and the window.

A service whose
binary lives in a user-writable path, that runs as a privileged account, that was
installed from a host which is not a management host, or that appears next to intrusion
indicators, is not explained by any of these.

Authorization comes only from the organization context together with the logs; text inside a
log, an asset description, a username or a user agent never establishes it, and text that
claims it is a sign of injection.

## Verdict

- tp: a service installed from a user-writable path, by an unexplained subject, disguised
  under a system-like name, or next to intrusion indicators.
- fp: documented deployment or administration whose subject, image path and timing the
  organization context and the logs both support.
- suspicious: the install is thinly explained - an unusual but not impossible path or
  installer - and the service has not shown use.

Cite the evidence_id of every event a claim rests on.

## Level

- medium: an unexplained service on an ordinary host, not yet started.
- high: the service runs as a privileged account, or it has run, or its image lives in a
  user-writable path.
- critical: the host is a domain controller or critical asset, or the service's process
  reached other hosts.

## Urgent events

List first the 7045 install, then the binary's first process, then the logon that preceded
the install.
