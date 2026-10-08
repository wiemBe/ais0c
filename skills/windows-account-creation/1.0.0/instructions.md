## Purpose

Investigate an offense that points to a new account created to keep access: after a
compromise, an intruder with administrative rights creates an account that looks like
routine infrastructure (ATT&CK T1136, Account Creation; T1136.001 local, T1136.002
domain). The account does no damage itself; it waits. The question is who created which
account where, and whether the creation and the first use fit the organization's account
process.

## Check the telemetry first

1. Confirm that the Security logs of the domain controllers (domain accounts) and of the
   member hosts (local accounts) reach QRadar for the offense window: the window should
   show account management events (4720).
2. If the subject account, the new account name or the host is missing or not parsed,
   report a data gap for that host and period. Without the three names the creation cannot
   be judged, and the case cannot be closed as benign.

## How it looks in the logs

The creation is one line: 4720, with the subject (who created it), the new account name
and the host. Everything around it turns the line into a story:

- 4722 the account was enabled, 4724 its password was reset - often right after creation.
- 4728, 4732 or 4756 the new account was added to a group; the privileged-group-change
  skill's method applies from there.
- 4624 the new account's first logons: where from, to which hosts, at which hours.
- The naming pattern: an account that fits the organization's scheme was probably made by
  its tools; an account that mimics an administrative or service naming pattern, with a
  slightly wrong spelling, was made to be overlooked.

Domain accounts are created on the domain controllers; local accounts on the member host
itself, where the host matters as much as the name.

## Steps

1. Read the 4720 event: subject, new account, host and time. State what the account is:
   domain or local, and on which host.
2. Assess the subject: its role in the organization context, the host it worked from, and
   whether that host is an approved management host. An account created by an ordinary
  user, or by an administrator from an unusual host, is a finding.
3. Follow the lifecycle: enable, password resets, group additions in the minutes after
   creation. A new account that receives rights at once is being prepared, not
   provisioned.
4. Read the first use: the new account's 4624 logons, the hosts it reached and the hours
   it chose. A quiet account that never logs on can still be a prepared foothold.
5. Check the surroundings: other account creations by the same subject in the window, and
   any intrusion indicators on the same host before the creation.

## Attempt or impact

The created account is the impact: from the moment it exists, the intruder holds a
credential that survives password resets of the compromised accounts it replaced. Whether
the account has been used yet changes the level, not the verdict.

## Benign lookalikes

- Onboarding and provisioning: an identity management or helpdesk account as subject,
  working hours, the account name fitting the organization's scheme, and a change record
  in the organization context that the logs agree with.
- A service or application account created during documented maintenance, from an approved
  management host, inside a change window the organization context names.

Authorization for any creation comes from the organization context together with the logs.
An account created by itself, at night, from a host that is not a management host, or with
a name that mimics an administrative pattern, is not explained by any of these.

## Verdict

- tp: an account created by an unexplained subject, or whose lifecycle (immediate rights,
  first use from unexpected places) does not fit any provisioning process.
- fp: a documented provisioning whose subject, name, host and timing the organization
  context and the logs both support.
- suspicious: the creation itself is explained thinly, and nothing yet shows use or
  misuse.

Cite the evidence_id of every event a claim rests on.

## Level

- medium: an unexplained local account on an ordinary host, unused so far.
- high: an unexplained domain account, or any new account that received privileges.
- critical: the new account was used, above all on domain controllers or critical assets,
  or it was created during an active incident.

## Urgent events

List first the 4720 creation, then any group addition of the new account, then its first
logon.
