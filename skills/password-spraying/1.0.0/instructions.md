## Purpose

Investigate an offense that points to password spraying: one source tries a few common passwords
against many accounts and stays under each account's lockout threshold (ATT&CK T1110.003). The
question that matters most is whether any attempt succeeded.

## Check the telemetry first

1. Confirm that failed logons reach QRadar for the offense window: event 4625 from the hosts the
   offense names and, for domain accounts, Kerberos (4771) and NTLM (4776) failures from the
   domain controllers.
2. If the target account, the source address or the failure status is missing or not parsed,
   report a data gap: without them spraying cannot be told apart from a misconfigured service.

## Steps

1. Describe the failures. For each source address: the number of distinct target accounts, the
   attempts per account and the time span. Spraying shows many accounts with one or two attempts
   each, often spread out over hours; brute force shows one account with many attempts. If the
   offense is brute force against one account, say so and investigate it as such.
2. Read the failure statuses. Wrong passwords (Sub Status 0xC000006A) on existing accounts mixed
   with unknown account names (0xC0000064) point to a guessed or harvested account list. Locked
   accounts (0xC0000234, or event 4740) show that the attempts passed the lockout threshold.
3. Look for success: successful logons (event 4624) from the same source addresses, and logons of
   the targeted accounts from new sources, during and after the failures. A success turns the
   case into a compromised account; list those accounts first.
4. Classify the source: an internal host, a VPN tunnel address or an external address. An
   internal source may be a compromised host: check what else it did in the window.
5. Rule out known causes: a service or scheduled task with an old password retries one account
   from one host, and an approved vulnerability scanner may be listed in the organization
   context.

## Verdict

- tp: one source failed against many accounts with few attempts each, or a success followed the
  failures.
- fp: the failures come from one service account on one host, or from a scanner the organization
  context lists, and no attempt succeeded.
- suspicious: the pattern fits spraying but the source or the account list cannot be
  established.

Cite the evidence_id of every event a claim rests on.

## Urgent events

List first any successful logon that followed the failures, then the failures of the source with
the most target accounts.
