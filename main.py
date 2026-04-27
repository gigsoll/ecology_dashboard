from fastapi import FastAPI

from app.core.config import app_config


app = FastAPI(debug=app_config.debug)


@app.get("/")
async def root():
    return {"message": "Hello World"}
