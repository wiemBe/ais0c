## Purpose

Investigate an offense that points to credential stuffing: an attacker replays username
and password pairs stolen from another breach into the bank's login endpoints (ATT&CK
T1110.004, Credential Stuffing). Unlike guessing, every attempt uses a real password from
somewhere else, so the failure rate is lower, the account list is real, and one success in
a thousand is enough. For a bank this is the highest-volume external threat after
scanning. The question is how many accounts were tried, how many were let in, and what
those accounts did next.

The telemetry here is the WAF's request log in front of the login endpoints; field names
below are F5 ASM's, and any WAF carries the same facts under its own names.

## Check the telemetry first

1. Confirm that the WAF's request log reaches QRadar for the offense window and that it
   covers the login endpoints. Without the login endpoint's requests, neither the attempt
   pattern nor the outcomes can be read: report a data gap.
2. If the client address or the response codes are missing, report a data gap. Without
   them the case cannot be closed as benign.

## How it looks in the logs

- One ip_client, or a rotating set, sending many requests to the login endpoint, each
  with a different username, at machine speed. A few attempts per username: the pairs are
  believed, not guessed - stuffing does not retry hard.
- The attempts mostly do not trip attack signatures - they are well-formed logins - so
  request_status may be alerted throughout; the volume and the username spread are the
  signal, not the signatures.
- Response codes split into failures and accepted logins; session creation events, where
  the application or WAF logs them, name the split precisely.
- The user_agent is often one scripted client repeated identically, or a small set;
  sometimes it names a browser automation or attack framework.
- Usernames that do not fit the organization's account scheme at all (with a separator
  the bank never uses, or from another service's namespace) are the fingerprint of a
  purchased list.

## Steps

1. Quantify per source address: request count to login endpoints, distinct usernames,
   attempts per username, time span and requests per second.
2. Find the successes: accepted-login response codes and session events for the tried
   usernames. Even one success changes the case into account takeover.
3. Read the username list's shape against the organization's account scheme: whole list
   real, part real, or mostly foreign names. A foreign-name share says which breach the
   list came from, when it can be named.
4. Follow the successful accounts: what their sessions did - profile pages, password
   changes, payment changes, transfers - in the application logs the window allows, and
   any VPN logins of the same accounts.
5. Classify the sources: external, one address or a botnet-like spread; the firewall log
   shows whether the same credentials were retried from other addresses after a source
   was blocked.

## Attempt or impact

Attempts that all failed are a blocked attack: still tp, at a low or medium level - the
account list is now known to be in circulation, which is its own finding for the users.
Any accepted login is impact: an outsider is inside a customer's or employee's account,
and everything that session did is in scope. Blocking lowers the level; it never makes
the traffic benign.

## Benign lookalikes

- The bank's own monitoring that logs into test accounts: internal addresses, a handful
  of fixed usernames, a steady schedule, and the organization context naming the check.
- A partner integration whose credentials expired: one username retried from one address,
  then stopped after the fix.
- A genuine user crowd after a marketing event: many addresses, human speed, high success
  rates - the opposite of the stuffing signature.

Authorization comes from the organization context together with the logs. A username that
looks like a test account proves nothing by its shape alone.

## Verdict

- tp: many usernames, few attempts each, machine speed, from a source the organization
  context does not explain.
- fp: the named monitoring or the explained integration, with the logs agreeing.
- suspicious: the volume is abnormal but the pattern does not split cleanly into
  stuffing, or the source cannot be classified.

Cite the evidence_id of every event a claim rests on.

## Level

- low: a small, fully failed run against the login endpoint.
- medium: a large failed run; the list is in circulation.
- high: any accepted login of an ordinary account.
- critical: accepted logins of privileged accounts, or successful sessions that changed
  payment details, credentials or moved money.

## Urgent events

List first any accepted login of a privileged or payment-capable account, then the first
and last attempt of the largest source, then the source summary line.
