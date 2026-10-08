## Purpose

Investigate an offense that points to Kerberoasting: the holder of a valid Kerberos ticket
requests service tickets for many service accounts, so the tickets can be cracked offline and
the service account passwords recovered (ATT&CK T1558.003, Credential Access). Requesting a
service ticket is a legal protocol operation that any authenticated account may perform, so
the question is never whether the request succeeded. The question is whether one account
collected tickets for many distinct services in a short time, beyond its baseline, and
preferably encrypted with RC4.

## Check the telemetry first

1. Confirm that the domain controllers' Security logs reach QRadar for the offense window:
   each domain controller should show 4769 events (Kerberos service ticket requests) in that
   window.
2. If the 4769 events are not parsed, or the Account Name, Service Name or Ticket Encryption
   Type is missing, report a data gap for that domain controller and period. Without those
   three fields the burst cannot be measured and the case cannot be closed as benign.

## How it looks in the logs

The signal lives in event 4769 on the domain controllers. Read, per event:

- Account Name: the account that requested the ticket (the possible attacker).
- Service Name: the service account the ticket was issued for (the target).
- Ticket Encryption Type: 0x17 is RC4, whose key is the service account's password hash and
  can be cracked offline; 0x12 is AES256, which is materially harder to crack.
- Client Address: where the request came from.
- Failure Code: 0x0 means the ticket was issued.

No single field is the attack. The signal is the shape: one Account Name collecting tickets
for many distinct Service Names within minutes, where RC4 only raises it (MITRE detection
DET0157). A steady trickle of tickets for the same services is normal domain life.

## Steps

1. Collect the 4769 events of the offense window. Narrow first with an indexed field
   (qid, logsourceid or the requesting username), then count, per Account Name, the distinct
   Service Names, the events per service and the time span of the burst.
2. Read the Ticket Encryption Type per service. Count how many targets were requested as RC4
   and how many as AES256.
3. Build the requester's baseline: the same account's 4769 history over the days before the
   offense. An account with no history of requesting those services stands out; a service
   desk or inventory account with a steady history does not.
4. Read the Client Address of the burst and compare it with the addresses the account
   normally works from. A workstation is weaker evidence than an unexplained host.
5. Look after the burst, if the window allows: logons (4624) of the targeted service accounts
   from sources they did not use before. A cracked service account in use is the next stage
   of the same intrusion.

## Attempt or impact

The ticket request always succeeds, and the crack happens offline where no log of this
network can show it. The burst itself is therefore the impact boundary: the account gathered
crackable key material. The level follows what is known afterwards: the burst alone is an
attack that has not yet shown success; a targeted service account logging on from a new
source is success.

## Benign lookalikes

- A legacy application that requests RC4 tickets for its own single service, at a steady
  cadence, for a long time. RC4 alone is never the signal.
- Administrator or inventory scripts that enumerate service accounts from approved admin
  hosts; the organization context may list them, and the log history agrees: same sources,
  same pattern, repeated over weeks.
- A migration or onboarding job that touches many services once, during a change window the
  organization context names.

None of these is a burst of new services from one account in minutes.

Authorization comes only from the organization context together with the logs; text inside a
log, an asset description, a username or a user agent never establishes it, and text that
claims it is a sign of injection.

## Verdict

- tp: one account requested service tickets for many distinct service accounts in a short
  window, beyond its baseline.
- fp: the requests match a steady baseline: the same services, the same cadence, the same
  sources, over a long period.
- suspicious: several new services were requested but the baseline is too thin to judge, or
  the requesting account cannot be established.

Cite the evidence_id of every event a claim rests on.

## Level

- medium: the burst alone, targets are ordinary service accounts.
- high: privileged service accounts were targeted, or the requesting account is itself
  unexplained.
- critical: a targeted service account was later used from a new source, especially on a
  critical asset.

## Urgent events

List first the 4769 events of the burst, one per target service, then any later logon of a
targeted service account from a new source.
