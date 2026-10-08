## Purpose

Investigate an offense that points to impossible travel in cloud identity: one
account signed in from two places too far apart for the time between them
(ATT&CK T1078.004, Cloud Accounts). Stolen credentials replayed from elsewhere
look like this - and so do mobile carriers and VPN egress points, which is why
the method reads the properties, not just the map. The question is which sign-
in pair, which properties, and what the distant session did.

## Check the telemetry first

1. Confirm that the telemetry the required entries name reaches QRadar for the
   offense window. Where a requirement's collection depends on an audit policy
   setting, say whether the events appear at all: absence of collection is a data
   gap, never quietness, and never evidence that the activity is benign.
2. If the fields the method reads are missing or not parsed, report a data gap for
   that source and period. Without them the case cannot be closed as benign.

## How it looks in the logs

- Two sign-ins for one account from locations further apart than the time
  between them allows - the raw shape; the properties make or break it.
- The honest lookalikes are infrastructure: mobile carrier egress points and
  consumer VPN exits that hand one address to many users - same place, many
  accounts, chronic in history; and cloud service sign-ins that carry the
  datacenter's location, not the user's.
- The dangerous shape is the fresh interactive sign-in from a new country on
  an unknown device, followed by session use.

## Steps

1. List the account's sign-ins around the offense; find the impossible pairs;
   state the times and places.
2. Read the properties: client application, device identifier, network - the
   account's usual hardware against the distant one.
3. Separate interactive sign-ins from token replays and service-principal
   noise; say which the pair is.
4. Read the distant session's use: mail rules, consent grants (the consent
   skill), data access.
5. Check the account's recent credential exposure: dumps, phishing follow-ups,
   the stuffing skill's shapes.

## Attempt or impact

A distant fresh sign-in is impact: the account authenticated from the
attacker's position. Token replays are impact too, but point at an earlier
theft; say which story the evidence tells.

## Benign lookalikes

- Carrier and VPN egress geography: many accounts, same egress points, chronic
  in history, documented in the organization context.
- The account's own documented travel, named for the window, with properties
  that match the account's hardware.

The discriminators are the properties and the history, never the map alone. A
new device on a fresh interactive sign-in is not a carrier's egress.

## Verdict

- tp: a fresh distant sign-in on unknown properties with session use
- fp: the named egress geography or documented travel, with properties and
history agreeing
- suspicious: the pair is impossible but the properties and token ages are
inconclusive

Cite the evidence_id of every event a claim rests on.

## Level

- medium: a distant sign-in with no session use found.
- high: session use followed.
- critical: privileged accounts, mail rule or consent changes, or data access
from the distant session.

## Urgent events

List first the distant sign-in, then the first action of its session, then the
account's credential exposure trail.
