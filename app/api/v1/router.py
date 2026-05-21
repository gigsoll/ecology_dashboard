from fastapi import APIRouter

from app.api.v1.endpoints import dashboard, measurements, parameters, stations, units

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(units.router)
api_router.include_router(parameters.router)
api_router.include_router(stations.router)
api_router.include_router(measurements.router)
api_router.include_router(dashboard.router)
