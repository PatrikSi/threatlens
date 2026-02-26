from fastapi import FastAPI

from app.api.routes import auth, feeds, health, items, tags

app = FastAPI(title="ThreatLens API", version="0.1.0")

app.include_router(auth.router)
app.include_router(feeds.router)
app.include_router(items.router)
app.include_router(tags.router)
app.include_router(health.router)
