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
