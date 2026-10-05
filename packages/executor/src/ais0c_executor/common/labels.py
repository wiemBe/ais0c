"""The Turkish contract-value labels used by both notes and e-mails (T-33)."""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final, Literal

from ais0c_executor.common.errors import TemplateError

type LabelCategory = Literal[
    "level", "level_tag", "verdict", "verdict_short", "confidence", "action", "gap_reason"
]

LABELS: Final[Mapping[LabelCategory, Mapping[str, str]]] = MappingProxyType(
    {
        "level": MappingProxyType(
            {"low": "düşük", "medium": "orta", "high": "yüksek", "critical": "kritik"}
        ),
        "level_tag": MappingProxyType(
            {"low": "DÜŞÜK", "medium": "ORTA", "high": "YÜKSEK", "critical": "KRİTİK"}
        ),
        "verdict": MappingProxyType(
            {"tp": "Gerçek pozitif (TP)", "fp": "Yanlış pozitif (FP)", "suspicious": "Şüpheli"}
        ),
        "verdict_short": MappingProxyType({"tp": "TP", "fp": "FP", "suspicious": "Şüpheli"}),
        "confidence": MappingProxyType({"low": "düşük", "medium": "orta", "high": "yüksek"}),
        "action": MappingProxyType(
            {
                "investigate_further": "Ayrıntılı inceleme",
                "contain_host_manual": "Host'u izole et (manuel)",
                "reset_credentials_manual": "Kimlik bilgilerini sıfırla (manuel)",
                "block_ioc_manual": "IOC'yi engelle (manuel)",
                "tune_rule": "Kuralı ayarla (tuning)",
                "close_as_fp": "FP olarak kapat",
                "notify_user": "Kullanıcıyı bilgilendir",
            }
        ),
        "gap_reason": MappingProxyType(
            {
                "no_data": "veri yok",
                "not_parsed": "ayrıştırılmamış",
                "not_visible": "görünmüyor",
                "query_failed": "sorgu başarısız",
                "budget_exhausted": "bütçe yetmedi",
            }
        ),
    }
)


def label(category: LabelCategory, value: str) -> str:
    """Return the Turkish label; missing categories and values are template errors."""
    try:
        return LABELS[category][value]
    except KeyError:
        raise TemplateError(f"no Turkish label for {category}:{value}") from None
