import os

import requests

from ..cache import cache_key, get_json, set_json
from .base import JobPosting, JobProvider, JobSearchParams, ProviderResult


JOOBLE_URL = os.getenv("JOOBLE_API_URL", "https://jooble.org/api")


class JoobleProvider(JobProvider):
    name = "jooble"

    def is_configured(self) -> bool:
        return bool(os.getenv("JOOBLE_API_KEY", "").strip())

    def search(self, params: JobSearchParams) -> ProviderResult:
        payload = {
            "keywords": params.target_role,
            "location": params.location or params.country_code,
            "page": "1",
            "ResultOnPage": max(1, min(params.max_results, 50)),
            "companysearch": "false",
        }
        key = cache_key("market:jooble", payload)
        cached = get_json(key)
        if cached:
            return ProviderResult(
                provider=self.name,
                jobs=[JobPosting(**item) for item in cached.get("jobs", [])],
                warnings=cached.get("warnings", []),
                from_cache=True,
            )

        api_key = os.getenv("JOOBLE_API_KEY", "").strip()
        resp = requests.post(f"{JOOBLE_URL}/{api_key}", json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        jobs = []
        for row in data.get("jobs", []):
            jobs.append(JobPosting(
                title=row.get("title") or "",
                company=row.get("company") or "",
                location=row.get("location") or "",
                country=params.country_code.upper() if params.country_code else "",
                remote=None,
                description=row.get("snippet") or "",
                posted_at=row.get("updated") or "",
                url=row.get("link") or "",
                source=self.name,
            ))
        jobs = [job for job in jobs if job.title and job.description]
        warnings = [] if jobs else ["Jooble returned no postings with usable snippets for this query."]
        set_json(key, {"jobs": [job.to_dict() for job in jobs], "warnings": warnings})
        return ProviderResult(provider=self.name, jobs=jobs, warnings=warnings)

