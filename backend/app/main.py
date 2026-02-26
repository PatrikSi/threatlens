from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.api.routes import auth, feeds, health, items, tags, views

app = FastAPI(title="ThreatLens API", version="0.1.0")
settings = get_settings()

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(feeds.router)
app.include_router(items.router)
app.include_router(tags.router)
app.include_router(views.router)
app.include_router(health.router)
