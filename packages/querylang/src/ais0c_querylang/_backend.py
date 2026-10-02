"""The parts of IBM's pySigma-backend-QRadar-AQL that ais0c_querylang uses, imported in one place.

The backend's source contains an invalid escape sequence (`"^\\S+$"`), so the first import
compiles it with a SyntaxWarning. The test suite turns warnings into errors; only that
warning is silenced here.
"""

import warnings

with warnings.catch_warnings():
    warnings.filterwarnings("ignore", category=SyntaxWarning)
    from sigma.backends.QRadarAQL import QRadarAQLBackend
    from sigma.mapping.fields import aql_field_mapping
    from sigma.pipelines.QRadarAQL import QRadarAQL_fields_pipeline
    from sigma.pySigma_QRadar_base.QRadarPipeline import QRadarFieldMappingTransformation

__all__ = [
    "QRadarAQLBackend",
    "QRadarAQL_fields_pipeline",
    "QRadarFieldMappingTransformation",
    "aql_field_mapping",
]
