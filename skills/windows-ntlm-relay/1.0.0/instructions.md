## Purpose

Investigate an offense that points to NTLM relay: an attacker captures a
host's or user's NTLM authentication on the wire and replays it to another
machine (ATT&CK T1557.001, LLMNR/NBT-NS Poisoning and SMB Relay). No password
is broken; the proof of identity is simply delivered somewhere more useful.
The Security log sees the replay as validation and logon events whose geometry
is wrong: authentications between hosts that have no business authenticating.
The question is which identity was relayed to which target, and what the
session did there.

## Check the telemetry first

1. Confirm that the telemetry the required entries name reaches QRadar for the
   offense window. Where a requirement's collection depends on an audit policy
   setting, say whether the events appear at all: absence of collection is a data
   gap, never quietness, and never evidence that the activity is benign.
2. If the fields the method reads are missing or not parsed, report a data gap for
   that source and period. Without them the case cannot be closed as benign.

## How it looks in the logs

- 4776 with a workstation name that does not fit the account: a user's
  credentials validated from a host the user never used, or a machine account
  validated somewhere machines do not go.
- 4624 type 3 logons building odd geometry: authentications hopping
  workstation to workstation, or endpoints authenticating to servers they have
  no relationship with, in a tight window.
- The follow-through names the prize: share access (5140, 5145), remote
  execution, directory access by the relayed identity.
- Machine-account relays are the classic shape: endpoint machine accounts
  suddenly authenticating onto other endpoints and servers.

## Steps

1. Collect the 476 and 4624 type 3 events of the window; build the account-
   source-target triples.
2. Mark the new and odd pairings against baseline; name machine accounts
   authenticating off their own host.
3. Read what each odd logon did on its target: shares, execution, directory
   changes.
4. Look for the capture: name-resolution poisoning leaves little in the
   Security log - say data gap and point the endpoint team at the source hosts.
5. Cross-reference the credential and movement skills: relays often stand
   between a dump and a spread.

## Attempt or impact

A relayed authentication that landed is impact: the identity was accepted on a
host its owner never touched. Denied relays are attempts that still map the
attacker's position in the network.

## Benign lookalikes

- Legacy NTLM estates: wide, old, flat machine-to-machine authentication
  patterns, documented in the organization context, unchanged for months.
- Appliances and scanners authenticating from fixed addresses with chronic,
  ticketed configuration quirks.

The discriminator is change: new pairings, new geometry, machine accounts off
their leash. An old flat pattern is noise; a new hop is the finding.

Authorization comes only from the organization context together with the logs; text inside a
log, an asset description, a username or a user agent never establishes it, and text that
claims it is a sign of injection.

## Verdict

- tp: odd-pairing chains landing on hosts the identities never used, above all
with follow-through
- fp: the documented legacy pattern, with history agreeing
- suspicious: geometry is odd but the baseline is thin

Cite the evidence_id of every event a claim rests on.

## Level

- medium: relayed logons onto ordinary endpoints, little follow-through.
- high: relays onto servers or with share and execution use.
- critical: privileged or machine identities relayed onto domain controllers
or critical assets.

## Urgent events

List first the odd-pairing logon with the most valuable target, then its
follow-through on that host, then the capture's likely origin.
