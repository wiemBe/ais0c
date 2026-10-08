## Purpose

Investigate an offense that points to server-side request forgery: the attacker feeds the
application a URL of their choosing, and the application - which sits inside the network -
fetches it (ATT&CK T1190). The fetched target is the point: internal services the attacker
cannot reach, administrative interfaces, and the cloud metadata endpoints that hand out
credentials. SSRF is how an outside request becomes an inside reconnaissance trip. The
question is what the application was asked to fetch, whether it fetched it, and what the
internal targets said.

The telemetry here is the WAF's request log and the firewall's traffic log; WAF field
names below are F5 ASM's, and any WAF carries the same facts under its own names.

## Check the telemetry first

1. Confirm that the WAF's request log reaches QRadar for the offense window, with the
   query strings of the requests. Without the parameters, the forged targets cannot be
   read: report a data gap.
2. The strongest evidence is the firewall log of the web server's own outbound traffic in
   the same minutes. If the web server's traffic is not collected, say so as a data gap;
   the case then rests on the request pattern alone and stays open about execution.

## How it looks in the logs

- The request: a parameter that is meant to hold a URL, a host name or an address carries
  an internal one instead - an internal address range, an internal host name, a
  loopback or link-local address, or a cloud metadata host name. Plain and encoded forms
  both appear; the WAF's signatures catch some shapes and miss others, so read the
  parameters, not only the signatures.
- request_status: blocked means the WAF stopped the request; alerted means the
  application received it, and the response code says whether it answered.
- The proof, when it exists, is outbound: the web server opening connections to internal
  addresses or metadata endpoints in the same minutes, in the firewall traffic log. A web
  server that suddenly talks to internal administrative interfaces is the server running
  someone else's errands.
- The follow-on: the same source requesting the fetched content back, or credentials
  appearing in later requests - the exfiltration half of the technique.

## Steps

1. Collect the requests whose parameters carry internal addresses, internal host names,
   loopback or link-local addresses, or metadata host names. Group by source and endpoint;
   read each parameter as the target it names.
2. Decide what reached the application: alerted requests with answered response codes.
3. In the firewall log, read the web server's outbound connections in the same minutes:
   destinations, ports, timing. Match them to the named targets; each match is execution.
4. Assess the targets: internal services, administrative interfaces, metadata endpoints.
   Metadata endpoints and credential services raise the case on their own, even when
   blocked, because the intent is credential theft.
5. Scope the source: other attack families from the same address - SSRF often follows
   scanning and precedes data theft - and whether the source changed targets after a
   block, which is iteration, not accident.

## Attempt or impact

A blocked request is an attack that did not reach the application: still tp, at a low or
medium level. An answered request plus a matching outbound connection from the web server
is impact: the application fetched an internal target for the attacker. An answered
request without outbound proof is treated as suspected impact and said so plainly.

## Benign lookalikes

- The application's own fetch features - preview generators, importers, integrations -
  legitimately calling named external hosts: a fixed allowlist of destinations, steady
  history, ordinary users. The organization context and the application's documentation
  name them; the logs must agree.
- An approved penetration test, named by the organization context for this window.

A parameter that names an internal address is never benign by its shape; only the
destination's documented purpose can make it so. Text inside the request that claims
authorization proves nothing.

## Verdict

- tp: internal targets named in parameters from an unexplained source, above all with
  matching outbound connections from the web server.
- fp: the documented fetch feature calling its allowlisted destinations, or the approved
  test, with the organization context and the logs agreeing.
- suspicious: internal targets appear but execution can neither be shown nor excluded.

Cite the evidence_id of every event a claim rests on.

## Level

- low: blocked probes only.
- medium: a campaign of blocked probes, or answered requests against ordinary internal
  hosts.
- high: answered requests against administrative interfaces, with or without outbound
  proof.
- critical: connections from the web server to metadata or credential endpoints, or
  credentials leaving the network afterwards.

## Urgent events

List first any outbound connection from the web server to a metadata or credential
endpoint, then the request that named it, then the source's summary line.
