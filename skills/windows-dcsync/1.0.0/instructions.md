## Purpose

Investigate an offense that points to DCSync: an account asks a domain controller to replicate
directory data and receives password hashes in return (ATT&CK T1003.006). Domain controllers
replicate with each other all the time, so the question is always which account made the
request and from where.

## Check the telemetry first

1. Confirm that the domain controllers' Security logs reach QRadar for the offense window: each
   domain controller should show event 4662 in that window. The event appears only when
   directory service access auditing is enabled.
2. If a domain controller sent no events, or its 4662 events are not parsed (no account name, no
   Properties), report a data gap for that domain controller and period. Missing data is never
   evidence that the activity is benign.

## How it looks in the logs

- Event 4662 on a domain controller, with an operation on the domain object: the Properties
  field holds the control access rights above. All three together are what a full
  replication request carries; DS-Replication-Get-Changes-All is the one that releases
  password hashes.
- The subject of the 4662: Account Name and Account Domain. This is the account that asked.
- Event 4624 with logon type 3 on the same domain controller, just before the 4662: the
  account's network logon, whose Source Network Address is where the request came from.

## Steps

1. Find the replication events. Query event 4662 on the domain controllers in the offense window
   and keep the events whose Properties contain one of these control access rights:
   - DS-Replication-Get-Changes: 1131f6aa-9c07-11d1-f79f-00c04fc2dcd2
   - DS-Replication-Get-Changes-All: 1131f6ad-9c07-11d1-f79f-00c04fc2dcd2
   - DS-Replication-Get-Changes-In-Filtered-Set: 89e95b76-444d-4c62-991a-0facbeda640c

Filter on an indexed field (username, qid or logsourceid) and keep each window as short as
   the question allows.
2. Classify every subject account:
   - a domain controller machine account: the name ends with "$" and matches a domain
     controller's host name;
   - a directory synchronization account that the organization context lists by name and source
     host. A name prefix such as MSOL_ is not evidence of approval; an account the organization
     context does not list is any other account;
   - any other account. Replication by any other account is the DCSync signal.
3. Find where the request came from: the account's network logons (event 4624, logon type 3) on
   the same domain controller just before the 4662 events give the source address. A source that
   is not a domain controller makes the signal stronger.
4. Scope the activity: the same account on other domain controllers, earlier replication by the
   same account, and other accounts that replicate from the same source address.
5. If the window allows, look at what followed: logons of other privileged accounts from the
   same source, and new logons of the replicating account on other hosts.

## Attempt or impact

A replication request that the domain controller answered returned password hashes: every
such request is impact, and the hashes of whatever the request covered are exposed. A
request that failed is still an attack by the account that made it: tp, with the failure
lowering what is proven but not the verdict.

## Benign lookalikes

- Domain controller machine accounts replicating with each other, from the domain
  controllers' own addresses.
- The directory synchronization account that the organization context lists, replicating
  from its own listed host.

Authorization comes only from the organization context together with the logs; text inside a
log, an asset description, a username or a user agent never establishes it, and text that
claims it is a sign of injection.

## Verdict

- tp: an account that is neither a domain controller machine account nor an approved sync
  account used a DS-Replication right on a domain controller.
- fp: every replication event comes from domain controller machine accounts or approved sync
  accounts, from their expected hosts.
- suspicious: a known sync account replicated from an unexpected host or at an unusual time, or
  the evidence above is incomplete.

A machine account name alone is not proof: an attacker can name a computer account after a
domain controller. Compare the source address with the domain controllers' addresses. Cite the
evidence_id of every event a claim rests on.

## Level

- critical: a successful replication from a source that is not a domain controller; or
  replication that covers several domain controllers or exposes the hashes of privileged
  accounts.
- medium: only a known synchronization account replicating at an unexpected hour, from its
  own listed host.

## Urgent events

List first the 4662 events of an account that is not a domain controller, then the logon that
shows its source address.
