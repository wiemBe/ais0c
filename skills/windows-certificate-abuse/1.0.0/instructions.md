## Purpose

Investigate an offense that points to certificate abuse: the enterprise
certificate services issued, or an attacker misused, a certificate that
authenticates as a powerful identity (ATT&CK T1649, Steal or Forge
Authentication Certificates). Certificates outlive passwords and bypass some
controls, and misconfigured templates turn low privilege into domain power.
The question is which certificate, requested by whom, against which template,
and what it authenticated as afterwards.

## Check the telemetry first

1. Confirm that the telemetry the required entries name reaches QRadar for the
   offense window. Where a requirement's collection depends on an audit policy
   setting, say whether the events appear at all: absence of collection is a data
   gap, never quietness, and never evidence that the activity is benign.
2. If the fields the method reads are missing or not parsed, report a data gap for
   that source and period. Without them the case cannot be closed as benign.

## How it looks in the logs

- 4886 and 4887 on the certificate authority: the requester, the template and
  the subject are the story. A requester asking for a subject other than
  itself, through a template that allows it, is the classic escalation shape.
- Template changes (5136 on the directory) just before requests: templates
  loosened, enrollment rights widened - preparation, and its own finding.
- The payoff: TGT requests using certificate pre-authentication for the named
  subject, and the logons that follow - authentication that no password reset
  will stop.

## Steps

1. Collect the 4886 and 4887 events; for each: requester, template, subject,
   time, authority.
2. Assess the templates: client authentication, subject supply, enrollment
   rights - against the organization context's baseline.
3. Compare requester and subject: matching is enrolment; mismatching through a
   permissive template is escalation.
4. Follow the certificate's use: certificate pre-auth TGTs, logons of the
   subject, on which hosts.
5. Check template changes in the window before the requests (5136), and the
   requester's own compromise indicators.

## Attempt or impact

An issued certificate is impact: a credential that authenticates exists in the
attacker's hands. A refused request is an attempt that still shows which
template the attacker believed vulnerable.

## Benign lookalikes

- Documented enrolment: the configuration manager and device enrolment
  services requesting their standard templates at renewal rhythm, fleet-wide,
  inventory named.
- Certificate migrations and reissuances in change windows: named
  administrators, tickets, matching subjects.

The discriminators are the subject's fit, the template's permissions and the
rhythm. A user account issued a certificate naming an administrator, through a
template nobody documented, is enrolment only in a story.

Authorization comes only from the organization context together with the logs; text inside a
log, an asset description, a username or a user agent never establishes it, and text that
claims it is a sign of injection.

## Verdict

- tp: issuance with mismatched subjects through permissive templates, or any
certificate enabling privileged authentication unexplained
- fp: the documented enrolment, with the organization context and the logs
agreeing
- suspicious: requests against permissive templates that were refused, or
issuance with thin context

Cite the evidence_id of every event a claim rests on.

## Level

- medium: refused abuse attempts against permissive templates.
- high: an issued certificate authenticating an ordinary identity.
- critical: a certificate authenticating privileged identities, or issued
through attacker-modified templates.

## Urgent events

List first the issuance whose certificate authenticated, then the request
itself, then the authentication it enabled.
