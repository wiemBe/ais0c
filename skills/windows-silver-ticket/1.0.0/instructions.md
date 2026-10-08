## Purpose

Investigate an offense that points to a silver ticket: a service ticket forged
with a service account's key (ATT&CK T1558.002, Forge Web Cookies' Kerberos
sibling under Steal or Forge Kerberos Tickets). Unlike a golden ticket it
reaches one service, not the domain - but it needs no domain controller to
agree, so the domain controller's logs are exactly where the gap shows. The
question is which service's tickets were used without ever being issued.

## Check the telemetry first

1. Confirm that the telemetry the required entries name reaches QRadar for the
   offense window. Where a requirement's collection depends on an audit policy
   setting, say whether the events appear at all: absence of collection is a data
   gap, never quietness, and never evidence that the activity is benign.
2. If the fields the method reads are missing or not parsed, report a data gap for
   that source and period. Without them the case cannot be closed as benign.

## How it looks in the logs

- The gap is the signal: logons and service use on a host (4624 type 3,
  application logs) by an account for services where the domain controllers
  show no issuing 4769 in the window.
- Encryption that does not fit: RC4-use where the service account's tickets
  are AES in history (the kerberoasting skill's mirror).
- One service family at a time: silver tickets are narrow - file shares, a
  database, a web app - and the narrowness itself is a clue.

## Steps

1. List the service authentications on the target host in the window: accounts,
   times, services.
2. Match each against 4769 issuance on the domain controllers; name every
   authentication with no issuer.
3. Check the encryption types of what was issued against what the service
   account's history shows.
4. Trace the key: the service account's kerberoasting exposure (4769 bursts
   past), dump indicators on hosts it ran on.
5. Read the forged use: what the account did on the service host, and whether
   it spread (the movement skills).

## Attempt or impact

A forged ticket in use is impact: the service accepted an identity the domain
never vouched for. There is no failed variant worth separating - the forgery
happened off-log; what is seen is its use.

## Benign lookalikes

- Telemetry gaps: a domain controller's log stream briefly missing makes
  issued tickets invisible and every use look unissued - check both
  controllers' coverage before concluding, and say the check's result.
- Third-party Kerberos realms the organization context documents, whose
  tickets pass without this domain's 4769.

The honest false positives are measurement. Both must be shown - coverage
health or the documented realm - before a benign verdict; otherwise the gap
stands.

## Verdict

- tp: service authentications with no issuing tickets behind them, above all
with encryption mismatches
- fp: a proven coverage gap or a documented alternative realm, with nothing
else standing
- suspicious: the gap cannot be confirmed or dismissed by the coverage at hand

Cite the evidence_id of every event a claim rests on.

## Level

- high: any confirmed unissued-ticket use.
- critical: privileged services, databases or remote management reached by
forged tickets.

## Urgent events

List first the unissued-ticket authentication on the most valuable service,
then the service account whose key was forged, then the key's likely origin.
