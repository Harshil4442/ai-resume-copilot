"""Answer supported questions by quoting scoped stored data, without generation."""
from __future__ import annotations

import re

from ...schemas import RagAskResponse
from ..interview_catalog import CATALOG_VERSION, curated_interview_questions


def _answer(text: str, sources: list[str], *, confidence: str = 'high', provenance: str = 'stored_data') -> RagAskResponse:
    return RagAskResponse(
        answer=text, confidence=confidence, sources=sources,
        mode='direct', provenance=provenance,
        suggested_followups=['Which skills lack resume evidence?', 'What is my recorded match score?', 'Show my approved evidence.'],
    )


def direct_match_answer(*, resume, match, question: str, approved_evidence: list[dict] | None = None) -> RagAskResponse | None:
    """Conservative supported intents; do not infer unrecorded facts or causes."""
    q = ' '.join(question.casefold().split())
    match_ref = f'match:{match.id}'
    resume_ref = f'resume:{resume.id}'
    if re.search(r'\b(?:missing|gaps?|lack|lacking)\b', q) and re.search(r'\b(?:skills?|requirements?|evidence)\b', q) and not re.search(r'\b(?:rewrite|write|draft|salary|eligible|eligibility)\b', q):
        gaps = [str(item) for item in (match.true_gaps or [])]
        partial = [str(item.get('skill', '')) for item in (match.partial_matches or []) if isinstance(item, dict) and item.get('skill')]
        text = 'The saved analysis records missing resume evidence for: ' + ', '.join(gaps) + '.' if gaps else 'The saved analysis has no missing-skill entries. An empty list does not establish full qualification.'
        if partial:
            text += ' Related or partial coverage was recorded for: ' + ', '.join(partial) + '.'
        text += ' Missing resume evidence is not proof that you cannot do the work. Review the actual requirements before deciding what to learn or add.'
        return _answer(text, [f'{match_ref}:true_gaps', f'{match_ref}:partial_matches'], provenance='stored_analysis')
    if re.search(r'\b(?:score|grade)\b', q) and re.search(r'\b(?:what|why|explain|show|how)\b', q) and not re.search(r'\b(?:increase|improve|predict|chance|probability|would|if)\b', q):
        score = float(match.match_score or 0)
        text = f'The recorded match score is {score:.1f}/100. It is an assessment from saved analysis, not an interview or offer probability.'
        summary = str(match.fit_summary or '').strip()
        if summary:
            text += f' Saved interpretation: {summary}'
        text += ' Saved interpretations may come from earlier AI analysis; this response makes no new model call.'
        return _answer(text, [f'{match_ref}:match_score', f'{match_ref}:fit_summary'], provenance='stored_analysis')
    if re.search(r'\bskills?\b', q) and re.search(r'\b(?:my resume|resume lists?|do i have|current skills)\b', q) and not re.search(r'\b(?:missing|gaps?|rewrite|add)\b', q):
        skills = [str(item) for item in (resume.skills or [])]
        text = 'Your saved resume lists: ' + ', '.join(skills) + '.' if skills else 'No skills are recorded for this resume.'
        return _answer(text + ' These are recorded mentions, not independently verified proficiency.', [f'{resume_ref}:skills'])
    if re.search(r'\b(?:approved evidence|approved facts?|approved experience)\b', q) and re.search(r'\b(?:show|list|which|what)\b', q):
        evidence = approved_evidence or []
        if not evidence:
            return _answer('No approved evidence is linked to this resume. Review or add a relevant fact before drafting a personal answer.', [])
        text = 'Approved evidence for this resume:\n' + '\n'.join(
            f"- {item.get('title', 'Evidence')}: {item.get('text', '')}" for item in evidence[:6]
        )
        return _answer(text, [f"evidence:{item['id']}" for item in evidence[:6]])
    if re.search(r'\binterview\b', q) and re.search(r'\b(?:questions?|practice|prepare)\b', q) and not re.search(r'\b(?:answer|write|draft|rewrite)\b', q):
        questions = curated_interview_questions(match.job_title, match.job_description or '', approved_evidence=approved_evidence)
        text = 'Curated role practice questions:\n' + '\n'.join(f"{index}. {item['question']}" for index, item in enumerate(questions, 1))
        text += '\nUse approved facts for personal examples; these questions do not assert candidate achievements.'
        return RagAskResponse(answer=text, confidence='medium', sources=[f'catalog:{CATALOG_VERSION}', f'{match_ref}:job_description'], mode='direct', provenance='curated', suggested_followups=['Show my approved evidence.', 'Which skills lack resume evidence?'])
    return None


def unsupported_direct_answer() -> RagAskResponse:
    return RagAskResponse(
        answer='This question needs interpretation or information that is not available in the saved fields. I can directly show missing resume evidence, the recorded score, resume skills, approved facts, or curated interview questions. Select enhanced mode for optional, reviewed synthesis.',
        confidence='low', mode='unavailable', provenance='local_guidance', sources=[],
        suggested_followups=['Which skills lack resume evidence?', 'What is my recorded match score?', 'Show my approved evidence.'],
    )
