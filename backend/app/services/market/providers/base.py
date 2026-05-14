from dataclasses import asdict, dataclass
from typing import List, Optional


@dataclass
class JobSearchParams:
    target_role: str
    location: str = ""
    country_code: str = ""
    experience_level: str = ""
    remote: Optional[bool] = None
    max_results: int = 50
    posted_within_days: int = 30


@dataclass
class JobPosting:
    title: str
    company: str
    location: str
    country: str
    remote: Optional[bool]
    description: str
    posted_at: str
    url: str
    source: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ProviderResult:
    provider: str
    jobs: List[JobPosting]
    warnings: List[str]
    from_cache: bool = False


class JobProvider:
    name = "base"

    def is_configured(self) -> bool:
        raise NotImplementedError

    def search(self, params: JobSearchParams) -> ProviderResult:
        raise NotImplementedError

