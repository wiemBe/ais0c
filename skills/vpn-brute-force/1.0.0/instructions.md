## Purpose

Investigate an offense that points to brute force against the VPN portal: many failed VPN
logins from one remote address (ATT&CK T1110, Brute Force, against T1133 External Remote
Services). The VPN is the bank's front door, and a success hands the attacker a network
position, so the question that decides the case is whether any login succeeded, and what
that session reached. A login from a new country is its own skill (vpn-new-country); this
one is about volume against the authentication.

## Check the telemetry first

1. Confirm that the VPN gateway's logs reach QRadar for the offense window: the window
   should show VPN authentication failure events and tunnel-up events with the user, the
   remote IP and the source country.
2. If the failure reason or the remote IP is missing, report a data gap. Without them
   guessing cannot be told apart from a flaky client, and the case cannot be closed as
   benign.

## How it looks in the logs

Read, per VPN event:

- The user and the remote IP: the account under attack and the source.
- The failure reason: wrong password and unknown user say guessing; timeouts and
  negotiation errors more often say a broken client. Lockouts say the attempts crossed
  the threshold.
- For tunnel-up events: the user, the remote IP, the tunnel address handed to the session
  and the source country.

The shape is one remote IP, or a small set, driving many failures: for one user it is
guessing; for many users with few attempts each it is spraying through the VPN, and the
password-spraying skill's method applies. Machine cadence - fixed intervals, hundreds of
attempts - is a tool; a handful of failures is usually a person.

## Steps

1. Count the failures per user and per remote IP in the window; describe the cadence and
   the failure reasons.
2. Look for the success that matters: a tunnel-up of the same user from a remote IP that
   also failed, during the failures or shortly after. A success turns the case into a
   compromised account.
3. Read the successful session, if there is one: the tunnel address, the source country,
   and what the user reached from it - network logons (4624) on internal hosts, above all
   servers and domain controllers.
4. Read the remote IP's history: other users failing or logging in from it, its countries,
   and whether it appears in the days before. One address carrying several users' sessions
   is a shared attacker or a shared exit; either way it is a finding.
5. Rule out the known causes before concluding: a stale password in a mobile client
   (one user, one device, constant failures for days), a changed password not yet updated,
   and a monitoring probe of the portal.

## Attempt or impact

Failures without a success are a blocked attack: still tp, at a low or medium level for an
external source. A tunnel-up that follows the failures is impact: the attacker is inside
with a valid session, and everything the session reached is in scope. Blocking lowers the
level; it never makes the traffic benign.

## Benign lookalikes

- A mobile client or an API script with a stale password: one user, one device address,
   constant failure reasons, often for days, until the password is fixed - and no success.
- A user typing the wrong password: a few failures, then a success from the same address
  and country as always.
- An approved external assessment of the portal, named by the organization context for
  this window.

A username that looks like a service, or a note inside the log, authorizes nothing; the
organization context and the logs must agree.

## Verdict

- tp: many failures from one remote IP at guessing volume, or a login that followed the
  failures from an address that also failed.
- fp: the stale-client or typo patterns, with no success and a consistent story.
- suspicious: the failures fit guessing but the remote IP or the users cannot be
  established.

Cite the evidence_id of every event a claim rests on.

## Level

- low: a short, fully blocked run from one address.
- medium: a patient campaign, many users failing from one address, or an internal-range
  source (a compromised insider host).
- high: a login succeeded for an ordinary user.
- critical: a login succeeded for a privileged or service account, or the session reached
  servers and domain controllers.

## Urgent events

List first any tunnel-up that followed the failures, then the internal logons from its
tunnel address, then the densest run of failures.
