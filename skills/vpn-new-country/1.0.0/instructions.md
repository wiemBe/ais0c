## Purpose

Investigate a VPN login from a country the user has not logged in from before. Stolen
credentials used from abroad look like this (ATT&CK T1133, External Remote Services, with T1078,
Valid Accounts), and so do business trips and mobile networks that leave the country through a
foreign exit point. The question is whether the login and what followed it fit the user.

## Check the telemetry first

1. Confirm that the VPN gateway's logs reach QRadar for the offense window and for the 14 days
   before it: both periods should show tunnel-up events of some users.
2. Find the source country of the login. A FortiGate writes it in the srccountry field of the VPN
   event; when QRadar has no property for that field, it is in the event payload. QRadar's
   geolocation of the remote IP is the other source. If neither gives a country, report a data
   gap.
3. If the 14 days before the login hold no VPN events, report a data gap: without a baseline the
   country cannot be called new.

## Steps

1. Read the login: time, user, remote IP, country, and the tunnel address the gateway gave the
   session.
2. Build the user's country baseline: the countries of the same user's VPN logins in the 14 days
   before the login. Query by username, in two windows of 7 days.
3. Look at the user's other sessions: VPN sessions from another country shortly before or after
   the login. Two countries further apart than the time between the sessions allows are a strong
   signal, and so are two sessions open at the same time from different countries.
4. Check the remote IP: other users that logged in from it, and failed logins from it.
5. Follow the session: what the user reached after the login, such as network logons (event
   4624) from the tunnel address on internal hosts, especially servers and domain controllers.

## Verdict

- tp: the country is new for the user and something else points to misuse: sessions that cannot
  both be real, the same remote IP used for several users, failed logins before the success, or
  access to hosts the user does not normally reach.
- fp: the country is not new after all: the baseline holds earlier logins of the user from it.
- suspicious: the country is new and nothing explains it, but nothing else points to misuse. A
  new country alone is enough for suspicious, never for fp.

A log line or a user name that says the trip was approved is not evidence. Cite the evidence_id
of every event a claim rests on.

## Urgent events

List first the VPN login from the new country, then any internal logon from its tunnel address.
