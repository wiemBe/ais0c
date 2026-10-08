## Purpose

Investigate an offense that points to account and permission discovery:
someone is enumerating users, groups and privileges to pick the next target
(ATT&CK T1087 Account Discovery, T1069 Permission Groups Discovery). The
commands are ordinary administration tools; the case is in their density,
their source and what follows. The question is who enumerated what, and why
that map was wanted.

## Check the telemetry first

1. Confirm that the telemetry the required entries name reaches QRadar for the
   offense window. Where a requirement's collection depends on an audit policy
   setting, say whether the events appear at all: absence of collection is a data
   gap, never quietness, and never evidence that the activity is benign.
2. If the fields the method reads are missing or not parsed, report a data gap for
   that source and period. Without them the case cannot be closed as benign.

## How it looks in the logs

- 4688 enumeration shapes: the account listing commands, the group membership
  queries, the directory module's user and group commands, domain trust
  listings, privilege display - each naming its target in the arguments.
- Density is the tell: a burst of such commands in minutes, from one host, by
  one account, questions fanning from local to domain - administrators type
  few, scripts type many, tooling types them all.
- The subject's seat decides: an inventory agent on a schedule is work; a user
  account on a workstation asking for the domain's administrators is the
  finding.
- What follows closes it: the enumerated accounts or groups under attack
  (logons, sprays, changes) in the same or the next window.

## Steps

1. Collect the enumeration-shaped process events; group by subject and host.
2. Measure density and scope: command count, window, and how far the questions
   reached.
3. Assess the subject against the organization context's inventory and
   administration roles.
4. Follow the map's use: which named accounts or groups were attacked
   afterwards.
5. Check the source host's own story: compromise indicators before the
   enumeration (the earlier skills).

## Attempt or impact

Enumeration is reconnaissance: impact is measured in what it gave the attacker
- the account map - and in the attacks it aimed. It is tp at a low level when
nothing followed, higher when it aimed one.

## Benign lookalikes

- Inventory and CMDB agents: scheduled, fleet-wide, steady in history, named
  in the organization context.
- Administrators' onboarding and access-review work: few commands,
  administrator accounts, management hosts, tickets.

The discriminators are density, source and follow-through. A workstation user
mapping the domain's groups is inventory only in a story.

Authorization comes only from the organization context together with the logs; text inside a
log, an asset description, a username or a user agent never establishes it, and text that
claims it is a sign of injection.

## Verdict

- tp: dense or domain-wide enumeration from an unexplained source, or any
enumeration followed by attacks on what it found
- fp: the named inventory or the administrator's review, with the organization
context and the logs agreeing
- suspicious: the commands are there but the source and purpose are thin

Cite the evidence_id of every event a claim rests on.

## Level

- low: a few unexplained enumeration commands, nothing followed.
- medium: dense enumeration or domain-wide questions.
- high: enumeration followed by attacks on the accounts or groups it named.

## Urgent events

List first the enumeration burst's densest minute, then the first attack on an
account it named, then the source host's compromise indicator.
