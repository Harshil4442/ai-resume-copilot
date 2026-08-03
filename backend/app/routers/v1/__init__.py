from fastapi import APIRouter

from . import admin, analysis, career, features

router = APIRouter(prefix="/v1")
router.include_router(admin.router)
router.include_router(analysis.router)
router.include_router(career.router)
router.include_router(features.router)
