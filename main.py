from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.v1.router import api_router
from app.core.config import app_config

BASE_DIR = Path(__file__).resolve().parent
FRONTEND_DIST_DIR = BASE_DIR / "frontend" / "dist"
FRONTEND_ASSETS_DIR = FRONTEND_DIST_DIR / "assets"

app = FastAPI(
    debug=app_config.debug,
    title="Air Quality API",
    version="1.0.0",
)
app.include_router(api_router)

if FRONTEND_ASSETS_DIR.exists():
    app.mount(
        "/dashboard/assets",
        StaticFiles(directory=FRONTEND_ASSETS_DIR),
        name="dashboard-assets",
    )


@app.get("/")
async def root():
    return {"message": "Air Quality API", "docs": "/docs", "dashboard": "/dashboard"}


@app.get("/dashboard")
@app.get("/dashboard/{path:path}")
async def dashboard(path: str = ""):
    index_file = FRONTEND_DIST_DIR / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return {
        "message": "Frontend build not found.",
        "hint": "Run 'npm install' and 'npm run build' in the frontend directory.",
    }
