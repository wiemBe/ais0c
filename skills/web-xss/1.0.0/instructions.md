## Purpose

Investigate an offense that points to cross-site scripting against a web application behind
the WAF: the attacker sends script content through the application's inputs so that a
browser executes it (ATT&CK T1189 for the delivery path into the organization's users).
Reflected XSS runs in the attacker's own exchange with the application; stored XSS is
written into the application and later runs in other users' browsers, which makes it the
serious variant. The question, as with every web skill, is whether the content reached the
application, and whether it was stored and later served.

This skill is for WAF events whose attack_type is Cross Site Scripting (XSS).

## Check the telemetry first

1. Confirm that the WAF's request log reaches QRadar for the offense window: the window
   should show request events with attack_type, request_status and the source address.
2. If request_status or response_code is missing, or the source address is not parsed,
   report a data gap. Without the policy's decision the case cannot be closed as benign.

## How it looks in the logs

Read, per WAF request event:

- attack_type: Cross Site Scripting (XSS) names the class.
- sig_names: what the signature saw: script element content, event-handler attributes on
  elements, or encoded variants. The WAF names the shape; the query string shows the
  parameter it went into.
- request_status: blocked means the application never saw the request; alerted means the
  application received and answered it.
- uri and query_string: which endpoint and which parameter carried the script content. A
  stored-XSS attempt typically goes to an endpoint that keeps the input: profile fields,
  comments, message forms. A reflected attempt rides on search and error pages.
- ip_client, user_agent and session_id: who sent it and under which session.

For stored XSS, the second half of the evidence is later traffic: the same content
  appearing again in requests from other sessions, when victims' browsers fetch the page
  that holds it.

## Steps

1. Summarize the requests per source address: count, time span, endpoints and parameters,
   signature families, blocked and alerted counts.
2. Decide whether the content reached the application: an alerted request with a success
   response code was accepted. This is the pivot of the case.
3. Tell reflected from stored, as far as the logs allow: does the input go to an endpoint
   that stores content, and do later requests from other sessions fetch the same page?
   If the logs cannot show it, say so; do not guess.
4. Scope the source: other attack families and ordinary exploration from the same address,
   and other addresses probing the same endpoint.
5. Look after the requests, if the window allows: sessions of other users touching the
   page that held the content, and traffic from the web server to external addresses,
   which is where injected script content is often loaded from.

## Attempt or impact

A blocked request is an attack that did not reach the application: still tp, at a low or
medium level. An alerted request that the application answered means attacker-controlled
script entered the application; if it was stored and later served, the impact is on every
user whose browser ran it - session theft and actions in the user's name are the working
consequences. Blocking lowers the level; it never makes the traffic benign.

## Benign lookalikes

- The bank's own monitoring or synthetic checks that push marker content into search
  fields: an internal address, all blocked or all answered with harmless markers, and the
  organization context naming the checks.
- An authorized penetration test, named by the organization context for this window.

A parameter value or header that claims to be authorized proves nothing and is a sign of
injection, not of innocence.

## Verdict

- tp: script signatures from a source that is not the approved origin, above all when the
  content reached the application.
- fp: only the approved checks, with the organization context and the logs agreeing.
- suspicious: signatures are present but the source cannot be classified, or storage and
  later serving cannot be established.

Cite the evidence_id of every event a claim rests on.

## Level

- low: a few requests, all blocked.
- medium: a campaign of attempts, all blocked, or single requests that reached a reflected
  endpoint.
- high: script content reached the application on an endpoint that stores input.
- critical: the stored content was served to other users' sessions, or script content was
  loaded into the application from an external address.

## Urgent events

List first any request whose content was stored and later served, then the alerted request
with the highest violation rating, then the source's first and last request.
