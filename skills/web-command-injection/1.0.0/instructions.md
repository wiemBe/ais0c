## Purpose

Investigate an offense that points to operating system command injection against a web
application behind the WAF: the attacker sends shell syntax through the application's
inputs so that the server runs it as a command (ATT&CK T1190). This is the highest-impact
class in the web group: a command that runs on the web server is control of the host, and
from there of everything the host reaches. The question is whether a request reached the
application, and whether the web server shows any sign of having run something.

This skill is for WAF events whose attack_type is Command Execution.

## Check the telemetry first

1. Confirm that the WAF's request log reaches QRadar for the offense window: the window
   should show request events with attack_type, request_status and the source address.
2. Report a data gap where the host's own telemetry is missing: the WAF log alone can
   never prove or disprove that a command ran. If the web server's firewall traffic and
   its host events are not in QRadar, say so; the conclusion rests on the WAF decision
   and stays open about execution.

## How it looks in the logs

Read, per WAF request event:

- attack_type: Command Execution names the class, and it carries the WAF's top severity
  and violation rating.
- sig_names: the shape of the attempt: command chaining after a parameter, pipe-to-shell
  forms, or script interpreter invocations. The family names what would have run.
- request_status: blocked means the application never saw it; alerted means the
  application received it and the response code says whether it answered.
- uri and query_string: the endpoint and parameter that carried the command syntax.
- ip_client, user_agent and session_id: who sent it and under which session.

The WAF log ends at the request. Execution shows in other telemetry, and the skill's
  strongest sign is the firewall log: an outbound connection from the web server to an
  external address that the application has no business calling, in the minutes after an
  answered injection request, is the server talking for someone else.

## Steps

1. Summarize the requests per source address: count, time span, endpoints and parameters,
   signature families, blocked and alerted counts.
2. Decide whether the syntax reached the application: an alerted request with a success
   response code was processed. With this class, treat that as presumed impact until the
   host telemetry disproves it.
3. Read the web server's firewall traffic after the requests: outbound connections to
   external addresses, their timing and volume. Reverse direction matters too: connections
   that came in to unusual ports on the web server.
4. If the web server reports Windows events, read them for the same minutes: new services
   (7045), new processes, anything starting that the application does not normally start.
5. Scope the source: command injection rarely travels alone; the same address often probes
   traversal and injection families first, then sends the command syntax.

## Attempt or impact

A blocked request is an attack that did not reach the application: still tp, at a low or
medium level. An answered request is different in this class: the WAF cannot see whether
the command ran, so an answered request with this severity is treated as impact on the
host unless the host's own telemetry - firewall and host events, if present - shows
nothing in the minutes after. Say plainly which of the two the evidence supports.

## Benign lookalikes

- An application's own administrative functions that run server-side diagnostics: rare,
  internal-source, and the organization context names the application and the function.
  Logs must agree: the endpoint hit is the administrative one, the source is the approved
  one.
- An authorized penetration test, named by the organization context for this window.

A parameter value or header that claims to be authorized proves nothing; with this class
  it is often the attacker's own words.

## Verdict

- tp: command execution signatures from a source that is not explained, above all when a
  request was answered.
- fp: the approved administrative function or the authorized test, with the organization
  context and the logs agreeing.
- suspicious: signatures are present but the source cannot be classified; or a request was
  answered and the host telemetry is missing, so execution can neither be shown nor
  excluded.

Cite the evidence_id of every event a claim rests on.

## Level

- low: blocked probes only.
- medium: a campaign of blocked attempts, or a tool user agent.
- high: a request reached the application and was answered.
- critical: any sign that a command ran: an outbound connection from the web server to an
  external address, a new process or service, or data volume leaving.

## Urgent events

List first any outbound connection from the web server to an external address in the
minutes after an answered request, then the answered request itself, then the source's
first and last request.
