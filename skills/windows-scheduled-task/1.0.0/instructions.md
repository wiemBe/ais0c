## Purpose

Investigate an offense that points to a scheduled task created to keep access or to run on
a schedule the attacker chose (ATT&CK T1053.005, Scheduled Task/Job: Scheduled Task). A
task is a favorite persistence device: it survives reboots and logoffs, it looks like
system housekeeping, and it runs under whatever account it was given. The question is who
created which task on which host, what the task runs, and whether the creation fits the
host's administration.

## Check the telemetry first

1. Confirm that the host's Security log reaches QRadar for the offense window and that
   audit policy brings 4698 (a scheduled task was created) at all: if the host shows no
   task events over a long period, say so as a data gap rather than as quietness.
2. If the subject account or the task name is missing or not parsed, report a data gap for
   that host and period. Without them the creation cannot be judged, and the case cannot
   be closed as benign.

## How it looks in the logs

- 4698: a scheduled task was created. The event carries the subject account, the task
  name, and - in the task content the event includes when auditing is configured - the
  command the task runs and the account it runs as. Read the content as a log field: what
  program, from which path, run as which user, on which trigger.
- 4702 (task updated) and 4700 (task enabled) around the creation: changes to existing
  tasks are the same technique wearing a quieter coat - an attacker updating a real
  housekeeping task is harder to spot than a new one.
- 4688 when the task first runs: the process it started is the payload's name; the first
  run often follows the creation by less than an hour.
- 4699 (task deleted) shortly after creation: cleanup after use, itself a finding.

The names matter as patterns: a task named like system housekeeping but created by a user
account, or a task whose command lives in a user-writable path, does not fit the host's
administration.

## Steps

1. Read the 4698 event: host, subject, task name, run-as account, time, and the content
   the event shows of the action and trigger.
2. Assess the subject: its role in the organization context, its host, and whether that
   host is where it works. A user account creating a task on a server is a finding.
3. Read the task's shape: what it runs and from where, as the event shows it; when it
   triggers (at logon, on a schedule, once); and which account it runs as. A task that
   runs as a privileged account and was created by an ordinary one has already escalated.
4. Follow the first run: 4688 on the same host in the minutes or hours after, matching
   the task's command; then what that process did.
5. Check the surroundings: other task events on the host in the window (updates,
   deletions), and any intrusion indicators on the host before the creation - the
   lateral-movement skill's signs.

## Attempt or impact

A created task is persistence that exists: the impact is the foothold itself, whether or
not the task has run yet. A task that has run adds the payload's doings to the case; a
task that never ran is still a prepared foothold.

## Benign lookalikes

- System and application housekeeping: tasks created by software deployment or the
  operating system itself, with names and paths that match the host's software, often
  recreated after updates.
- Administrator-created maintenance tasks: an administrator account from an approved
  management host, inside a change window the organization context names, with a change
  record the logs agree with.

Authorization comes from the organization context together with the logs. A task whose
name imitates a system task, created from a user-writable path or by an account with no
admin role on that host, is not explained by either.

## Verdict

- tp: a task created by an unexplained subject, running from an unusual path or account,
  or standing next to intrusion indicators on the host.
- fp: documented administration or software installation whose subject, task and timing
  the organization context and the logs both support.
- suspicious: the creation is thinly explained and the task has not run.

Cite the evidence_id of every event a claim rests on.

## Level

- medium: an unexplained task on an ordinary host, not yet run.
- high: the task runs as a privileged account, or it has run.
- critical: the task ran and its process reached other hosts, or the host is a domain
  controller or critical asset.

## Urgent events

List first the 4698 creation, then the first process the task started, then the task's
update or deletion events.
