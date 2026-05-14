import os

import requests

from ..cache import cache_key, get_json, set_json
from .base import JobPosting, JobProvider, JobSearchParams, ProviderResult


ADZUNA_URL = os.getenv("ADZUNA_API_URL", "https://api.adzuna.com/v1/api")


class AdzunaProvider(JobProvider):
    name = "adzuna"

    def is_configured(self) -> bool:
        return bool(os.getenv("ADZUNA_APP_ID", "").strip() and os.getenv("ADZUNA_APP_KEY", "").strip())

    def search(self, params: JobSearchParams) -> ProviderResult:
        country = (params.country_code or "us").lower()
        query = {
            "app_id": os.getenv("ADZUNA_APP_ID", "").strip(),
            "app_key": os.getenv("ADZUNA_APP_KEY", "").strip(),
            "what": params.target_role,
            "where": params.location,
            "results_per_page": max(1, min(params.max_results, 50)),
            "content-type": "application/json",
        }
        key = cache_key("market:adzuna", {"country": country, **query})
        cached = get_json(key)
        if cached:
            return ProviderResult(
                provider=self.name,
                jobs=[JobPosting(**item) for item in cached.get("jobs", [])],
                warnings=cached.get("warnings", []),
                from_cache=True,
            )

        resp = requests.get(f"{ADZUNA_URL}/jobs/{country}/search/1", params=query, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        jobs = []
        for row in data.get("results", []):
            jobs.append(JobPosting(
                title=row.get("title") or "",
                company=(row.get("company") or {}).get("display_name", ""),
                location=(row.get("location") or {}).get("display_name", ""),
                country=country.upper(),
                remote=None,
                description=row.get("description") or "",
                posted_at=row.get("created") or "",
                url=row.get("redirect_url") or "",
                source=self.name,
            ))
        jobs = [job for job in jobs if job.title and job.description]
        warnings = [] if jobs else ["Adzuna returned no postings with usable descriptions for this query."]
        set_json(key, {"jobs": [job.to_dict() for job in jobs], "warnings": warnings})
        return ProviderResult(provider=self.name, jobs=jobs, warnings=warnings)

