from fastapi import APIRouter

from . import admin, analysis, browser_pairing, career, employer, features

router = APIRouter(prefix="/v1")
router.include_router(admin.router)
router.include_router(analysis.router)
router.include_router(career.router)
router.include_router(features.router)
router.include_router(employer.router)
router.include_router(browser_pairing.router)
