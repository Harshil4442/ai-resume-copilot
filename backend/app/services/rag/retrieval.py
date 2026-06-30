import math
import re
from collections import Counter
from typing import Dict, List, Tuple

from .chunking import EvidenceChunk


Intent = str


INTENT_KEYWORDS: Dict[Intent, List[str]] = {
    "missing_skills": ["missing", "gap", "lack", "skills", "requirement", "required"],
    "score_explanation": ["score", "low", "high", "why", "grade", "match", "fit"],
    "evidence_or_proof": ["prove", "proven", "evidence", "show", "demonstrate", "resume"],
    "interview_prep": ["interview", "prepare", "question", "answer", "talk", "explain"],
    "learning_path": ["learn", "learning", "study", "project", "roadmap", "improve first"],
    "resume_improvement": ["resume", "bullet", "rewrite", "improve", "add", "highlight"],
}


SOURCE_BOOSTS: Dict[Intent, Dict[str, float]] = {
    "missing_skills": {
        "true_gaps": 1.4,
        "required_skills": 1.0,
        "partial_matches": 0.7,
        "job_description": 0.5,
    },
    "score_explanation": {
        "match_score": 1.2,
        "dimension_scores": 1.0,
        "fit_summary": 0.9,
        "true_gaps": 0.7,
        "partial_matches": 0.5,
    },
    "evidence_or_proof": {
        "resume_experience": 1.2,
        "resume_projects": 1.1,
        "full_matches": 0.9,
        "partial_matches": 0.8,
        "resume_skills": 0.5,
    },
    "interview_prep": {
        "job_description": 1.0,
        "fit_summary": 0.8,
        "dimension_scores": 0.8,
        "true_gaps": 0.6,
        "resume_experience": 0.5,
        "resume_projects": 0.5,
    },
    "learning_path": {
        "true_gaps": 1.2,
        "partial_matches": 0.9,
        "improvement_tips": 0.9,
        "required_skills": 0.7,
    },
    "resume_improvement": {
        "improvement_tips": 1.1,
        "resume_experience": 0.9,
        "resume_projects": 0.9,
        "true_gaps": 0.6,
        "dimension_scores": 0.5,
    },
    "general": {
        "fit_summary": 0.6,
        "dimension_scores": 0.5,
        "job_description": 0.4,
    },
}


def tokenize(text: str) -> List[str]:
    return [
        t for t in re.findall(r"[a-zA-Z0-9+#.]+", (text or "").lower())
        if len(t) > 1
    ]


def classify_intent(question: str) -> Intent:
    q_tokens = set(tokenize(question))
    best_intent = "general"
    best_score = 0
    for intent, words in INTENT_KEYWORDS.items():
        score = sum(1 for word in words if word in q_tokens or word in question.lower())
        if score > best_score:
            best_intent = intent
            best_score = score
    return best_intent


def _idf(chunks: List[EvidenceChunk]) -> Dict[str, float]:
    doc_count = max(len(chunks), 1)
    freq: Counter[str] = Counter()
    for chunk in chunks:
        freq.update(set(tokenize(f"{chunk.title} {chunk.text}")))
    return {term: math.log((doc_count + 1) / (count + 1)) + 1 for term, count in freq.items()}


def _tfidf_similarity(question_terms: List[str], chunk_terms: List[str], idf: Dict[str, float]) -> float:
    if not question_terms or not chunk_terms:
        return 0.0
    q_counts = Counter(question_terms)
    c_counts = Counter(chunk_terms)
    shared = set(q_counts) & set(c_counts)
    numerator = sum(q_counts[t] * c_counts[t] * idf.get(t, 1.0) for t in shared)
    q_norm = math.sqrt(sum((count * idf.get(t, 1.0)) ** 2 for t, count in q_counts.items()))
    c_norm = math.sqrt(sum((count * idf.get(t, 1.0)) ** 2 for t, count in c_counts.items()))
    if q_norm == 0 or c_norm == 0:
        return 0.0
    return numerator / (q_norm * c_norm)


def rank_chunks(question: str, chunks: List[EvidenceChunk], top_k: int = 7) -> Tuple[Intent, List[EvidenceChunk]]:
    intent = classify_intent(question)
    if not chunks:
        return intent, []
    idf = _idf(chunks)
    q_terms = tokenize(question)
    boosts = SOURCE_BOOSTS.get(intent, SOURCE_BOOSTS["general"])

    scored: List[Tuple[float, EvidenceChunk]] = []
    for chunk in chunks:
        terms = tokenize(f"{chunk.title} {chunk.text}")
        lexical = _tfidf_similarity(q_terms, terms, idf)
        rule_boost = boosts.get(chunk.source, 0.0)
        final = lexical + rule_boost + chunk.priority
        scored.append((final, chunk))

    scored.sort(key=lambda item: item[0], reverse=True)
    return intent, [item[1] for item in scored[:top_k]]

