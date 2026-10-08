## Purpose

Investigate an offense that points to lateral tool transfer: executables
written to other hosts over administrative shares (ATT&CK T1570, Lateral Tool
Transfer). Movement needs tools on the far host, and the writes are logged.
The question is what was written where, by which account, and what ran
afterwards.

## Check the telemetry first

1. Confirm that the telemetry the required entries name reaches QRadar for the
   offense window. Where a requirement's collection depends on an audit policy
   setting, say whether the events appear at all: absence of collection is a data
   gap, never quietness, and never evidence that the activity is benign.
2. If the fields the method reads are missing or not parsed, report a data gap for
   that source and period. Without them the case cannot be closed as benign.

## How it looks in the logs

- 5145 share writes of executable and script file types into the
  administrative share roots and drive roots of other hosts - the
  infrastructure channels deployment and intrusion share.
- The writer account and source host say which: deployment service accounts
  from management hosts are software; user or compromised accounts from
  workstations are movement.
- The follow-through completes it: 4688 of the written names on the target
  hosts, minutes later, under the same or another account.

## Steps

1. Collect the share writes of executable file types; group by writer account
   and source host.
2. Read the targets: which hosts, which shares, and how the set compares with
   the account's baseline.
3. Follow execution on the targets: the written names' 4688 events and their
   children.
4. Reconstruct the writer: its logons (the lateral-movement skill), its
   compromise indicators.
5. Compare with deployment: fleet-wide identical writes from management hosts
   are software; verify in the inventory anyway.

## Attempt or impact

A write that landed put the tool on the far host: half the impact. The tool
running there is the whole of it. A write that failed still maps the
intruder's intended path.

## Benign lookalikes

- Software deployment and patching: deployment service accounts writing
  identical packages to fleet hosts from management hosts, on schedule,
  inventory-matched.
- Operations scripts distributed by the administration system, named in the
  organization context.

The discriminators are the writer, the breadth and the execution. A user
account writing an executable to servers' administrative shares is deployment
only in a story.

Authorization comes only from the organization context together with the logs; text inside a
log, an asset description, a username or a user agent never establishes it, and text that
claims it is a sign of injection.

## Verdict

- tp: executables written to other hosts' shares by unexplained accounts,
above all ones that then ran
- fp: the documented deployment, with the organization context and the logs
agreeing
- suspicious: the writes fit neither cleanly

Cite the evidence_id of every event a claim rests on.

## Level

- medium: writes that landed but never executed.
- high: the written tools executed on target hosts.
- critical: the tools were credential theft or movement tooling, or the
targets are domain controllers or critical assets.

## Urgent events

List first the write whose file executed, then that execution on the target
host, then the writer's logon onto that host.
