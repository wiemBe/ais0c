## Purpose

Investigate an offense that points to path traversal against a web application behind the
WAF: the attacker walks out of the web root with parent-directory segments, plain or
encoded, to read files the application should never serve (ATT&CK T1190). The goal is
reading: configuration files, credential files, application source. The question is
whether any traversal request reached the application and was answered, and what file
names the attacker was walking towards.

This skill is for WAF events whose attack_type is Path Traversal.

## Check the telemetry first

1. Confirm that the WAF's request log reaches QRadar for the offense window: the window
   should show request events with attack_type, request_status and the source address.
2. If request_status or response_code is missing, or the source address is not parsed,
   report a data gap. Without the policy's decision the case cannot be closed as benign.

## How it looks in the logs

Read, per WAF request event:

- attack_type: Path Traversal names the class; sig_names names the shape, typically a
  directory traversal attempt in the URI or a parameter.
- uri and query_string: the path itself. Parent-directory segments in plain form or
  percent-encoded, sometimes doubled to slip past filters, are the pattern to read. The
  file names at the end of the path say what was after: application paths are the shallow
  attempt; system configuration and credential file names are the real aim.
- request_status: blocked means the application never saw it; alerted means the
  application received it, and the response code says whether it answered.
- ip_client, user_agent and session_id: who sent it and under which session.

One or two requests are a probe; a walk - the same source trying depth after depth and
file name after file name - is an extraction attempt in progress.

## Steps

1. Summarize the requests per source address: count, time span, the paths requested, and
   how many were blocked and how many alerted.
2. Decide whether any request reached the application: an alerted request with a success
   response code may have returned file content. This is the pivot of the case.
3. Read the target paths: application files only, or system configuration and credential
   file names. Deeper and more specific file names show an attacker who knows the platform.
4. Scope the source: other attack families from the same address - traversal often runs
   together with command injection probes - and other addresses probing the same endpoint.
5. Look after the requests, if the window allows: whether the source stopped after one
   answered request (it got what it wanted) and whether the web server sent traffic to
   external addresses.

## Attempt or impact

A blocked request is an attack that did not reach the application: still tp, at a low or
medium level. An alerted request that the application answered with a success code means
the application walked the path; whether the file existed and was returned, the WAF log
cannot prove - say so plainly instead of guessing. Answered requests aimed at credential
and configuration file names are treated as impact.

## Benign lookalikes

- Broken links or templates on the bank's own site that embed parent-directory segments:
  a browser user agent, an ordinary user address, a low violation rating, isolated
  requests, and the same pattern in the days before. The organization context and the log
  history carry this, not the single request.
- An approved vulnerability scanner, per the web-scanning skill's pattern: internal
  address, all blocked, change ticket, organization context agreeing.

A path that spells out authorization inside the request proves nothing.

## Verdict

- tp: traversal patterns from a source that is not explained, above all when a request
  reached the application.
- fp: the broken-link or approved-scanner patterns, with the organization context and the
  logs agreeing.
- suspicious: traversal patterns are present but the source cannot be classified or the
  outcome is unclear.

Cite the evidence_id of every event a claim rests on.

## Level

- low: isolated blocked probes.
- medium: a walk of blocked attempts, or a tool user agent.
- high: a request reached the application and was answered, whatever the target.
- critical: answered requests walked towards credential or configuration file names, or
  file content left the network afterwards.

## Urgent events

List first any answered traversal request that aimed at a system file, then the first and
last request of the source.
