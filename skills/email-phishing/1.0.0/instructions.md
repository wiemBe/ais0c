## Purpose

Investigate an offense that points to phishing: mail designed to make the
recipient open, click or comply (ATT&CK T1566.002, Spearphishing Link, with
the attachment variant beside it). The mail gateway's telemetry carries most
of the case: what arrived, from whom, to whom, what the gateway did with it,
and who still clicked. The question is the campaign's shape, its targets and
its delivered share.

## Check the telemetry first

1. Confirm that the telemetry the required entries name reaches QRadar for the
   offense window. Where a requirement's collection depends on an audit policy
   setting, say whether the events appear at all: absence of collection is a data
   gap, never quietness, and never evidence that the activity is benign.
2. If the fields the method reads are missing or not parsed, report a data gap for
   that source and period. Without them the case cannot be closed as benign.

## How it looks in the logs

- Bursts of similar messages: one sender or a small set, near-identical
  subjects, many recipients inside the bank, minutes apart - the campaign's
  fingerprint.
- Sender domains a letter away from the bank's or its partners': read them
  letter by letter and name the difference.
- The gateway's verdicts split the campaign: blocked and quarantined messages
  are contained; delivered ones are live, and released quarantines are live by
  hand.
- The tail: internal hosts fetching the links' destinations (the proxy and
  firewall logs), and attachment names executing (4688) - the delivered
  share's consequences.

## Steps

1. Collect the campaign's messages; fix its shape: senders, subjects,
   recipients, times, verdicts.
2. Read the sender domains against the bank's and its partners'; name every
   lookalike.
3. List the delivered share: the mailboxes that hold the message, and any
   quarantine releases.
4. Follow the links: internal traffic to their destinations; follow the
   attachments: executions of their names.
5. Check the campaign's afterlife: credentials given (the stuffing skill's
   shapes), or the same themes returning from new senders.

## Attempt or impact

Blocked and quarantined messages are contained attempts: still tp at a low
level, because the campaign names the bank's people as targets. Delivered
messages are impact-in-waiting; a click or an execution is impact, and
everything that followed the click is in scope.

## Benign lookalikes

- The bank's own campaigns and newsletters: internal senders, the organization
  context's named marketing and communication services.
- Simulated phishing by the security team's vendor: the organization context
  names the service and its windows, and the links' destinations are the
  vendor's.

The discriminators are the sender's identity and the simulation record. A
domain one letter from the bank's is never benign whatever the subject says.

## Verdict

- tp: a lookalike or unknown-sender campaign reaching bank mailboxes, above
all with clicks or executions
- fp: the organization's own or its vendor's simulated campaign, with the
organization context and the logs agreeing
- suspicious: the campaign is real but delivered share and clicks cannot be
established

Cite the evidence_id of every event a claim rests on.

## Level

- low: fully contained attempts.
- medium: deliveries without clicks.
- high: clicks or credential submissions followed.
- critical: attachments executed, or credentials used (the stuffing and
account skills).

## Urgent events

List first any host that executed an attachment, then the first internal click
on a link destination, then the campaign's delivered mailbox list.
