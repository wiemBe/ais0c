# Recordings

Lab offenses recorded for the harness's `replay` mode (task T-052, decision T-70). Each closed
source offense is normalized to an open analysis view in the recording. Each directory is one
recording; its format, the `record` command and the anonymization are in
[harness/README.md](../README.md#replay-recorded-lab-offenses).

| Recording | Offense | What it holds |
|---|---|---|
| `lab-30-dcsync` | 30 (`svc_backup`, DCSync) | The offense, its rule and log source, 15,270 events of the two hours around it (all log source types but QRadar's `Health Metrics`) and the audit queries with the lab's answers |

Every file is reviewed before it is committed. A test (`harness/tests/test_replay_recording.py`)
fails when any file here holds an IP address outside RFC 5737 and `2001:db8::/32`.
