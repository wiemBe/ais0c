"""The routers of the analyst API, under the `/api/v1` prefix (api.md).

Only the first scope of T-028 is here: cases, the QA queue, groups, the Analysis Catalog, the
critical assets and notification recipients, the change approvals of double control (T-033), the SLA metric and the platform flags, `/me` and
`/health`. Tuning, hunts, hunt packs, actors, `/metrics/agents` and `/admin/versions` come later,
and no endpoint closes an offense, changes a rule or runs an action (D-02, D-19).
"""

from fastapi import APIRouter

from ais0c_api.routers import administration, cases, catalog, changes, groups, monitoring, qa

API_PREFIX = "/api/v1"

api_router = APIRouter(prefix=API_PREFIX)
api_router.include_router(cases.router)
api_router.include_router(qa.router)
api_router.include_router(groups.router)
api_router.include_router(catalog.router)
api_router.include_router(changes.router)
api_router.include_router(administration.router)
api_router.include_router(monitoring.router)

__all__ = ["API_PREFIX", "api_router"]
