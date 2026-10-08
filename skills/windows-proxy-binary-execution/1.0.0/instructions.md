## Purpose

Investigate an offense that points to proxy execution through signed Windows
binaries: the attacker runs code with tools the operating system ships and
signs, so allowlists and eyes pass over them (ATT&CK T1218, System Binary
Proxy Execution). The Security log's process events catch the argument shapes.
The question is which signed binary was made to run what, and whether the what
fits the host.

## Check the telemetry first

1. Confirm that the telemetry the required entries name reaches QRadar for the
   offense window. Where a requirement's collection depends on an audit policy
   setting, say whether the events appear at all: absence of collection is a data
   gap, never quietness, and never evidence that the activity is benign.
2. If the fields the method reads are missing or not parsed, report a data gap for
   that source and period. Without them the case cannot be closed as benign.

## How it looks in the logs

- 4688 of the classic proxies: the html-help utility, the shell utility hosts,
  the registry server, the data-access utilities - each with its off-purpose
  shapes: a helper invoked with a remote or short-format argument, a utility
  asked to load a library by name, an installer utility pointed at odd package
  types.
- The parent tells the story: software installers legitimately drive some of
  these binaries from program directories; user sessions and document
  applications driving them is the finding.
- The follow-through: fetched content landing in temp and executing; the
  host's traffic to the fetch's origin.

## Steps

1. Collect the proxy binaries' 4688 events; for each, read the argument shape
   and name what it was asked to run or fetch.
2. Group by parent: program installers against user sessions and document
   applications.
3. Follow the payload: fetched files landing and executing; the network the
   fetch came from.
4. Reconstruct the arrival: what wrote or dropped the invocation (litter,
   phishing, web shell).
5. Cross-reference: fetched content executing is often stage two of the
   download skill's shapes; the C2 skill's method applies to its network.

## Attempt or impact

The proxy execution ran the payload is impact on the host. An execution killed
by policy before running anything is a blocked attempt that still shows
tooling and intent.

## Benign lookalikes

- Software installers and licensing components legitimately driving the same
  binaries: program directories, vendor signatures, steady history, fleet
  shape.
- Administrator utilities that use these binaries by design, named in the
  organization context.

The discriminators are the argument's purpose, the parent and the follow-
through. A signed binary fetching remote content at a document application's
request is not administration.

## Verdict

- tp: a proxy binary run with off-purpose arguments from an unexplained
source, above all with fetched content
- fp: the documented software use, with the inventory and history agreeing
- suspicious: the invocation is odd but no payload or network followed

Cite the evidence_id of every event a claim rests on.

## Level

- medium: an off-purpose invocation killed or idle.
- high: content was fetched or executed through the proxy.
- critical: the executed content reached credentials, other hosts or data.

## Urgent events

List first the proxy execution that fetched content, then the fetched
content's first execution, then the host's network to the fetch's origin.
