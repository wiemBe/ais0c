## Purpose

Investigate an offense that points to the use of forged Kerberos ticket-granting tickets:
a golden ticket (ATT&CK T1558.001) is a ticket-granting ticket forged with the domain's
krbtgt key, after that key was stolen, and it grants its holder whatever privileges the
forger wrote into it, for as long as the forger chose. The domain controller cannot tell
it from a real ticket, so the defense does not see the forgery - it sees its shadows: a
session that uses service tickets without ever having requested a ticket-granting ticket,
service ticket requests no legitimate client makes, and encryption that does not fit the
account's history. The question is whether any of those shadows appears.

## Check the telemetry first

1. Confirm that the domain controllers' Security logs reach QRadar for the offense window
   and for a baseline period before it: both should show 4768 (TGT requests) and 4769
   (service ticket requests).
2. If the Service Name, Account Name or Client Address is missing or not parsed, report a
   data gap. Without them the correlations below cannot be built, and the case cannot be
   closed as benign.

## How it looks in the logs

Three shadows, each weak alone and strong together:

- A service ticket request for krbtgt (4769 with Service Name krbtgt). No legitimate
  client requests a service ticket for the ticket-granting service itself; any single
  occurrence deserves the whole method.
- A TGT gap: a client address requesting service tickets (4769) in a window where it never
  requested a ticket-granting ticket (4768) from any domain controller. Real sessions
  request a TGT first; forged ones skip that exchange.
- An encryption anomaly: service tickets for an account suddenly issued as RC4 (0x17)
  where the account's history shows AES256 (0x12), because forgers often work with
  whatever key material they hold.

Read each 4769 for Account Name, Service Name, Ticket Encryption Type and Client Address;
read each 4768 for Account Name and Client Address. Correlate by Client Address and time.

## Steps

1. Search the window for 4769 events whose Service Name is krbtgt. If any exist, the case
   is made; the rest of the method scopes it.
2. Build the TGT gap: group 4769 requests by client address, and check each client's 4768
   history in a window before its first 4769. A session with service tickets and no TGT is
   the anomaly; name the accounts it used.
3. Check the encryption history of privileged accounts that appear in the anomaly: RC4
   where AES256 is the recorded pattern.
4. Read what the anomalous sessions did: logons (4624) of the accounts involved, above all
   on domain controllers and critical assets, and the group changes (4728, 4732, 4756) of
   the same window.
5. Look for the companion theft: golden tickets follow a DCSync or krbtgt-hash compromise
   (the windows-dcsync skill's method), sometimes weeks earlier. Say so as context if the
   logs show it; a data gap if they do not reach back.

## Attempt or impact

A golden ticket in use is always impact: authentication as a forged identity, usually with
forged privileges. There is no blocked variant; the forgery itself happened outside these
logs, and what is seen is its use. The level follows what the sessions reached.

## Benign lookalikes

There is no benign lookalike of a service ticket request for krbtgt or of a session with
no TGT behind it. The honest false-positive sources are measurement: a broken log source
that swallowed the 4768 events (check the domain controllers' coverage before concluding),
or a smartcard or third-party Kerberos realm setup the organization context documents,
where the realm's tickets appear without this domain's 4768. Both must be shown - by the
log sources' health or by the organization context - before they carry a benign verdict.

## Verdict

- tp: a 4769 for krbtgt; or a TGT gap with service ticket use that the organization
  context does not explain.
- fp: a documented alternative realm or a telemetry gap proven by the log sources'
  coverage, with nothing else standing.
- suspicious: an encryption anomaly or an unresolved TGT gap that the coverage cannot
  confirm or dismiss.

Cite the evidence_id of every event a claim rests on.

## Level

- high: a confirmed TGT gap or encryption anomaly on ordinary accounts.
- critical: a 4769 for krbtgt, or forged-ticket sessions touching domain controllers,
  privileged accounts or critical assets.

## Urgent events

List first any 4769 for krbtgt, then the service ticket requests of the client address
with the TGT gap, then the first logon that session performed.
