from fastapi import APIRouter

from app.api import agent_routes as _agent_routes
from app.api import apply_routes as _apply_routes
from app.api import job_routes as _job_routes
from app.api import knowledge_routes as _knowledge_routes
from app.api import match_routes as _match_routes
from app.api import profile_routes as _profile_routes
from app.api import resume_version_routes as _resume_version_routes
from app.api import settings_routes as _settings_routes
from app.api import shared as _shared
from app.api import tracker_routes as _tracker_routes
from app.api.agent_routes import router as agent_router
from app.api.apply_routes import router as apply_router
from app.api.job_routes import router as job_router
from app.api.knowledge_routes import router as knowledge_router
from app.api.match_routes import router as match_router
from app.api.profile_routes import router as profile_router
from app.api.resume_version_routes import router as resume_version_router
from app.api.settings_routes import router as settings_router
from app.api.tracker_routes import router as tracker_router
from app.api.shared import get_settings
from app.services.supervised_apply import SupervisedLinkedInApplyAgent
from app.services.supervised_linkedin import SupervisedLinkedInImporter

router = APIRouter()

for child_router in (
    agent_router,
    profile_router,
    job_router,
    match_router,
    apply_router,
    knowledge_router,
    tracker_router,
    resume_version_router,
    settings_router,
):
    router.include_router(child_router)


_COMPAT_MODULES = (
    _agent_routes,
    _profile_routes,
    _job_routes,
    _match_routes,
    _apply_routes,
    _knowledge_routes,
    _tracker_routes,
    _resume_version_routes,
    _settings_routes,
    _shared,
)


def __getattr__(name: str):
    for module in _COMPAT_MODULES:
        if hasattr(module, name):
            return getattr(module, name)
    raise AttributeError(f"module 'app.api.routes' has no attribute {name!r}")
