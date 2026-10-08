## Purpose

Investigate an offense that points to tunneling: an internal host wraps its
traffic in another protocol to slip past egress rules (ATT&CK T1572 Protocol
Tunneling, with T1071.004 DNS as the classic carrier). DNS tunnels answer to
no firewall rule, and layered tunnels answer to only their outer shell. The
question is which host, in which protocol, moving how much, and to whose
benefit.

## Check the telemetry first

1. Confirm that the telemetry the required entries name reaches QRadar for the
   offense window. Where a requirement's collection depends on an audit policy
   setting, say whether the events appear at all: absence of collection is a data
   gap, never quietness, and never evidence that the activity is benign.
2. If the fields the method reads are missing or not parsed, report a data gap for
   that source and period. Without them the case cannot be closed as benign.

## How it looks in the logs

- DNS tunnels: a host's query volume far above its baseline, query names long
  and blocky (data riding the labels),TXT-heavy patterns, and resolvers
  outside the sanctioned set.
- Layered tunnels: single long-lived sessions whose outer protocol is innocent
  and whose volume says a workload rides inside; keepalive-paced durations.
- The content's shape tells the purpose: steady small chatter fits control;
  bursts outbound fit data leaving.

## Steps

1. Compare the host's DNS volume and query shapes with its baseline and its
   peers.
2. Read the long-lived sessions: duration, keepalive pacing, volume in both
   directions.
3. Name the owner from the host's process events where they reach QRadar.
4. Check the destinations' identity and the sanctioned resolvers and services
   against the organization context.
5. Where the volume is outbound-heavy, apply the exfiltration skill's method to
   the same sessions.

## Attempt or impact

A tunnel that moved traffic is impact: data or control crossed the boundary
inside another protocol. The level follows the volume's direction and size.

## Benign lookalikes

- Sanctioned VPN clients and gateways: the organization context names the
  endpoints, and the pattern is user-driven, workday-shaped.
- Legitimate long sessions: backups, replication, streaming - named services,
  named destinations, ordinary directions.

The discriminators are the resolver set, the destination's identity and the
content's shape. A workstation's DNS suddenly blocky and long is not browsing.

## Verdict

- tp: tunnel shapes from an unexplained host, above all with outbound-heavy
volume
- fp: the named VPN or long-session services, with the organization context
and the logs agreeing
- suspicious: the anomaly is real but ownership and content are thin

Cite the evidence_id of every event a claim rests on.

## Level

- medium: control-chatter-sized tunneling, nothing else on the host.
- high: outbound-heavy tunneling, or tunneling from a server.
- critical: bulk data leaving through the tunnel, or the host shows broader
compromise.

## Urgent events

List first the tunnel's largest outbound window, then the anomalous session
itself, then the host's compromise indicators.
