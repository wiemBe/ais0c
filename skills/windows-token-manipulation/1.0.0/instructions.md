## Purpose

Investigate an offense that points to access token manipulation: a process
carries or requests credentials of another account, impersonating it to act
with its rights (ATT&CK T1134.001, Token Impersonation and Delegation). The
Security log sees this as logons with new credentials out of place, and as
sensitive privilege use by accounts that should not hold it. The question is
which account acted under which identity, and whether the pattern fits the
host's software.

## Check the telemetry first

1. Confirm that the telemetry the required entries name reaches QRadar for the
   offense window. Where a requirement's collection depends on an audit policy
   setting, say whether the events appear at all: absence of collection is a data
   gap, never quietness, and never evidence that the activity is benign.
2. If the fields the method reads are missing or not parsed, report a data gap for
   that source and period. Without them the case cannot be closed as benign.

## How it looks in the logs

- 4624 with Logon Type 9: a process on the host asked to act as another
  account with fresh credentials. Read the caller and the target: an
  administration console raising itself to a service account is work; a
  workstation process raising itself to a domain administrator is the finding.
- 4673 and 4674: sensitive privilege use - backup, debug, impersonate, assign
  tokens - by accounts whose roles do not carry them. Debug privileges on user
  workstations belong to credential theft tooling more than to business
  software.
- 4688 with runas shapes: the manual form of the same act.
- What followed under the new identity: network logons, share access,
  directory changes - the point of the exercise.

## Steps

1. Collect the type 9 logons and the sensitive-privilege events in the window;
   name caller, target and privilege for each.
2. Check the host's software: application pools, middleware and services that
   legitimately impersonate are named in the organization context and steady in
   history.
3. Compare roles: which target accounts are being reached, and by callers that
   never reach them elsewhere.
4. Follow the acted-as identity: its logons, share access and directory changes
   afterwards.
5. Cross-reference the credential skills: impersonation often follows a dump
   (the credential dumping skill) and precedes lateral movement (the pass-the-
   hash skill).

## Attempt or impact

Each accepted impersonation or privilege use is impact: the process acted, or
could act, as the target account. Denied privilege requests are attempts that
still show intent and tooling on the host.

## Benign lookalikes

- Application infrastructure: middleware and web farms impersonating service
  accounts per their documented design, steady in history, fleet-shaped.
- Administrators using runas honestly: administrator accounts, management
  hosts, working hours, change records.

The discriminators are the caller's role, the target's reach and the history.
A user process holding debug or impersonation privileges is not
administration.

## Verdict

- tp: impersonation or token privileges used by accounts outside their roles,
or reaching accounts they never reach
- fp: the documented application impersonation or administrator runas, with
the organization context and the logs agreeing
- suspicious: privilege requests appear but nothing accepted followed, or the
baseline is thin

Cite the evidence_id of every event a claim rests on.

## Level

- medium: denied or unused token privilege attempts on a workstation.
- high: accepted impersonation reaching service or administrative accounts.
- critical: impersonation of domain administration accounts, or use on domain
controllers.

## Urgent events

List first the type 9 logon that reached the most privileged target, then the
sensitive-privilege events of the same process, then what was done under the
new identity.
