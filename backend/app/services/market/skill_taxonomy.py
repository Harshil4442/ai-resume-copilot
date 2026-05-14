from typing import Dict, List


SKILL_TAXONOMY: Dict[str, List[str]] = {
    "Programming Languages": [
        "Python", "Java", "JavaScript", "TypeScript", "Go", "C++", "C", "C#",
        "Rust", "Kotlin", "Swift", "SQL", "Ruby", "PHP", "Scala", "R",
    ],
    "Backend": [
        "FastAPI", "Flask", "Django", "Spring Boot", "Node.js", "Express.js",
        "REST API", "GraphQL", "Microservices", "API Design", "gRPC",
    ],
    "Frontend": [
        "React", "Next.js", "Angular", "Vue", "HTML", "CSS", "Tailwind CSS",
        "Jest",
    ],
    "Databases": [
        "PostgreSQL", "MySQL", "MongoDB", "Redis", "Elasticsearch", "Oracle",
        "DynamoDB", "SQLite",
    ],
    "Cloud": [
        "AWS", "Azure", "GCP", "Lambda", "EC2", "S3", "Cloud Run",
        "Cloud Functions",
    ],
    "DevOps": [
        "Docker", "Kubernetes", "CI/CD", "GitHub Actions", "Jenkins",
        "Terraform", "Ansible", "Linux", "Nginx", "Helm",
    ],
    "AI/ML": [
        "LLM", "Generative AI", "RAG", "LangChain", "LangGraph", "OpenAI API",
        "Groq API", "Machine Learning", "Deep Learning", "PyTorch",
        "TensorFlow", "scikit-learn", "NLP", "Embeddings", "Vector Database",
        "FAISS", "Qdrant", "ChromaDB", "MLflow",
    ],
    "Data Engineering": [
        "Pandas", "NumPy", "Spark", "Kafka", "Airflow", "ETL",
        "Data Pipelines", "dbt", "Snowflake",
    ],
    "Testing": [
        "pytest", "Unit Testing", "Integration Testing", "Selenium",
        "Playwright", "DeepEval",
    ],
    "Security": [
        "JWT", "OAuth", "IAM", "Encryption", "OWASP", "Vulnerability Scanning",
        "Prompt Injection Defense",
    ],
    "Observability": [
        "Logging", "Monitoring", "Prometheus", "Grafana", "OpenTelemetry",
        "Sentry", "LangSmith", "Langfuse",
    ],
    "Architecture": [
        "System Design", "Distributed Systems", "Scalability",
        "Event-Driven Architecture", "Message Queues", "Background Jobs",
        "Celery",
    ],
    "Tools": [
        "Git", "GitHub", "GitLab", "Docker Compose", "Postman", "Vercel",
        "Google Cloud Run", "Render",
    ],
    "Soft Skills": [
        "Communication", "Collaboration", "Leadership", "Problem Solving",
        "Analytical Thinking", "Ownership", "Stakeholder Management",
    ],
}


ALIASES: Dict[str, str] = {
    "js": "JavaScript",
    "node": "Node.js",
    "nodejs": "Node.js",
    "node js": "Node.js",
    "ts": "TypeScript",
    "postgres": "PostgreSQL",
    "postgresql": "PostgreSQL",
    "k8s": "Kubernetes",
    "kubernates": "Kubernetes",
    "ci cd": "CI/CD",
    "cicd": "CI/CD",
    "github action": "GitHub Actions",
    "github actions": "GitHub Actions",
    "genai": "Generative AI",
    "generative ai": "Generative AI",
    "llms": "LLM",
    "restful api": "REST API",
    "restful apis": "REST API",
    "rest api": "REST API",
    "rest apis": "REST API",
    "api design": "API Design",
    "amazon web services": "AWS",
    "google cloud": "GCP",
    "google cloud platform": "GCP",
    "microsoft azure": "Azure",
    "fast api": "FastAPI",
    "express": "Express.js",
    "expressjs": "Express.js",
    "nextjs": "Next.js",
    "next js": "Next.js",
    "tailwind": "Tailwind CSS",
    "sklearn": "scikit-learn",
    "scikit learn": "scikit-learn",
    "vector db": "Vector Database",
    "vector databases": "Vector Database",
    "background job": "Background Jobs",
    "message queue": "Message Queues",
    "message queues": "Message Queues",
    "open telemetry": "OpenTelemetry",
    "opentelemetry": "OpenTelemetry",
}


CANONICAL_TO_CATEGORY = {
    skill.lower(): category
    for category, skills in SKILL_TAXONOMY.items()
    for skill in skills
}


def canonical_skill(value: str) -> str:
    raw = (value or "").strip()
    key = raw.lower().replace("-", " ").replace("_", " ").strip()
    key = " ".join(key.split())
    if key in ALIASES:
        return ALIASES[key]
    for category_skills in SKILL_TAXONOMY.values():
        for skill in category_skills:
            if skill.lower() == raw.lower():
                return skill
    return raw


def skill_category(skill: str) -> str:
    canonical = canonical_skill(skill)
    return CANONICAL_TO_CATEGORY.get(canonical.lower(), "Other")


def all_search_terms() -> Dict[str, List[str]]:
    terms: Dict[str, List[str]] = {}
    for category_skills in SKILL_TAXONOMY.values():
        for skill in category_skills:
            terms.setdefault(skill, []).append(skill)
    for alias, canonical in ALIASES.items():
        terms.setdefault(canonical, []).append(alias)
    return terms

