"""The field names of QRadar's `events` database (T-052, decision T-70).

Read from the lab on 2026-10-07 (QRadar 7.6.0 FP1, API 29.0): `GET /api/ariel/databases/events`.
The replay engine uses the lists to tell a query QRadar would refuse (a name in neither list: its
422 says the field does not exist) from one it cannot answer because the recording does not hold
the field (a name in a list). Names are lower case; a custom property is written in double quotes
when it has a space, and unquoted when it has none (`eventID` worked on the lab).

The custom properties are the lab deployment's own; a production console has others. They matter
only for the recordings of the lab.
"""

from typing import Final

# fmt: off
BUILTIN_FIELDS: Final = frozenset(
    {
        "adekey", "adevalue", "category", "collectorid", "credibility", "creeventlist",
        "destinationaddress", "destinationgeographiclocation", "destinationip", "destinationmac",
        "destinationport", "destinationv6", "devicegrouplist", "devicetime", "devicetype",
        "domainid", "duration", "endtime", "eventcount", "eventdirection", "geographiclocation",
        "hasidentity", "hasoffense", "hastlv", "highlevelcategory", "identityhostname",
        "identityip", "iscreevent", "isduplicate", "islogonly", "istruncated", "isunparsed",
        "logsourceid", "logsourceidentifier", "magnitude", "partialmatchlist",
        "partialormatchlist", "payload", "pcappacket", "postnatdestinationip",
        "postnatdestinationport", "postnatsourceip", "postnatsourceport", "prenatdestinationip",
        "prenatdestinationport", "prenatsourceip", "prenatsourceport", "processorid",
        "protocolconfigid", "protocolid", "qid", "qideventcategory", "qideventid", "relevance",
        "severity", "sourceaddress", "sourcegeographiclocation", "sourceip", "sourcemac",
        "sourceport", "sourcev6", "starttime", "storedforperformance", "tlvs", "username",
    }
)

CUSTOM_PROPERTIES: Final = frozenset(
    {
        "access allowed", "access intent", "access origin", "accesses", "account id",
        "account name", "accountid", "accountname", "acf2 rule key", "active offense count",
        "application", "application category", "application name", "avt-app-category",
        "avt-app-name", "avt-app-volumebytes", "avt-app-volumepackets", "bytes",
        "bytes from client", "bytes from server", "bytes received", "bytes sent", "bytesreceived",
        "bytessent", "cics terminal id", "client hostname", "command", "completion status",
        "component name", "component type", "computer name", "cre description", "cre name",
        "customer interface", "database name", "deployment id", "destination host name",
        "destination hostname", "destination zone", "dormant offense count", "element",
        "error code", "event id", "event summary", "eventid",
        "events per second coalesced - average 1 min", "events per second coalesced - peak 1 sec",
        "events per second raw - average 1 min", "events per second raw - peak 1 sec",
        "external id", "file directory", "file extension", "file hash", "file id", "file path",
        "filename", "flow source", "flows per second - average 15 min",
        "flows per second - peak 1 min", "function", "group id", "group name", "groupid",
        "host status", "hostname", "http_method", "identity context name",
        "identity context registry", "installer filename", "ips_action", "log string",
        "logon type", "machine id", "machine identifier", "malware", "md5 hash", "message",
        "metric id", "nested application", "nsm policy", "object name", "object type",
        "object type(s)", "objectname", "objecttype", "originating host", "originating_user",
        "os name", "packets", "packets from client", "packets from server", "packets received",
        "packets sent", "parent", "parent process id", "parent process name",
        "parent process path", "parity policy", "peak eps rate", "policy", "policy name",
        "process commandline", "process id", "process name", "process path",
        "racf authority used", "realm", "recipient host", "recipient_user", "recipients",
        "remote network", "resource", "role", "role name", "root hash", "rule id", "rule name",
        "scope", "sender", "sender host", "service", "service name", "sha1 hash", "sha256 hash",
        "sna global network name", "source host name", "source hostname", "source process",
        "source workstation", "source zone", "ssh login audit event", "subscriber",
        "target user name", "target username", "threat category", "threat family", "threat name",
        "threat severity", "total sessions", "transaction name", "unix access origin",
        "unix function", "url", "url host", "url path", "urlhost", "user domain", "user id",
        "value", "virusname", "vrf", "waf_action", "waf_responsecode", "waf_url", "waf_useragent",
    }
)
# fmt: on
