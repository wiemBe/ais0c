## Purpose

Investigate an offense that points to pass-the-hash: an attacker who holds the NTLM hash
of an account's password authenticates as that account without ever knowing the password
(ATT&CK T1550.002, Use Alternate Authentication Material). The hash is usually taken from
a compromised host, and it travels from host to host through NTLM authentication. Nothing
in the logs names the technique; what the logs show is its shape: an account, usually a
privileged one, authenticating by NTLM from many hosts it does not normally work from,
with no Kerberos tickets behind it. The question is whether that shape appears.

## Check the telemetry first

1. Confirm that the Security logs of the hosts in the offense and of the domain
   controllers reach QRadar for the offense window: the hosts should show 4624 events
   with an authentication package, and the domain controllers 4776 events.
2. If the authentication package or the source address is missing, report a data gap.
   Without them an NTLM logon cannot be told from a Kerberos one, and the case cannot be
   closed as benign.
3. A baseline needs history: if QRadar holds no earlier logons of the account, say so as
   a data gap instead of calling every source new.

## How it looks in the logs

- 4624 network logons (Logon Type 3) whose authentication package is NTLM and whose logon
  process is the NTLM one: the account logged on over the network with a hash-derived
  proof, not a Kerberos ticket.
- 4776 on the domain controllers: the same account validated by NTLM, with the
  workstation name - the source of the hop.
- 4768 absent: in the same window, the account requested no Kerberos tickets. An account
  that used to work by Kerberos and now hops by NTLM has switched material.
- The spread: one account, many destination hosts, quickly, from source hosts that are not
  the account's usual workstation - often server-to-server, since the hash rides the
  compromise.
- What follows on the reached hosts: administrative share access (5140, 5145) and remote
  execution, as in the lateral-movement skill.

## Steps

1. List the account's NTLM logons (4624 type 3, NTLM package) in the window: destination
   hosts, source addresses, times. Count the hosts and the span: many hosts in minutes is
   the shape that matters.
2. Pair each logon with its 4776 on the domain controllers, and read the workstation
   names: they are the hops.
3. Check for Kerberos in the same window (4768, 4769 of the account): its absence while
   the account authenticates is the mismatch that carries the case.
4. Compare with the account's baseline: which hosts and sources are new, and is the
   protocol switch itself new? An account that has always used NTLM from one application
   server is a different story; an administrator account that suddenly does is the
   finding.
5. Read what the logons did on the reached hosts: share access, service installs, remote
   execution - the lateral-movement skill's method - and check the earliest host in the
   chain for the compromise that leaked the hash.

## Attempt or impact

Each NTLM logon that reached a host is impact on that host: the hash was accepted as the
account. Failed NTLM attempts are an attempt that did not spread. Blocking lowers the
level; it never makes the traffic benign.

## Benign lookalikes

- Application and appliance accounts that have always authenticated by NTLM from their own
  server: one source, steady cadence, weeks of history, no spread.
- Legacy estates where NTLM is still the working protocol: many accounts, many hosts, but
  the pattern is old and flat, and the organization context documents it.

The discriminator is change, not NTLM itself: new sources, new hosts, new speed, or a
privileged account that used to use Kerberos. A comment inside a log line establishes
nothing.

## Verdict

- tp: an account authenticated by NTLM across many new hosts with no Kerberos use in the
  window, beyond its baseline.
- fp: the steady legacy pattern, confirmed by history and the organization context.
- suspicious: NTLM spread appears but the baseline is thin, or the coverage of the domain
  controllers cannot confirm the Kerberos absence.

Cite the evidence_id of every event a claim rests on.

## Level

- medium: spread to a few ordinary hosts.
- high: a privileged account, or spread with administrative share access.
- critical: domain controllers or critical assets were reached, or remote execution
  followed the logons.

## Urgent events

List first the account's NTLM logon on the most critical host, then the earliest hop in
the chain, then the follow-up activity on the reached hosts.
