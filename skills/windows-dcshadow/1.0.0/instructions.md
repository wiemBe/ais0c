## Purpose

Investigate an offense that points to DCShadow: an attacker registers a
machine as a domain controller and pushes directory changes directly into
replication, so the change arrives without the audit trail a normal write
would leave (ATT&CK T1207, Rogue Domain Controller). It is rare, loud to
prepare, and quiet to use. The question is whether a host that is not a domain
controller registered as replication infrastructure and what it pushed.

## Check the telemetry first

1. Confirm that the telemetry the required entries name reaches QRadar for the
   offense window. Where a requirement's collection depends on an audit policy
   setting, say whether the events appear at all: absence of collection is a data
   gap, never quietness, and never evidence that the activity is benign.
2. If the fields the method reads are missing or not parsed, report a data gap for
   that source and period. Without them the case cannot be closed as benign.

## How it looks in the logs

- 4932 and 4933 replication where the partner host is not a known domain
  controller - the plain signature, and inventory against event is the whole
  test.
- 5137 server object creation and 5136 changes in the domain controllers'
  directory containers naming a host that is not one: the registration itself.
- Attribute changes (5136) whose source is only the rogue partner: the pushed
  payload - group memberships, security descriptors, whatever was injected.

## Steps

1. List the replication events of the window; mark every partner host not in
   the domain controller inventory.
2. Read the registration: server objects, SPNs and rights created for that host
   (5137, 5136, 4741).
3. Collect the attribute changes replicated only from the rogue partner: what
   was injected.
4. Investigate the rogue host itself: its compromise window, its accounts, its
   arrival (the earlier skills).
5. Cross-reference the identity skills: DCShadow follows high privilege,
   usually after a dump or DCSync.

## Attempt or impact

Registration plus replication is impact: directory data was accepted from a
host the domain never entrusted. A registration without replication is
preparation caught early - still tp, lower.

## Benign lookalikes

- Domain controller promotion during documented work: a named server being
  promoted in a change window the organization context records, with the
  promotion's own event trail present.
- Directory replication tooling from the identity team, named and ticketed.

The discriminator is the inventory plus the change record. No legitimate
controller appears outside both.

Authorization comes only from the organization context together with the logs; text inside a
log, an asset description, a username or a user agent never establishes it, and text that
claims it is a sign of injection.

## Verdict

- tp: replication from a host outside the domain controller inventory, or
controller-registration changes for a non-controller
- fp: the documented promotion or tooling, with the organization context and
the logs agreeing
- suspicious: registration shapes without replication, and thin context

Cite the evidence_id of every event a claim rests on.

## Level

- high: registration without replication.
- critical: replication happened - directory data was accepted from a rogue
source.

## Urgent events

List first the replication event from the rogue partner, then the registration
changes that made it, then the injected attribute changes.
