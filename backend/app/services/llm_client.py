import os
import json
import requests
from typing import List, Dict

# Read lazily so tests / local dev without a key still import cleanly
def _api_key() -> str:
    return os.getenv("LLM_API_KEY", "")

LLM_API_BASE = os.getenv("LLM_API_BASE", "https://api.openai.com/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")

def _chat(messages: List[Dict]) -> str:
    key = _api_key()
    if not key:
        raise RuntimeError("LLM_API_KEY is not set. Put it in backend/.env (copy from .env.example).")

    url = f"{LLM_API_BASE}/chat/completions"
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": LLM_MODEL,
        "messages": messages,
        "temperature": 0.3,
    }
    resp = requests.post(url, json=payload, headers=headers, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"]

def rewrite_bullets(resume_text: str, jd_text: str, tone: str) -> Dict:
    system_prompt = (
        "You are an expert resume writer. Rewrite the candidate's bullet points using STAR format, "
        "quantifying impact and aligning with the job description. "
        f"Tone: {tone}. Output JSON with keys 'bullets' (list of strings) and 'summary' (string)."
    )
    user_content = f"RESUME:\n{resume_text}\n\nJOB DESCRIPTION:\n{jd_text}"
    content = _chat(
        [{"role": "system", "content": system_prompt},
         {"role": "user", "content": user_content}]
    )

    try:
        return json.loads(content)
    except Exception:
        return {"bullets": [content], "summary": "Model did not return JSON; raw response in bullets[0]."}

def generate_interview_questions(job_title: str, jd_text: str, num_questions: int = 8) -> List[Dict[str, str]]:
    system_prompt = (
        "You are an expert interviewer. Generate thoughtful interview questions and model answers "
        "based on the job description. Output a JSON list of objects with 'question' and 'answer'."
    )
    user_content = f"JOB TITLE: {job_title}\n\nJOB DESCRIPTION:\n{jd_text}\n\nNumber of Qs: {num_questions}"
    content = _chat(
        [{"role": "system", "content": system_prompt},
         {"role": "user", "content": user_content}]
    )

    try:
        data = json.loads(content)
        if isinstance(data, list):
            return data
        return [{"question": "Explain your relevant experience.", "answer": str(data)}]
    except Exception:
        return [{"question": "Explain your relevant experience.", "answer": content[:800]}]


def extract_jd_skills_llm(jd_text: str) -> List[str]:
    """
    Extract required + preferred skills from a job description using LLM.
    No hardcoded vocabulary — works for any industry or tech stack.
    Raises on failure so callers can fall back to regex heuristic.
    """
    system_prompt = (
        "You are a job description analyst. Extract ALL technical skills, tools, "
        "frameworks, programming languages, and platforms from this job description. "
        "Include both required and preferred skills. "
        "Return ONLY a valid JSON array of lowercase strings. "
        'Example: ["python", "aws", "docker", "rest apis"]. '
        "Do not include soft skills like communication or teamwork."
    )
    content = _chat([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": jd_text[:3000]},
    ])
    parsed = json.loads(content)
    if isinstance(parsed, list):
        return sorted(str(s).lower() for s in parsed)
    raise ValueError("LLM did not return a JSON list")


def generate_fit_summary_llm(
    resume_skills: List[str],
    job_title: str,
    jd_text: str,
    match_score: float,
    missing_skills: List[str],
    weak_skills: List[str],
) -> str:
    """
    Generate a concise 2-3 sentence fit summary explaining how well
    the candidate matches the role, with specific strengths and gaps.
    """
    system_prompt = (
        "You are an expert career coach. Write a 2-3 sentence fit analysis for a candidate "
        "based on their skills and a job description. Be specific, honest, and actionable. "
        "Mention their strongest matching skills and their most critical gaps."
    )
    user_content = (
        f"Role: {job_title}\n"
        f"Match Score: {match_score:.1f}/100\n"
        f"Candidate Skills: {', '.join(resume_skills[:30])}\n"
        f"Missing Skills: {', '.join(missing_skills[:8])}\n"
        f"Partial Matches: {', '.join(weak_skills[:8])}\n\n"
        f"Job Description (excerpt):\n{jd_text[:1500]}"
    )
    return _chat([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ])


def extract_skills_llm(resume_text: str) -> List[str]:
    """
    Use the LLM to extract skills dynamically from resume text.
    No hardcoded vocabulary — works for any domain or industry.
    Returns a sorted list of skill strings.
    Raises on failure so callers can fall back to heuristic.
    """
    system_prompt = (
        "You are a resume analysis expert. Extract all technical skills, tools, "
        "frameworks, programming languages, platforms, and professional competencies "
        "mentioned in the resume text. Return ONLY a valid JSON array of strings. "
        "Example: [\"Python\", \"FastAPI\", \"Docker\", \"Machine Learning\"]. "
        "Do not include soft skills like 'communication' or 'teamwork'."
    )
    # Limit text to keep token usage low (first 3000 chars covers skills section)
    content = _chat([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": resume_text[:3000]},
    ])
    parsed = json.loads(content)
    if isinstance(parsed, list):
        return sorted(str(s) for s in parsed)
    raise ValueError("LLM did not return a JSON list")


def get_skill_coverage_llm(skill_from: str, skill_to: str) -> float:
    """
    Ask LLM how much knowing skill_from conceptually covers skill_to.
    Returns 0.0-1.0. Caller stores the result in the skill_coverage DB table.
    """
    system_prompt = (
        "You are a senior software engineering expert and technical curriculum designer.\n\n"
        "Task: Evaluate how much practical knowledge someone gains for skill_B "
        "by having expertise in skill_A.\n\n"
        "Evaluate across these five dimensions:\n\n"
        "1. SYNTAX/LANGUAGE SUPERSET\n"
        "   Is skill_A a syntactic superset of skill_B? Do they share the same runtime?\n"
        "   Example: C++ contains all of C. TypeScript contains all of JavaScript.\n\n"
        "2. FRAMEWORK/DEPENDENCY CHAIN\n"
        "   Does using skill_A require skill_B as a mandatory core dependency?\n"
        "   Example: Django requires Python. React requires JavaScript. PyTorch requires NumPy.\n\n"
        "3. CONCEPTUAL AND PARADIGM TRANSFER\n"
        "   Do they share programming paradigms (OOP, functional, declarative, imperative)?\n"
        "   Do algorithms, design patterns, and mental models from A apply directly in B?\n\n"
        "4. DOMAIN AND USE-CASE OVERLAP\n"
        "   Are they used to solve the same category of problems?\n"
        "   Could someone expert in skill_A be productive in skill_B with minimal ramp-up?\n\n"
        "5. INDUSTRY BRIDGEABILITY\n"
        "   Is there a well-known migration path between them in the industry?\n"
        "   Would a technical interviewer accept skill_A knowledge for skill_B requirements?\n\n"
        "SCORING GUIDE:\n"
        "  0.90-1.00  Complete. A is a direct superset of B, or B is a mandatory dependency of A.\n"
        "  0.75-0.89  Very high. Deep conceptual overlap, same paradigm, minimal ramp-up.\n"
        "  0.60-0.74  High. Strong transfer, same domain, different surface API.\n"
        "  0.40-0.59  Moderate. Some paradigm overlap, significant new concepts still needed.\n"
        "  0.20-0.39  Low. Adjacent domain, limited direct concept transfer.\n"
        "  0.00-0.19  Minimal. Different paradigms and domains. No meaningful advantage.\n\n"
        "CRITICAL RULES:\n"
        "  - Be asymmetric: C++ covers C (0.85) but C covers C++ (0.20) — C lacks OOP/templates.\n"
        "  - Framework implies language MORE than language implies framework.\n"
        "  - Shared category (both databases) does NOT automatically mean high coverage.\n"
        "  - Base on realistic employer expectations, not theoretical relationships.\n\n"
        "Return ONLY a single decimal number between 0.00 and 1.00. No explanation. No text."
    )
    user_content = (
        f"skill_A = '{skill_from}'\n"
        f"skill_B = '{skill_to}'\n\n"
        "How much does expertise in skill_A prepare someone to work with skill_B?"
    )
    raw = _chat([
        {"role": "system", "content": system_prompt},
        {"role": "user",   "content": user_content},
    ])
    score = float(raw.strip())
    return max(0.0, min(1.0, score))
