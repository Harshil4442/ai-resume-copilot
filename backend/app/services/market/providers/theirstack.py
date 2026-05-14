import os
from typing import Any

import requests

from ..cache import cache_key, get_json, set_json
from .base import JobPosting, JobProvider, JobSearchParams, ProviderResult


THEIRSTACK_URL = os.getenv("THEIRSTACK_API_URL", "https://api.theirstack.com/v1/jobs/search")


SENIORITY_MAP = {
    "entry": "junior",
    "junior": "junior",
    "mid": "mid_level",
    "mid-level": "mid_level",
    "senior": "senior",
    "staff": "staff",
    "principal": "staff",
    "lead": "senior",
}


class TheirStackProvider(JobProvider):
    name = "theirstack"

    def is_configured(self) -> bool:
        return bool(os.getenv("THEIRSTACK_API_KEY", "").strip())

    def _payload(self, params: JobSearchParams) -> dict:
        payload: dict[str, Any] = {
            "job_title_or": [params.target_role],
            "posted_at_max_age_days": max(1, min(params.posted_within_days, 365)),
            "limit": max(1, min(params.max_results, 100)),
            "page": 0,
            "order_by": [
                {"field": "date_posted", "desc": True},
                {"field": "discovered_at", "desc": True},
            ],
        }
        if params.country_code:
            payload["job_country_code_or"] = [params.country_code.upper()]
        if params.location:
            payload["job_location_pattern_or"] = [params.location]
        if params.remote is not None:
            payload["remote"] = params.remote
        seniority = SENIORITY_MAP.get((params.experience_level or "").lower())
        if seniority:
            payload["job_seniority_or"] = [seniority]
        return payload

    def _normalize_job(self, row: dict) -> JobPosting:
        locations = row.get("locations") or []
        first_location = locations[0] if locations and isinstance(locations[0], dict) else {}
        company_obj = row.get("company_object") or {}
        return JobPosting(
            title=row.get("job_title") or row.get("normalized_title") or "",
            company=row.get("company") or company_obj.get("name") or "",
            location=row.get("location") or row.get("short_location") or row.get("long_location") or first_location.get("display_name", ""),
            country=first_location.get("country_code") or "",
            remote=row.get("remote"),
            description=row.get("description") or row.get("job_description") or "",
            posted_at=row.get("date_posted") or row.get("posted_at") or row.get("discovered_at") or "",
            url=row.get("final_url") or row.get("source_url") or row.get("url") or "",
            source=self.name,
        )

    def search(self, params: JobSearchParams) -> ProviderResult:
        payload = self._payload(params)
        key = cache_key("market:theirstack", payload)
        cached = get_json(key)
        if cached:
            return ProviderResult(
                provider=self.name,
                jobs=[JobPosting(**item) for item in cached.get("jobs", [])],
                warnings=cached.get("warnings", []),
                from_cache=True,
            )

        token = os.getenv("THEIRSTACK_API_KEY", "").strip()
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        warnings: list[str] = []
        resp = requests.post(THEIRSTACK_URL, json=payload, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        rows = data.get("data") or data.get("jobs") or data.get("results") or []
        jobs = [self._normalize_job(row) for row in rows if isinstance(row, dict)]
        jobs = [job for job in jobs if job.title and job.description]
        if not jobs:
            warnings.append("TheirStack returned no postings with usable descriptions for this query.")

        value = {"jobs": [job.to_dict() for job in jobs], "warnings": warnings}
        set_json(key, value)
        return ProviderResult(provider=self.name, jobs=jobs, warnings=warnings)

