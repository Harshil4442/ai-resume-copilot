from app.services.market import analyzer
from app.services.market.providers.base import JobPosting, ProviderResult
from app.services.market.skill_extractor import extract_skills_from_text
from app.services.market.skill_taxonomy import canonical_skill


def test_alias_normalization():
    assert canonical_skill("JS") == "JavaScript"
    assert canonical_skill("Postgres") == "PostgreSQL"
    assert canonical_skill("K8s") == "Kubernetes"
    assert canonical_skill("RESTful APIs") == "REST API"
    assert canonical_skill("Amazon Web Services") == "AWS"


def test_skill_extraction_counts_each_skill_once_per_jd():
    text = "Python Python backend with FastAPI, Postgres, PostgreSQL, Docker, AWS and CI CD."
    skills, warnings = extract_skills_from_text(text)
    assert warnings == []
    assert {"Python", "FastAPI", "PostgreSQL", "Docker", "AWS", "CI/CD"} <= skills


def test_analyze_market_frequency(monkeypatch):
    jobs = [
        JobPosting(
            title="Backend Engineer",
            company="A",
            location="Remote",
            country="US",
            remote=True,
            description="Python FastAPI PostgreSQL Docker AWS REST APIs CI/CD",
            posted_at="2026-05-01",
            url="https://example.com/1",
            source="test",
        ),
        JobPosting(
            title="Software Engineer",
            company="B",
            location="Remote",
            country="US",
            remote=True,
            description="Python microservices PostgreSQL Redis Docker Kubernetes GitHub Actions system design",
            posted_at="2026-05-02",
            url="https://example.com/2",
            source="test",
        ),
        JobPosting(
            title="Backend Developer",
            company="C",
            location="Remote",
            country="US",
            remote=True,
            description="FastAPI Django SQL databases AWS API design pytest monitoring logging",
            posted_at="2026-05-03",
            url="https://example.com/3",
            source="test",
        ),
    ]

    def fake_search_all(params):
        return ProviderResult(provider="test", jobs=jobs, warnings=[])

    monkeypatch.setattr(analyzer, "search_all", fake_search_all)
    result = analyzer.analyze_market(
        target_role="Backend Engineer",
        location="Remote",
        country_code="US",
        experience_level="mid",
        remote=True,
        max_results=20,
        posted_within_days=30,
        resume=None,
    )

    top = {item["skill"]: item for item in result["top_skills"]}
    assert result["sample_size"] == 3
    assert top["Python"]["count"] == 2
    assert top["PostgreSQL"]["count"] == 2
    assert top["Docker"]["count"] == 2
    assert top["AWS"]["count"] == 2
    assert top["Python"]["percentage"] == 66.7
    assert result["confidence"] == "low"
    assert result["recommended_projects"]

