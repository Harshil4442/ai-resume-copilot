from typing import Dict, List

from .skill_taxonomy import skill_category


PROJECT_TEMPLATES = [
    {
        "title": "Production Backend Deployment Pipeline",
        "categories": {"Backend", "Databases", "DevOps", "Cloud", "Testing"},
        "skills": ["FastAPI", "PostgreSQL", "Docker", "GitHub Actions", "AWS", "pytest"],
        "difficulty": "intermediate",
        "description": "Build and deploy a production-style backend API with database persistence, containerization, automated tests, CI/CD, and cloud deployment.",
        "resume_bullets": [
            "Built and containerized a FastAPI + PostgreSQL backend with Docker and automated CI checks using GitHub Actions.",
            "Deployed a production-style REST API with environment-based configuration, health checks, and test coverage.",
        ],
    },
    {
        "title": "Evidence-Grounded AI Assistant",
        "categories": {"AI/ML", "Backend", "Security"},
        "skills": ["RAG", "LLM", "Embeddings", "Vector Database", "FastAPI", "Prompt Injection Defense"],
        "difficulty": "advanced",
        "description": "Create a document-grounded assistant that retrieves relevant context and answers questions with structured, prompt-injection-safe LLM outputs.",
        "resume_bullets": [
            "Implemented a retrieval-augmented generation workflow with grounded context selection and structured LLM responses.",
            "Added prompt-injection safeguards for untrusted document text before LLM summarization.",
        ],
    },
    {
        "title": "Observability-Ready Backend Service",
        "categories": {"Observability", "Backend", "DevOps"},
        "skills": ["Logging", "Monitoring", "OpenTelemetry", "Prometheus", "Grafana"],
        "difficulty": "intermediate",
        "description": "Add production observability to a backend service with structured logs, metrics, traces, dashboards, and latency/error tracking.",
        "resume_bullets": [
            "Added structured logging, metrics, and tracing to a backend API to improve debugging and production visibility.",
            "Created monitoring dashboards for request latency, error rates, and service health.",
        ],
    },
    {
        "title": "Event-Driven Skill Intelligence Pipeline",
        "categories": {"Data Engineering", "Architecture", "Backend"},
        "skills": ["Kafka", "Data Pipelines", "PostgreSQL", "Background Jobs", "System Design"],
        "difficulty": "advanced",
        "description": "Build an event-driven analytics pipeline that ingests records, processes them asynchronously, and stores aggregated insights.",
        "resume_bullets": [
            "Designed an event-driven data pipeline for asynchronous ingestion, transformation, and analytics aggregation.",
            "Implemented background processing with retry-safe workers and database-backed analytics outputs.",
        ],
    },
    {
        "title": "Modern Frontend Analytics Dashboard",
        "categories": {"Frontend", "Testing"},
        "skills": ["React", "Next.js", "TypeScript", "Tailwind CSS", "Playwright", "Jest"],
        "difficulty": "intermediate",
        "description": "Create a polished analytics dashboard with filters, charts, responsive layouts, and automated UI checks.",
        "resume_bullets": [
            "Built a responsive analytics dashboard using Next.js, TypeScript, Tailwind CSS, and reusable chart components.",
            "Added frontend validation and test coverage for core dashboard interactions.",
        ],
    },
]


def recommend_projects(gaps: List[Dict], max_projects: int = 5) -> List[Dict]:
    gap_skills = [g["skill"] for g in gaps if g.get("resume_status") != "proven"]
    gap_categories = {skill_category(skill) for skill in gap_skills}
    scored = []
    for template in PROJECT_TEMPLATES:
        skill_overlap = len(set(template["skills"]) & set(gap_skills))
        category_overlap = len(set(template["categories"]) & gap_categories)
        score = skill_overlap * 2 + category_overlap
        if score > 0:
            covered = [skill for skill in template["skills"] if skill in gap_skills]
            if len(covered) < 2:
                covered = list(dict.fromkeys([*covered, *gap_skills[:3]]))
            scored.append((score, {
                "title": template["title"],
                "skills_covered": covered[:6],
                "difficulty": template["difficulty"],
                "description": template["description"],
                "resume_bullets": template["resume_bullets"],
            }))

    scored.sort(key=lambda item: item[0], reverse=True)
    if scored:
        return [project for _, project in scored[:max_projects]]

    if not gap_skills:
        return []
    return [{
        "title": "Target Role Gap-Closing Project",
        "skills_covered": gap_skills[:5],
        "difficulty": "intermediate",
        "description": "Build a compact project that demonstrates the most repeated market gaps for your selected role.",
        "resume_bullets": [
            f"Built a role-aligned project demonstrating {', '.join(gap_skills[:3])}.",
            "Documented implementation decisions, tradeoffs, and testing evidence for interview discussions.",
        ],
    }]

