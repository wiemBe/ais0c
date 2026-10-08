## Purpose

Investigate an offense that points to AS-REP roasting: an attacker who knows an account
name requests ticket-granting tickets for an account that is configured to skip Kerberos
pre-authentication, receives a ticket encrypted with a key derived from the account's
password, and cracks it offline (ATT&CK T1558.004). Unlike Kerberoasting this needs no
credentials at all - only account names - which makes it a favorite first move against a
domain. The question is which no-pre-authentication accounts had tickets requested for
them, from where, and beyond their normal pattern.

## Check the telemetry first

1. Confirm that the domain controllers' Security logs reach QRadar for the offense window:
   the window should show 4768 events (Kerberos TGT requests).
2. If the Pre-Authentication Type, the Account Name or the Client Address is missing or not
   parsed, report a data gap for that domain controller and period. Without those fields
   the pattern cannot be measured, and the case cannot be closed as benign.

## How it looks in the logs

The signal lives in event 4768 on the domain controllers. Read, per event:

- Account Name: the account the ticket was issued for (the target).
- Pre-Authentication Type: 0, or an absent pre-authentication field, means the ticket was
  issued without the exchange that proves the requester knows the password. Only accounts
  configured without pre-authentication can produce this.
- Ticket Encryption Type: 0x17 RC4 means the ticket's key is the account's password hash
  and is crackable offline; 0x12 AES256 is materially harder.
- Client Address: where the request came from.

The shape of the attack is volume: one client address requesting tickets for one or more
no-pre-authentication accounts, quickly and repeatedly, where the account does not normally
get its tickets from there. A single ticket request for a service account from its own
server is that account's normal morning.

## Steps

1. Collect the 4768 events of the window without pre-authentication. Narrow first with an
   indexed field (qid, logsourceid or username), then group per Account Name and Client
   Address: count and time span.
2. Read the encryption types: how many of the issued tickets were RC4.
3. Build the baseline: the same accounts' 4768 history over the days before. An account
   whose tickets always come from its own server and now come from a workstation, or many
   requests where there used to be one a day, stands out.
4. Read the Client Address across accounts: one client requesting tickets for several
   no-pre-authentication accounts is collection, not coincidence.
5. Look after the requests, if the window allows: logons (4624) of the roasted accounts
   from sources they did not use before. A cracked account in use is the next stage.

## Attempt or impact

The ticket request always succeeds, and the crack happens offline where no log of this
network can show it. The requests themselves are the impact boundary: key material that
can be cracked left the domain controller. The level follows what is known afterwards: the
requests alone are an attack that has not yet shown success; a roasted account logging on
from a new source is success.

## Benign lookalikes

- A service account without pre-authentication whose application requests its ticket on a
  schedule from its own server: one account, one client, an even cadence over weeks.
- A migration or legacy system documented in the organization context that uses
  pre-authentication-free accounts; the logs must agree: same accounts, same sources.

The account configuration itself is old: it was not changed by the attack.

Authorization comes only from the organization context together with the logs; text inside a
log, an asset description, a username or a user agent never establishes it, and text that
claims it is a sign of injection.

## Verdict

- tp: one client requested tickets for no-pre-authentication accounts beyond their
  baseline, above all for several accounts or at machine speed.
- fp: the requests match a steady baseline: the same accounts, the same clients, the same
  cadence, over a long period.
- suspicious: no-pre-authentication requests exist but the baseline is too thin to judge,
  or the client cannot be established.

Cite the evidence_id of every event a claim rests on.

## Level

- medium: requests for one account beyond its baseline.
- high: requests for several no-pre-authentication accounts from one client, or RC4
  tickets at machine speed.
- critical: a roasted account was later used from a new source, especially on a critical
  asset.

## Urgent events

List first the burst of 4768 events without pre-authentication, one per account, then any
later logon of a roasted account from a new source.
