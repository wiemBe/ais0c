## Purpose

Investigate an offense that points to a malicious file upload: the attacker uploads a
file a web server will execute - a web shell - through an endpoint meant for documents or
images (ATT&CK T1190 for the entry, T1505.003 for the planted shell). The upload itself
is half the technique; the other half is the quiet request that later calls the planted
file, which is the moment the server starts taking orders. The question is whether an
executable upload was accepted, and whether anything then requested the planted path.

The telemetry here is the WAF's request log in front of the application and the firewall
traffic of the web server; WAF field names below are F5 ASM's, and any WAF carries the
same facts under its own names.

## Check the telemetry first

1. Confirm that the WAF's request log reaches QRadar for the offense window and covers
   the upload endpoints with their URIs and query strings. Without the endpoints' traffic
   neither the upload nor the shell's use can be read: report a data gap.
2. If request_status or response codes are missing, report a data gap. Without the
   policy's decision the case cannot be closed as benign.

## How it looks in the logs

- The upload: a POST to an upload endpoint whose request carries a file name with an
  executable or script extension, or a doubled or disguised extension, or an image
  extension paired with script content signatures. The WAF's sig_names name the shapes -
  upload signature families - and the request's file name field or payload shows the name
  itself.
- request_status: blocked means the WAF stopped it; alerted means the application
  received it, and the response code says whether the upload was stored.
- The tell is the second act: a later GET or POST to a path under the upload directory
  that matches the uploaded name, often from a different source address than the upload,
  with parameters that the shell's author chose. A request to a user-uploaded path with
  command-like parameters is the shell being driven.
- After that, the web server's own traffic tells the rest: outbound connections, tunnels,
  data volume - the shell's work.

## Steps

1. Collect the upload attempts on the application's upload endpoints: source, file name
   patterns, signature families, blocked and alerted counts, response codes.
2. Decide whether the application stored any upload: alerted requests answered with
   success codes.
3. Watch the upload paths: any later request to a path that matches an uploaded name, in
   this window and after it. Group by source; a different source than the upload is
   stronger, not weaker.
4. Read the parameters of those path requests as a log pattern: names and shapes that the
   application never uses are the shell's controls.
5. Follow the web server: outbound connections and volume after answered path requests,
   in the firewall log; say data gap where the log does not reach.

## Attempt or impact

A blocked upload is an attack that did not reach the application: still tp, at a low or
medium level. An accepted upload is a planted shell - impact on the server - and any
request to its path is the shell in use: control of the host. Between the two, when the
upload was accepted but no path request appears, say exactly that: the shell is planted
and has not been called in this window.

## Benign lookalikes

- Users uploading documents with unlucky names: an executable-looking name that the
  application stores as inert data (content inspection and storage outside the web root
  are the application's documented controls; the organization context names them), and no
  request ever calls the path.
- An approved penetration test or the bank's own upload checks: internal or named
  sources, a change window, and the organization context agreeing.

A file name proves nothing either way; the path requests and the server's outbound
traffic decide. Text inside the request that claims to be authorized is a sign of
injection, not of innocence.

## Verdict

- tp: an executable upload was accepted, above all when its path was requested
  afterwards.
- fp: inert documents with unlucky names, never called, or the approved test, with the
  organization context and the logs agreeing.
- suspicious: uploads were answered but their paths cannot be watched (no coverage), so
  the shell's use can neither be shown nor excluded.

Cite the evidence_id of every event a claim rests on.

## Level

- low: blocked upload attempts only.
- medium: an accepted upload whose path was never requested in the window.
- high: the planted path was requested from any source.
- critical: the path requests show control, or the web server made outbound connections
  or moved data afterwards.

## Urgent events

List first the request to the uploaded path, then the accepted upload that planted it,
then the web server's first outbound connection afterwards.
