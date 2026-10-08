## Purpose

Investigate an offense that points to pass-the-ticket: a stolen Kerberos
ticket is replayed from a host the account never logged on from (ATT&CK
T1550.003, Pass the Ticket). Tickets prove identity to services, so a replayed
ticket is the account without the account's owner. The Security log cannot see
the theft, but it sees the reuse: one account's tickets presented from many
places at once. The question is which account's tickets traveled, and where
they were used.

## Check the telemetry first

1. Confirm that the telemetry the required entries name reaches QRadar for the
   offense window. Where a requirement's collection depends on an audit policy
   setting, say whether the events appear at all: absence of collection is a data
   gap, never quietness, and never evidence that the activity is benign.
2. If the fields the method reads are missing or not parsed, report a data gap for
   that source and period. Without them the case cannot be closed as benign.

## How it looks in the logs

- 4769 grouped by account: the client addresses of one account's service
  requests fan out across hosts it never used, in a tight window -
  simultaneous use is the signature, because a person is one place at a time.
- 4624 type 3 logons of the account from the same fan of addresses, onto
  servers, without matching interactive history anywhere.
- The absence that fits: no new TGT requests (4768) for some of the uses - the
  ticket predates the session, which is what replay means.
- Impossible pairs carry the case: uses further apart than the time between
  them allows.

## Steps

1. Group the account's 4769 and 4624 type 3 events by client address; count,
   map and time-line them.
2. Find the impossible pairs: simultaneous uses, or distances time forbids.
3. Check the ticket lifetimes' fit: uses whose sessions have no 4768 beginning.
4. Trace the theft: the account's earlier hosts, their compromise indicators
   (the credential skills), and where the ticket could have been lifted.
5. Read what the tickets reached: services, shares, remote management (the
   lateral-movement skill's method).

## Attempt or impact

Each replayed use is impact: the account authenticated where its owner was
not. There is no failed variant of the offense worth separating - denied
replays are the same attack caught at the door.

## Benign lookalikes

- Network address translation and portals: one address fronting many real
  hosts, so one account's uses seem to share an address - the reverse of the
  spread, and the infrastructure inventory names the portals.
- Farm and service accounts used from farm nodes by design: steady, scheduled,
  documented in the organization context.

The discriminator is simultaneity that infrastructure cannot explain. A named
portal's shared address is in the inventory; two countries at once is not.

## Verdict

- tp: one account's tickets used from addresses or places its owner cannot
have been at once
- fp: the named portal or farm pattern, with the organization context and the
logs agreeing
- suspicious: the spread is wide but simultaneity cannot be established

Cite the evidence_id of every event a claim rests on.

## Level

- high: any confirmed replay.
- critical: privileged or service accounts replayed, or replays reached domain
controllers or critical assets.

## Urgent events

List first the most impossible pair of uses, then the services the traveling
tickets reached, then the theft's likely origin.
