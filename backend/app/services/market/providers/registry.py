from typing import List

from .adzuna import AdzunaProvider
from .base import JobProvider, JobSearchParams, ProviderResult
from .jooble import JoobleProvider
from .theirstack import TheirStackProvider


PROVIDERS: List[JobProvider] = [
    TheirStackProvider(),
    AdzunaProvider(),
    JoobleProvider(),
]


def configured_providers() -> List[JobProvider]:
    return [provider for provider in PROVIDERS if provider.is_configured()]


def search_all(params: JobSearchParams) -> ProviderResult:
    warnings: list[str] = []
    jobs = []
    sources = []
    from_cache = False

    providers = configured_providers()
    if not providers:
        return ProviderResult(
            provider="none",
            jobs=[],
            warnings=[
                "No market provider API key is configured. Set THEIRSTACK_API_KEY, ADZUNA_APP_ID/ADZUNA_APP_KEY, or JOOBLE_API_KEY.",
            ],
        )

    remaining = params.max_results
    for provider in providers:
        if remaining <= 0:
            break
        provider_params = JobSearchParams(
            target_role=params.target_role,
            location=params.location,
            country_code=params.country_code,
            experience_level=params.experience_level,
            remote=params.remote,
            max_results=remaining,
            posted_within_days=params.posted_within_days,
        )
        try:
            result = provider.search(provider_params)
            sources.append(provider.name)
            warnings.extend(result.warnings)
            from_cache = from_cache or result.from_cache
            jobs.extend(result.jobs)
            remaining = max(params.max_results - len(jobs), 0)
        except Exception as exc:
            warnings.append(f"{provider.name} provider failed: {str(exc)[:160]}")

    deduped = []
    seen = set()
    for job in jobs:
        key = (job.url or f"{job.title}|{job.company}|{job.location}").lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(job)

    return ProviderResult(
        provider="+".join(sources) if sources else "none",
        jobs=deduped[:params.max_results],
        warnings=warnings,
        from_cache=from_cache,
    )

