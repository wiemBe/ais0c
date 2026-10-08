## Purpose

Investigate an offense that points to Group Policy abuse: a domain policy
object was changed so that every computer in its scope executes or permits
something new (ATT&CK T1484.001, Domain Policy Modification: Group Policy).
One edit, every host: that is why attackers love it and why the change must be
explained exactly. The question is which policy changed, who changed it, and
what the change makes hosts do.

## Check the telemetry first

1. Confirm that the telemetry the required entries name reaches QRadar for the
   offense window. Where a requirement's collection depends on an audit policy
   setting, say whether the events appear at all: absence of collection is a data
   gap, never quietness, and never evidence that the activity is benign.
2. If the fields the method reads are missing or not parsed, report a data gap for
   that source and period. Without them the case cannot be closed as benign.

## How it looks in the logs

- 5136 (object modified), 5137 (created) and 5141 (deleted) on the domain
  controllers where the object's path sits under the policies container. The
  changed attribute names the edit: a script path, a task definition, a
  security setting, a preference item.
- The subject: group policy administration belongs to a small named set of
  accounts; anything else is the finding.
- The follow-through is what turns an edit into an incident: fleet-wide 4698
  scheduled tasks or script executions whose content matches the changed
  policy, minutes after the change.
- A policy deleted wholesale is the quieter variant: defenses configured by
  policy vanishing scope by scope.

## Steps

1. Read the directory events: policy object, attribute, old and new values
   where the event shows them, subject and time.
2. Assess the subject against the organization context's group policy
   administrators and its management hosts.
3. Establish the scope: which hosts the policy reaches, and what the change
   makes them do or stop doing.
4. Watch the fleet: scheduled tasks, script executions and setting changes on
   the scope's hosts after the edit.
5. Check the surrounding window on the domain controllers: the change is rarely
   the intruder's only act (logons, replication, group changes).

## Attempt or impact

The policy change is impact the moment it is made: every host in scope now
follows it, or stopped following what it replaced. There is no failed variant;
a change reverted later was in force in between.

## Benign lookalikes

- Documented group policy administration: named policy administrator accounts,
  from management hosts, inside change windows the organization context
  records, with the change matching a ticket.
- Fleet software or baseline pushes: the same well-known policies edited by
  the administration system on a schedule.

The discriminator is the subject's place in the named administrator set and
the change record. A policy edit by any other account, at any other hour, is a
finding whatever the edit says.

## Verdict

- tp: a policy changed by a subject outside the named administrators, or whose
effect spread tasks, scripts or weakened settings
- fp: documented policy administration, with the organization context and the
logs agreeing
- suspicious: the change is explained thinly and its scope's hosts show
nothing yet

Cite the evidence_id of every event a claim rests on.

## Level

- high: any unexplained policy change.
- critical: the change pushed execution (tasks, scripts) or weakened defenses
across the fleet, or it happened during an active incident.

## Urgent events

List first the policy change, then the first fleet-wide execution it caused,
then the subject's logon that made it.
