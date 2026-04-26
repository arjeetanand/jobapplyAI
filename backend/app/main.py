from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.core.config import get_settings
from app.db.session import Base, engine
from app.models import entities  # noqa: F401


settings = get_settings()

app = FastAPI(title=settings.app_name, version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup() -> None:
    settings.resolved_storage_root.mkdir(parents=True, exist_ok=True)
    for child in ["base_resumes", "browser_sessions", "resume_versions", "metadata", "vector_index"]:
        (settings.resolved_storage_root / child).mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(bind=engine)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "review_first": True, "auto_apply": False, "auto_email": False}


app.include_router(router)
