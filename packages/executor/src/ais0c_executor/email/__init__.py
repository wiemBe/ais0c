"""Alert e-mails to the SOC operators (architecture §9, "E-posta bildirimi"; D-22).

Cases whose notify level is critical or high, and groups that went into storm state at those
levels, are e-mailed to the operators. The executor builds and sends the e-mail from fixed
Turkish templates, never a model:

- `alert_message`, `render_body`: the subject, the template fields and the body of a
  `CaseAlert` or a `GroupAlert`.
- `alert_needed`: the level rule. After a re-evaluation, an e-mail goes out only if the level
  went up.
- `refused_recipients`: the allowed domain check. One address outside the allowed domains
  refuses the whole e-mail.
- `EmailSender`: sends an alert once. It takes the recipients from the `operators` list, checks
  the kill switch right before the e-mail leaves and records the outcome in `notifications` and
  `audit_log`.
- `SmtpTransport`: the company's SMTP relay, set up by `SmtpSettings`.

The activity `send_email` (`ais0c_activities.email`) runs the sender.
"""

from ais0c_executor.email.addresses import (
    RefusalReason,
    RefusedRecipient,
    address_domain,
    normalize_domain,
    refused_recipients,
)
from ais0c_executor.email.errors import EmailTransportError, InvalidEmail
from ais0c_executor.email.levels import ALERT_LEVELS, alert_needed
from ais0c_executor.email.render import (
    EMAIL_TEMPLATES,
    EMAIL_TIME_ZONE,
    MAX_SUBJECT_LENGTH,
    TEMPLATE_IDS,
    alert_message,
    render_body,
)
from ais0c_executor.email.request import (
    CaseAlert,
    EmailOutcome,
    EmailRequest,
    EmailResult,
    GroupAlert,
)
from ais0c_executor.email.sender import (
    ALERT_RECIPIENTS,
    EMAIL_REJECT_ACTION,
    EMAIL_SEND_ACTION,
    EXECUTOR_ID,
    EmailConnection,
    EmailSender,
    EmailTransport,
)
from ais0c_executor.email.smtp import (
    DEFAULT_PORTS,
    SendReceipt,
    SmtpConnection,
    SmtpSettings,
    SmtpTransport,
    TlsMode,
    mime_message,
)

__all__ = [
    "ALERT_LEVELS",
    "ALERT_RECIPIENTS",
    "DEFAULT_PORTS",
    "EMAIL_REJECT_ACTION",
    "EMAIL_SEND_ACTION",
    "EMAIL_TEMPLATES",
    "EMAIL_TIME_ZONE",
    "EXECUTOR_ID",
    "MAX_SUBJECT_LENGTH",
    "TEMPLATE_IDS",
    "CaseAlert",
    "EmailConnection",
    "EmailOutcome",
    "EmailRequest",
    "EmailResult",
    "EmailSender",
    "EmailTransport",
    "EmailTransportError",
    "GroupAlert",
    "InvalidEmail",
    "RefusalReason",
    "RefusedRecipient",
    "SendReceipt",
    "SmtpConnection",
    "SmtpSettings",
    "SmtpTransport",
    "TlsMode",
    "address_domain",
    "alert_message",
    "alert_needed",
    "mime_message",
    "normalize_domain",
    "refused_recipients",
    "render_body",
]
