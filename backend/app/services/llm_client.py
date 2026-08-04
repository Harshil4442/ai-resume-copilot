import json
import logging
import os
import random
import time
from typing import List, Dict

import httpx

logger = logging.getLogger(__name__)

GEMINI_FALLBACK_MODELS = (
    "gemini-3.6-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.5-flash",
    "gemini-3.1-flash-lite",
    "gemini-2.5-flash",
)


class LLMProviderError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


def _gemini_error_disposition(exc: Exception) -> tuple[bool, bool]:
    """Return whether to try another model and whether the task should retry."""
    message = str(exc).lower()
    model_unavailable = any(
        marker in message for marker in ("404", "not found", "is not supported")
    )
    transient = any(
        marker in message
        for marker in (
            "408",
            "429",
            "500",
            "502",
            "503",
            "504",
            "high demand",
            "quota",
            "rate limit",
            "resource exhausted",
            "temporarily",
            "timed out",
            "timeout",
            "unavailable",
        )
    )
    return model_unavailable or transient, transient


def _gemini_models(target_model: str) -> list[str]:
    return list(dict.fromkeys((target_model, *GEMINI_FALLBACK_MODELS)))

# Read lazily so tests / local dev without a key still import cleanly
def _api_key() -> str:
    return os.getenv("LLM_API_KEY", "").strip()

LLM_API_BASE = os.getenv("LLM_API_BASE", "https://api.openai.com/v1").strip()
LLM_MODEL = os.getenv("LLM_MODEL", "").strip()
if not LLM_MODEL:
    # Heuristic warning: gpt-4o-mini does not exist on Groq, so if no model is
    # set but the base looks non-OpenAI, warn loudly instead of silently 404ing.
    import warnings as _warnings
    if "openai.com" not in LLM_API_BASE:
        _warnings.warn(
            f"LLM_MODEL is not set and LLM_API_BASE={LLM_API_BASE!r} is not OpenAI. "
            "Set LLM_MODEL explicitly (e.g. a Groq model name) to avoid 404s.",
            stacklevel=2,
        )
    LLM_MODEL = "gpt-4o-mini"

def _chat(messages: List[Dict]) -> str:
    key = _api_key()
    if not key:
        raise RuntimeError("LLM_API_KEY is not set.")

    if LLM_MODEL.startswith("gemini"):
        try:
            from google import genai
            from google.genai import types
        except ImportError:
            raise RuntimeError("google-genai package is not installed. Run pip install google-genai.")
            
        client = genai.Client(api_key=key.strip('"\' \r\n'))
        
        gemini_messages = []
        system_text = ""
        
        for m in messages:
            if m["role"] == "system":
                system_text += m["content"] + "\n\n"
            elif m["role"] == "user":
                text = m["content"]
                if system_text:
                    text = f"### SYSTEM INSTRUCTIONS:\n{system_text}\n\n### USER INPUT:\n{text}"
                    system_text = "" # prepend only once
                gemini_messages.append({"role": "user", "parts": [{"text": text}]})
            elif m["role"] == "assistant":
                gemini_messages.append({"role": "model", "parts": [{"text": m["content"]}]})
                
        # Gemini 3.x models use their documented default sampling behavior.
        config = types.GenerateContentConfig()
            
        target_model = LLM_MODEL.strip('"\' \r\n')
        models_to_try = _gemini_models(target_model)
        
        logger.debug(
            "Gemini model fallback initialized",
            extra={"target_model": target_model, "fallback_count": len(models_to_try)},
        )
        
        last_error = None
        saw_transient_error = False
        for attempt_model in models_to_try:
            try:
                logger.debug(
                    "Attempting Gemini model",
                    extra={"attempt_model": attempt_model},
                )
                response = client.models.generate_content(
                    model=attempt_model,
                    contents=gemini_messages,
                    config=config
                )
                if response and hasattr(response, "text"):
                    logger.info(
                        "Gemini request completed",
                        extra={"attempt_model": attempt_model},
                    )
                    return response.text
                return ""
            except Exception as e:
                last_error = e
                try_fallback, transient = _gemini_error_disposition(e)
                saw_transient_error = saw_transient_error or transient
                logger.warning(
                    "Gemini model attempt failed",
                    extra={
                        "attempt_model": attempt_model,
                        "try_fallback": try_fallback,
                        "transient": transient,
                    },
                )
                if try_fallback:
                    continue
                raise LLMProviderError("Gemini provider rejected the request.") from e

        message = (
            "All configured Gemini models were temporarily unavailable."
            if saw_transient_error
            else "No configured Gemini model is available."
        )
        raise LLMProviderError(message, retryable=saw_transient_error) from last_error

    # Strip trailing slash to prevent 404 double-slash errors (e.g., //chat/completions)
    base_url = LLM_API_BASE.rstrip("/")
    url = f"{base_url}/chat/completions"
    
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": LLM_MODEL,
        "messages": messages,
        "temperature": 0.3,
    }

    max_retries = 5
    for attempt in range(max_retries):
        try:
            resp = httpx.post(
                url,
                json=payload,
                headers=headers,
                timeout=httpx.Timeout(90, connect=10),
            )
            
            if resp.status_code == 429:
                # 429 is the rate limit error. Wait longer each time.
                if attempt < max_retries - 1:
                    sleep_time = (5 * (attempt + 1)) + random.random()
                    logger.warning(
                        "LLM provider rate limited; retrying",
                        extra={
                            "attempt": attempt + 1,
                            "max_retries": max_retries,
                            "retry_delay_seconds": round(sleep_time, 1),
                        },
                    )
                    time.sleep(sleep_time)
                    continue
            
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]
            
        except httpx.HTTPStatusError as e:
            if resp.status_code == 429 and attempt < max_retries - 1:
                continue # Already handled above, but just in case
            if attempt == max_retries - 1:
                raise RuntimeError("LLM provider returned an HTTP error.") from e
            time.sleep(2)
        except Exception as e:
            if attempt == max_retries - 1:
                raise RuntimeError("LLM provider request failed.") from e
            time.sleep(2)
    
    raise RuntimeError("All LLM provider retries failed.")


def _extract_json_object(raw: str) -> Dict:
    cleaned = (raw or "").strip()
    if "```" in cleaned:
        for part in cleaned.split("```"):
            part = part.strip()
            if part.lower().startswith("json"):
                part = part[4:].strip()
            if part.startswith("{"):
                cleaned = part
                break
    if not cleaned.startswith("{"):
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            cleaned = cleaned[start:end + 1]
    return json.loads(cleaned)


def chat_json(messages: List[Dict]) -> Dict:
    """Run a chat-completions request and parse a JSON object response."""
    return _extract_json_object(_chat(messages))

def rewrite_bullets(resume_text: str, jd_text: str, tone: str) -> Dict:
    system_prompt = (
        "You are an evidence-preserving resume editor. Rewrite only facts explicitly present in "
        "the candidate's resume. Never add or infer metrics, employers, dates, tools, skills, scope, "
        "ownership, or outcomes. If a fact needed for STAR is missing, keep the claim modest and "
        "state the missing detail in the summary instead of fabricating it. Align wording with the "
        "job description only when the resume already supports that wording. "
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

def generate_interview_questions(
    job_title: str,
    jd_text: str,
    num_questions: int = 8,
    approved_evidence: List[Dict] | None = None,
) -> List[Dict[str, object]]:
    evidence = approved_evidence or []
    allowed_ids = {str(item.get("id")) for item in evidence if item.get("id")}
    system_prompt = (
        "You are an evidence-grounded interview coach. Generate thoughtful interview questions "
        "for the job description and a concise coaching angle for each. Never write a fictional "
        "first-person model answer or invent candidate history, metrics, skills, or outcomes. "
        "Reference only evidence IDs supplied by the user. If no evidence supports a question, use "
        "an empty evidence_ids list. Output a JSON list with question, coaching_angle, and evidence_ids."
    )
    evidence_context = json.dumps(evidence, default=str)[:6000]
    user_content = (
        f"JOB TITLE: {job_title}\n\nJOB DESCRIPTION:\n{jd_text}\n\n"
        f"APPROVED EVIDENCE:\n{evidence_context}\n\nNumber of Qs: {num_questions}"
    )
    content = _chat(
        [{"role": "system", "content": system_prompt},
         {"role": "user", "content": user_content}]
    )

    try:
        data = json.loads(content)
        if not isinstance(data, list):
            raise ValueError("Interview response was not a list")
    except Exception:
        data = [
            {
                "question": "Which approved experience best demonstrates your fit for this role?",
                "coaching_angle": "Choose one relevant example and explain the context, action, and verified result.",
                "evidence_ids": [],
            }
        ]

    evidence_by_id = {str(item.get("id")): item for item in evidence if item.get("id")}
    questions: List[Dict[str, object]] = []
    for raw_item in data[:num_questions]:
        if not isinstance(raw_item, dict):
            continue
        question = str(raw_item.get("question") or "Explain your relevant experience.").strip()
        coaching_angle = str(
            raw_item.get("coaching_angle")
            or "Explain the context, your action, and the verified result."
        ).strip()
        raw_ids = raw_item.get("evidence_ids")
        evidence_ids = (
            [str(item) for item in raw_ids if str(item) in allowed_ids]
            if isinstance(raw_ids, list)
            else []
        )
        cited = [evidence_by_id[item] for item in evidence_ids]
        if cited:
            facts = "; ".join(
                f"{item.get('title', 'Evidence')}: {item.get('text', '')}" for item in cited
            )[:1800]
            answer = f"Approved facts to use: {facts}\n\nCoaching focus: {coaching_angle}"
            answer_state = "evidence_backed"
        else:
            answer = (
                "No approved evidence is linked to this question yet. Add or approve a relevant "
                f"fact before drafting a personal answer. Coaching focus: {coaching_angle}"
            )
            answer_state = "evidence_needed"
        questions.append(
            {
                "question": question,
                "answer": answer,
                "evidence_ids": evidence_ids,
                "answer_state": answer_state,
            }
        )
    return questions


def tailor_resume_from_evidence(
    *,
    job_title: str,
    jd_text: str,
    approved_evidence: List[Dict],
) -> Dict:
    """Create traceable resume copy using approved evidence as the only fact source."""
    if not approved_evidence:
        raise ValueError("At least one approved evidence item is required")

    allowed = {
        str(item["id"]): item
        for item in approved_evidence
        if item.get("id") and str(item.get("text") or "").strip()
    }
    system_prompt = (
        "You are an evidence-preserving resume editor. Transform only the supplied APPROVED "
        "EVIDENCE into concise resume language relevant to the target job. Never add, infer, or "
        "exaggerate any metric, employer, date, skill, tool, responsibility, seniority, scope, or "
        "outcome. Every summary statement and bullet must cite one or more supplied evidence IDs. "
        "Unsupported job requirements belong in evidence_needed, never in candidate claims. Return "
        "only JSON with summary_items [{text,evidence_ids}], bullets [{text,evidence_ids}], and "
        "evidence_needed [string]."
    )
    data = chat_json(
        [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": (
                    f"TARGET JOB: {job_title}\n\nJOB DESCRIPTION:\n{jd_text[:3500]}\n\n"
                    f"APPROVED EVIDENCE:\n{json.dumps(approved_evidence, default=str)[:9000]}"
                ),
            },
        ]
    )

    def sourced_items(key: str, limit: int) -> List[Dict]:
        result: List[Dict] = []
        raw_items = data.get(key, [])
        if not isinstance(raw_items, list):
            return result
        for raw_item in raw_items[:limit]:
            if not isinstance(raw_item, dict):
                continue
            text = str(raw_item.get("text") or "").strip()
            raw_ids = raw_item.get("evidence_ids")
            ids = (
                list(dict.fromkeys(str(value) for value in raw_ids if str(value) in allowed))
                if isinstance(raw_ids, list)
                else []
            )
            if not text or not ids:
                continue
            result.append(
                {
                    "text": text,
                    "evidence_ids": ids,
                    "sources": [
                        {
                            "id": evidence_id,
                            "title": allowed[evidence_id].get("title", "Evidence"),
                            "evidence_text": allowed[evidence_id].get("text", ""),
                        }
                        for evidence_id in ids
                    ],
                }
            )
        return result

    summary_items = sourced_items("summary_items", 3)
    bullets = sourced_items("bullets", 12)
    if not summary_items and not bullets:
        raise ValueError("The model returned no evidence-cited resume content")
    raw_needed = data.get("evidence_needed", [])
    evidence_needed = (
        [str(item).strip() for item in raw_needed if str(item).strip()][:10]
        if isinstance(raw_needed, list)
        else []
    )
    approved_skills = sorted(
        {
            str(skill).strip()
            for item in approved_evidence
            for skill in (item.get("skills") if isinstance(item.get("skills"), list) else [])
            if str(skill).strip()
        }
    )
    return {
        "target_job_title": job_title,
        "summary_items": summary_items,
        "bullets": bullets,
        "skills": approved_skills,
        "evidence_needed": evidence_needed,
        "evidence_policy": "approved_only",
    }


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



def compute_holistic_match_llm(
    experience_text: str,
    projects_text: str,
    education_text: str,
    resume_skills: List[str],
    experience_years: float,
    jd_text: str,
    job_title: str,
    applied_skills_score: float,
    claimed_skills_score: float,
    skill_verification_rate: int,
) -> dict:
    """
    Score all non-skill dimensions as a senior hiring manager would.
    Returns: {dimensions: [{name, score, feedback}], improvement_tips: [str]}
    Bug fix: prompt now uses concrete integers (not ranges) so LLM returns valid JSON.
    """
    # Use json.dumps to safely embed the example structure in the prompt
    example = json.dumps({
        "dimensions": [
            {"name": "Experience Level Fit",  "score": 75, "feedback": "Replace with real feedback."},
            {"name": "Role Relevance",         "score": 80, "feedback": "Replace with real feedback."},
            {"name": "Domain / Industry Fit",  "score": 60, "feedback": "Replace with real feedback."},
            {"name": "Project Relevance",      "score": 70, "feedback": "Replace with real feedback."},
            {"name": "Achievement Quality",    "score": 65, "feedback": "Replace with real feedback."},
            {"name": "Career Trajectory",      "score": 85, "feedback": "Replace with real feedback."},
            {"name": "Education Fit",          "score": 90, "feedback": "Replace with real feedback."},
            {"name": "Employment Stability",   "score": 75, "feedback": "Replace with real feedback."},
        ],
        "improvement_tips": ["Tip 1", "Tip 2", "Tip 3"],
    }, indent=2)

    system_prompt = (
        "You are a senior hiring manager and technical recruiter with 15+ years of experience "
        "across software engineering, data science, and product roles.\n\n"
        "Analyze the candidate resume against the job description and score each dimension "
        "exactly as a real hiring manager would — be honest, specific, and practical.\n\n"
        "Return a JSON object with this EXACT structure (replace example scores and feedback):\n"
        + example + "\n\n"
        "RULES:\n"
        "- score MUST be a plain integer between 0 and 100 (NOT a range like 0-100).\n"
        "- feedback MUST be 1-2 specific sentences referencing actual resume content.\n"
        "- Return ONLY the raw JSON — no markdown fences, no extra text.\n\n"
        "DIMENSION SCORING GUIDELINES:\n"
        "Experience Level Fit: Years of experience + seniority signals vs JD requirement.\n"
        "Role Relevance: Similarity of past job titles and responsibilities to target role.\n"
        "Domain / Industry Fit: Has candidate worked in the same industry/domain?\n"
        "Project Relevance: Do projects align with the JD tech stack and problem domain?\n"
        "Achievement Quality: Quantified metrics (%, $, scale numbers) vs vague duties.\n"
        "Career Trajectory: Upward progression, lateral moves, or regression?\n"
        "Education Fit: Degree/field/certifications match JD requirements?\n"
        "Employment Stability: Average tenure per company (2+ years preferred)."
    )
    user_content = (
        f"TARGET ROLE: {job_title}\n\n"
        f"JOB DESCRIPTION:\n{jd_text[:2000]}\n\n"
        f"WORK EXPERIENCE:\n{experience_text[:2000]}\n\n"
        f"PROJECTS:\n{projects_text[:1000]}\n\n"
        f"EDUCATION:\n{education_text[:500]}\n\n"
        f"SKILL SUMMARY:\n"
        f"Total experience: {experience_years:.1f} years | "
        f"Skills: {', '.join(resume_skills[:25])} | "
        f"Applied score: {applied_skills_score:.0f}/100 | "
        f"Claimed score: {claimed_skills_score:.0f}/100 | "
        f"Verification rate: {skill_verification_rate}%"
    )
    raw = _chat([
        {"role": "system", "content": system_prompt},
        {"role": "user",   "content": user_content},
    ])

    # Robustly strip markdown fences
    cleaned = raw.strip()
    if "```" in cleaned:
        for part in cleaned.split("```"):
            part = part.strip()
            if part.lower().startswith("json"):
                part = part[4:].strip()
            if part.startswith("{"):
                cleaned = part
                break

    result = json.loads(cleaned.strip())
    if "dimensions" not in result:
        result["dimensions"] = []
    if "improvement_tips" not in result:
        result["improvement_tips"] = []
    # Coerce scores to float in case LLM returns strings
    for dim in result["dimensions"]:
        dim["score"] = float(dim.get("score", 50))
    return result


def analyze_job_match_mega_llm(
    resume_sections: dict,
    resume_skills: List[str],
    experience_years: float,
    jd_text: str,
    job_title: str
) -> dict:
    """
    All-in-one analysis prompt. Combines:
    1. JD Skill Extraction
    2. Skill Match/Coverage Analysis
    3. Holistic Dimension Scoring
    4. Fit Summary & Tips
    """
    system_prompt = (
        "You are a senior hiring manager and expert technical recruiter. Your task is to perform a "
        "deep, multi-dimensional analysis of a candidate resume against a job description.\n\n"
        "### DIMENSION SCORING GUIDELINES (0-100):\n"
        "1. Experience Level Fit: Years/seniority vs JD requirements.\n"
        "2. Role Relevance: Past titles/duties similarity to target role.\n"
        "3. Domain/Industry Fit: Sector overlap (e.g., Fintech, AI, SaaS).\n"
        "4. Project Relevance: Side projects alignment with role tech/domain.\n"
        "5. Achievement Quality: Quantified impact statements (%, $, scale).\n"
        "6. Career Trajectory: Progression, growth, and stability signal.\n"
        "7. Education Fit: Degree/field match.\n"
        "8. Employment Stability: Average tenure (2+ years is green).\n\n"
        "### OUTPUT JSON FORMAT:\n"
        "Return ONLY a JSON object with this structure:\n"
        "{\n"
        "  \"extracted_jd_skills\": [\"python\", \"docker\", ...],\n"
        "  \"skill_analysis\": [\n"
        "    {\n"
        "      \"jd_skill\": \"javascript\",\n"
        "      \"match_type\": \"full\" | \"partial\" | \"gap\",\n"
        "      \"coverage\": 0.0-1.0,\n"
        "      \"via_skill\": \"typescript\" (if partial/full), \n"
        "      \"explanation\": \"Short note on why this match/gap exists\"\n"
        "    }\n"
        "  ],\n"
        "  \"dimensions\": [ {\"name\": \"Experience Level Fit\", \"score\": 85, \"feedback\": \"...\"}, ... ],\n"
        "  \"fit_summary\": \"High-level 3-sentence executive summary...\",\n"
        "  \"improvement_tips\": [\"tip1\", \"tip2\"]\n"
        "}"
    )

    user_content = (
        f"JOB: {job_title}\n"
        f"JD: {jd_text[:1000]}\n\n"
        f"EXP: {experience_years:.1f}y\n"
        f"SKILLS: {', '.join(resume_skills[:20])}\n"
        f"WORK: {resume_sections.get('experience', '')[:1000]}\n"
        f"PROJ: {resume_sections.get('projects', '')[:500]}"
    )

    raw = _chat([
        {"role": "system", "content": system_prompt},
        {"role": "user",   "content": user_content},
    ])
    
    # Robust JSON extraction
    cleaned = raw.strip()
    if "```" in cleaned:
        for part in cleaned.split("```"):
            part = part.strip()
            if part.lower().startswith("json"):
                part = part[4:].strip()
            if part.startswith("{"):
                cleaned = part
                break
                
    return json.loads(cleaned)


def generate_learning_strategy_llm(
    *,
    job_title: str,
    company: str,
    jd_text: str,
    resume_skills: List[str],
    experience_years: float,
    true_gaps: List[str],
    partial_matches: List[Dict],
    required_skills: List[str],
    match_score: float,
    fit_summary: str,
    dimension_scores: List[Dict],
    improvement_tips: List[str],
) -> Dict:
    """
    Generate a match-specific learning strategy. This intentionally focuses on
    hiring signals and project proof, not just course links.
    """
    example = {
        "readiness_summary": "2-3 sentences on the candidate's readiness and the highest-leverage learning focus.",
        "missing_hiring_signals": [
            {
                "signal": "Production deployment experience",
                "why_it_matters": "The job expects deployed services, but the resume does not show evidence of shipping or operating them.",
                "severity": "high"
            }
        ],
        "learning_priorities": [
            {
                "skill": "docker",
                "priority": "high",
                "current_status": "true_gap",
                "reason": "Required by the JD and not evidenced in the resume.",
                "expected_outcome": "Can package and run backend services consistently across environments."
            }
        ],
        "project_recommendations": [
            {
                "title": "Production-style backend deployment project",
                "covers_gaps": ["docker", "postgresql", "ci/cd"],
                "description": "Build a small but realistic backend service that proves the missing hiring signals.",
                "implementation_steps": ["Define the API and schema", "Containerize it", "Deploy it", "Document tradeoffs"],
                "resume_bullets": ["Built and deployed a containerized backend API with persistent storage and automated checks."],
                "interview_talking_points": ["Why you chose the deployment target", "How you handled configuration and secrets"]
            }
        ],
        "timeline": [
            {
                "phase": "Week 1",
                "focus": "Core missing skill",
                "deliverable": "A working artifact that can be shown in GitHub or a demo."
            }
        ]
    }
    system_prompt = (
        "You are a senior hiring manager and career strategist. Create a practical learning strategy "
        "for a candidate targeting one specific job match.\n\n"
        "Do NOT recommend a generic course list. Recommend high-leverage learning priorities and "
        "project work that would create credible hiring evidence for this exact job.\n\n"
        "Return ONLY valid JSON with this exact structure:\n"
        f"{json.dumps(example, indent=2)}\n\n"
        "Rules:\n"
        "- Prioritize 3-6 skills or hiring signals at most.\n"
        "- Prefer projects that cover multiple gaps at once.\n"
        "- Make resume bullets concrete and achievement-oriented.\n"
        "- Keep implementation steps specific enough to act on.\n"
        "- Severity and priority must be one of: high, medium, low.\n"
        "- current_status should be true_gap, partial_coverage, weak_evidence, or improvement.\n"
        "- Return raw JSON only. No markdown fences."
    )

    user_content = (
        f"JOB TITLE: {job_title}\n"
        f"COMPANY: {company or 'Unknown'}\n"
        f"MATCH SCORE: {match_score:.1f}/100\n"
        f"EXPERIENCE YEARS: {experience_years:.1f}\n\n"
        f"JOB DESCRIPTION EXCERPT:\n{jd_text[:2500]}\n\n"
        f"RESUME SKILLS:\n{', '.join(resume_skills[:60])}\n\n"
        f"JD REQUIRED SKILLS:\n{', '.join(required_skills[:60])}\n\n"
        f"TRUE GAPS:\n{', '.join(true_gaps[:30])}\n\n"
        f"PARTIAL MATCHES:\n{json.dumps(partial_matches[:20])}\n\n"
        f"FIT SUMMARY:\n{fit_summary[:1200]}\n\n"
        f"DIMENSION SCORES:\n{json.dumps(dimension_scores[:12])}\n\n"
        f"EXISTING IMPROVEMENT TIPS:\n{json.dumps(improvement_tips[:8])}"
    )

    raw = _chat([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ])

    cleaned = raw.strip()
    if "```" in cleaned:
        for part in cleaned.split("```"):
            part = part.strip()
            if part.lower().startswith("json"):
                part = part[4:].strip()
            if part.startswith("{"):
                cleaned = part
                break

    return json.loads(cleaned)

def tailor_resume_mega_llm(
    resume_text: str,
    jd_text: str,
    template_type: str,
    true_gaps: List[str],
    partial_matches: List[Dict],
    approved_evidence: List[Dict] | None = None,
) -> str:
    """
    Completely rewrite and tailor the resume for a specific job match based on a template.
    Returns a beautifully formatted Markdown string.
    """
    
    tone_instructions = {
        "ats": "Focus heavily on exact keyword matching and a traditional, clean structure. Make it dense with relevant terms.",
        "executive": "Shift the tone to focus on business impact, metrics, team sizes, budgets, and strategic vision rather than just technical execution.",
        "technical": "Place heavy emphasis on the tech stack, architectural decisions, methodologies (e.g., Agile, TDD), and complex problem-solving.",
        "creative": "Highlight specific campaigns, portfolio-style achievements, and direct measurable outcomes (e.g., conversion rates, client satisfaction)."
    }
    
    chosen_tone = tone_instructions.get(template_type.lower(), tone_instructions["ats"])
    
    system_prompt = (
        "You are an expert executive resume writer and career coach. Your task is to rewrite the "
        "provided resume to perfectly match the provided Job Description.\n\n"
        "### STRICT RULES:\n"
        "1. **LaTeX Formatting:** You MUST output raw, valid LaTeX code. Use the standard article class. Do not use markdown.\n"
        "2. **1-Page Constraint:** The content MUST fit on a single A4 page. Be extremely concise.\n"
        "3. **Evidence Boundary:** The original resume and APPROVED EVIDENCE are the only sources of "
        "candidate facts. Never add, infer, exaggerate, or make plausible-sounding metrics, skills, "
        "tools, responsibilities, dates, employers, scope, or outcomes.\n"
        "4. **Known Gaps:** These are gaps, not candidate skills: " + ", ".join(true_gaps[:15]) + ". "
        "Do not claim them. Partial matches may be used only when the source text explicitly supports them.\n"
        "5. **Traceability:** Add a LaTeX comment immediately before every generated bullet in the "
        "form `% Evidence: resume` or `% Evidence: EVIDENCE_ID`. Never cite an ID not supplied.\n"
        "6. **Missing Facts:** Do not insert placeholders or guessed numbers. Omit unsupported claims.\n"
        "7. **Reordering:** Reorder supported bullets so the most relevant evidence appears first.\n"
        "8. **Preserve Links:** You MUST retain any URLs, LinkedIn profiles, GitHub links, and portfolios exactly as they appear in the original resume. Use \\href{url}{text}.\n"
        f"9. **Tone & Style:** {chosen_tone}\n\n"
        "### LATEX TEMPLATE TO USE:\n"
        "```latex\n"
        "\\documentclass[10pt,a4paper]{article}\n"
        "\\usepackage[left=0.5in,top=0.5in,right=0.5in,bottom=0.5in]{geometry}\n"
        "\\usepackage{enumitem}\n"
        "\\usepackage{hyperref}\n"
        "\\usepackage{titlesec}\n"
        "\\titleformat{\\section}{\\large\\bfseries\\uppercase}{}{0em}{}[\\titlerule]\n"
        "\\titlespacing*{\\section}{0pt}{1.5ex plus 1ex minus .2ex}{1ex plus .2ex}\n"
        "\\begin{document}\n"
        "\\begin{center}\n"
        "    {\\huge \\textbf{CANDIDATE NAME}} \\\\\n"
        "    \\vspace{1mm}\n"
        "    EMAIL $|$ PHONE $|$ \\href{LINKEDIN URL}{LinkedIn} $|$ \\href{GITHUB URL}{GitHub}\n"
        "\\end{center}\n"
        "\n"
        "\\section*{Professional Summary}\n"
        "Short paragraph highlighting alignment with the target role.\n"
        "\n"
        "\\section*{Skills}\n"
        "\\textbf{Languages:} ... \\\\\n"
        "\\textbf{Technologies:} ...\n"
        "\n"
        "\\section*{Experience}\n"
        "\\textbf{Job Title} \\hfill Start Date -- End Date \\\\\n"
        "\\textit{Company Name} \\hfill Location \\\\\n"
        "\\begin{itemize}[leftmargin=*,noitemsep]\n"
        "    \\item Spearheaded...\n"
        "\\end{itemize}\n"
        "\n"
        "\\section*{Education}\n"
        "\\textbf{Degree} \\hfill Year \\\\\n"
        "\\textit{University}\n"
        "\\end{document}\n"
        "```\n\n"
        "### OUTPUT FORMAT:\n"
        "You must return ONLY the raw LaTeX string starting with \\documentclass and ending with \\end{document}. Do not include markdown code blocks (```latex) in the final output string. Just the raw LaTeX code."
    )
    
    user_content = (
        f"JOB DESCRIPTION:\n{jd_text[:3000]}\n\n"
        f"CANDIDATE'S ORIGINAL RESUME:\n{resume_text[:4000]}\n\n"
        f"APPROVED EVIDENCE:\n{json.dumps(approved_evidence or [], default=str)[:6000]}"
    )
    
    raw = _chat([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content}
    ])
    
    # Strip markdown formatting just in case the LLM disobeys
    cleaned = raw.strip()
    if cleaned.startswith("```latex"):
        cleaned = cleaned[8:]
    elif cleaned.startswith("```"):
        cleaned = cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
        
    return cleaned.strip()
