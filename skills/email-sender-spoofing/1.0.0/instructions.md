## Purpose

Investigate an offense that points to sender spoofing: mail that claims to
come from the bank or its partners but does not (the T1566.002 delivery
pattern in its executive-impersonation shape). The gateway's authentication
verdicts - the sender-policy, signature and alignment checks - turn pretense
into evidence. The question is who was impersonated, to whom, and what the
mail asked for.

## Check the telemetry first

1. Confirm that the telemetry the required entries name reaches QRadar for the
   offense window. Where a requirement's collection depends on an audit policy
   setting, say whether the events appear at all: absence of collection is a data
   gap, never quietness, and never evidence that the activity is benign.
2. If the fields the method reads are missing or not parsed, report a data gap for
   that source and period. Without them the case cannot be closed as benign.

## How it looks in the logs

- Gateway verdicts: sender-policy and signature failures on mail claiming the
  bank's own domains or its partners' - alignment failures above all, where
  the visible domain is right and the signer is not.
- Display-name impersonation: the executive's name on an external address, and
  reply-to pointing elsewhere than the claimed sender.
- Recipient choice tells the intent: finance and payments staff, or one
  executive's direct reports - the business-email-compromise shape.

## Steps

1. Collect the failing-verdict messages; group by claimed sender and actual
   infrastructure.
2. Name the impersonation pairs and the recipient targeting; read the reply-to
   tricks.
3. Read the ask: payment changes, credential lures, thread hijacks (the
   phishing skill's shapes).
4. Follow the answers: replies leaving to the reply-to, submissions, and
   internal traffic to any links.
5. Check the chronicity: long-standing partner misconfiguration is ticket
   noise; a new campaign shape is the case.

## Attempt or impact

Blocked spoofing is a contained attempt; delivered spoofing that recipients
acted on - replies, payments, credentials - is impact. The ask's nature sets
the ceiling.

## Benign lookalikes

- Partners' misconfigured mail: chronic authentication failures with
  legitimate content and a standing ticket - the organization context names
  them.
- The bank's own marketing and notification services using third-party senders
  on its behalf, documented and aligned.

The discriminators are the alignment verdict, the reply tricks and the ask. A
finance-aimed thread hijack from a lookalike domain is nobody's
misconfiguration.

## Verdict

- tp: alignment-failing impersonation of the bank or partners with deceptive
reply paths, above all delivered and acted on
- fp: the named chronic partner misconfiguration or the documented sender,
with the organization context and the logs agreeing
- suspicious: authentication failures with ordinary content and no deception
markers

Cite the evidence_id of every event a claim rests on.

## Level

- low: blocked spoofed mail.
- medium: delivered deception without responses.
- high: recipients replied or clicked.
- critical: payments, credentials or data changed hands.

## Urgent events

List first the message whose recipient answered, then that recipient's
submissions and replies, then the campaign's impersonation summary.
