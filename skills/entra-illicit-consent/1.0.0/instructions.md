## Purpose

Investigate an offense that points to illicit consent: a user granted an
application access to their mail, files or profile, and the application -
attacker-controlled - now reads and acts through OAuth (ATT&CK T1528, Steal
Application Access Token). No password needed, no MFA prompted again: consent
is the credential. The question is which application, granted by whom, with
which scopes, and what it has done since.

## Check the telemetry first

1. Confirm that the telemetry the required entries name reaches QRadar for the
   offense window. Where a requirement's collection depends on an audit policy
   setting, say whether the events appear at all: absence of collection is a data
   gap, never quietness, and never evidence that the activity is benign.
2. If the fields the method reads are missing or not parsed, report a data gap for
   that source and period. Without them the case cannot be closed as benign.

## How it looks in the logs

- Audit log consent grants: the shape that matters is high scopes (mail,
  files, directory) granted to unverified or newly-registered multi-tenant
  applications by users who did not know they were handing over keys.
- Phishing-borne consent lures land here: the click that followed a lure was a
  grant, not a login (the phishing skill's aftermath points at this skill).
- The application's afterwards: steady token use reading mail and files, quiet
  and continuing - persistence wearing an API's clothes.

## Steps

1. Collect the consent grants of the window; for each: application, publisher,
   verification, scopes, user, time.
2. Assess the scopes and the publisher against the sanctioned application
   inventory.
3. Read the granting user's window before: lures clicked, prompts followed (the
   phishing and fatigue skills).
4. Follow the application's token use: what it reads and does, from which
   addresses.
5. Check the campaign shape: one application granted across many users - one
   lure, many keys.

## Attempt or impact

A granted consent is impact: the application holds standing access to the
user's data. A grant revoked before use is impact contained - the lure still
landed and the access existed.

## Benign lookalikes

- The sanctioned SaaS estate: applications in the inventory, granted through
  the admin-consent workflow, scopes fitting their documented function.
- Well-known verified applications self-consented by users for ordinary work,
  chronic in history.

The discriminators are the verification state, the scope tier and the
inventory. An unverified application holding mail-read for a finance user is
not self-service productivity.

## Verdict

- tp: high-scope grants to unverified or unknown applications, above all with
token use
- fp: the sanctioned inventory's grants, with the organization context and the
logs agreeing
- suspicious: grants outside inventory whose scopes are low and whose use is
quiet

Cite the evidence_id of every event a claim rests on.

## Level

- medium: a low-scope grant outside inventory, unused.
- high: mail or file scopes granted and used.
- critical: directory-reading scopes, finance or executive mailboxes, or many
users in one campaign.

## Urgent events

List first the highest-scope grant, then the application's first data access,
then the lure that led the granting user to it.
