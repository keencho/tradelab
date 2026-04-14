from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from config import get_logger
from db.database import init_db
from routes.views import router as views_router
from routes.api import router as api_router

logger = get_logger("main")

app = FastAPI(title="TradeLab")

app.mount("/static", StaticFiles(directory="static"), name="static")

app.include_router(views_router)
app.include_router(api_router, prefix="/api")


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.on_event("startup")
def startup():
    init_db()
    logger.info("TradeLab started")
