from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from backend.app.database import Base
from backend.app.domains.analysis import schemas as analysis_schemas
from backend.app.domains.analysis import service as analysis_service
from backend.app.domains.analysis import tasks
from backend.app.domains.career.service import latest_opportunity_match
from backend.app.models import AnalysisRun, EvidenceItem, JobMatch, Opportunity, Resume, User
from backend.app.routers.rag import ask_ai_about_match
from backend.app.routers.recommendations import match_learning_strategy
from backend.app.schemas import LearningStrategyRequest, RagAskRequest
from backend.app.services import llm_client
from backend.app.services.prompt_privacy import redact_structured
from backend.app.services.rag.chat import ask_match_ai
from backend.app.services.rag.chunking import build_match_chunks
from backend.app.services.recommender import get_skill_gaps_and_courses
from google import genai
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


def _objects():
    resume = SimpleNamespace(id=1, skills=['Python'], sections={'experience': 'Built Python services.', 'projects': 'Automated tests.'}, contact_info={'email': 'private@example.com', 'phone': '+91 9999999999'})
    match = SimpleNamespace(id=2, job_title='Software Engineer', job_description='Build Python and Docker services.', company='Example', match_score=50, true_gaps=['Docker'], partial_matches=[], required_skills=['Python', 'Docker'], full_matches=['Python'], skill_verification_rate=0, dimension_scores=[], fit_summary='Recorded Python overlap; Docker evidence is missing.', improvement_tips=['Review missing evidence.'])
    return resume, match


@pytest.mark.parametrize('question,expected', [('Which skills lack resume evidence?', 'Docker'), ('What is my recorded match score?', '50.0/100'), ('What skills does my resume list?', 'Python'), ('Give me interview practice questions.', 'Curated role practice questions')])
def test_cold_direct_answers_do_not_generate_and_expose_sources(monkeypatch, question, expected):
    from backend.app.services.rag import chat
    monkeypatch.setattr(chat, 'chat_json', lambda *args: pytest.fail('Direct stored-data answer must not generate'))
    resume, match = _objects()
    response = ask_match_ai(resume=resume, match=match, question=question, recent_messages=[])
    assert expected in response.answer
    assert response.mode == 'direct'
    assert response.sources
    assert 'private@example.com' not in response.answer


def test_unsupported_question_does_not_guess_or_generate(monkeypatch):
    from backend.app.services.rag import chat
    monkeypatch.setattr(chat, 'chat_json', lambda *args: pytest.fail('Basic mode must not generate'))
    resume, match = _objects()
    response = ask_match_ai(resume=resume, match=match, question='Am I legally eligible for this role and what salary will they offer?', recent_messages=[])
    assert response.mode == 'unavailable' and response.confidence == 'low'
    assert response.sources == []


def test_enhanced_open_ended_answer_still_runs_explicitly(monkeypatch):
    from backend.app.services.rag import chat
    calls = []
    def generated(messages):
        calls.append(messages)
        import re
        source = re.search(r'"id": "([^"]+)"', messages[-1]['content']).group(1)
        return {'answer': 'Consider a relevant project; confirm missing requirements first.', 'confidence': 'medium', 'suggested_followups': [], 'sources': [source]}
    monkeypatch.setattr(chat, 'chat_json', generated)
    resume, match = _objects()
    response = ask_match_ai(resume=resume, match=match, question='How could I compare two project ideas for this role?', recent_messages=[], mode='enhanced')
    assert len(calls) == 1
    assert response.mode == 'enhanced' and response.provenance == 'generated'
    assert response.sources


def test_structured_prompt_filter_excludes_sensitive_keys_without_altering_original():
    original = {'skills': ['Python'], 'password': 'secret-password', 'race': 'sensitive-value', 'consent_status': True, 'text': 'Built Python services and reduced latency by 20%. Email: candidate@example.com'}
    sanitized = redact_structured(original)
    assert not {'password', 'race', 'consent_status'} & sanitized.keys()
    assert sanitized['skills'] == ['Python']
    assert '20%' in sanitized['text'] and 'candidate@example.com' not in sanitized['text']
    assert original['password'] == 'secret-password'


def test_real_model_boundary_redacts_identifiers_credentials_and_protected_declarations(monkeypatch, persisted_model_budget):
    sent = []
    class Models:
        def generate_content(self, **kwargs):
            sent.append(kwargs)
            return SimpleNamespace(text='ok')
    monkeypatch.setenv('LLM_API_KEY', 'synthetic')
    monkeypatch.setattr(llm_client, 'LLM_MODEL', 'gemini-3.6-flash')
    monkeypatch.setattr(genai, 'Client', lambda **kwargs: SimpleNamespace(models=Models()))
    original = 'Skills: Python Docker\nReduced latency by 20%.\nEmail: candidate@example.com\nPhone: +91 9999999999\nSSN: 123-45-6789\nGender: female\nPassword: private-secret\nDOB: 1998-01-01'
    assert llm_client._chat([{'role': 'user', 'content': original}]) == 'ok'
    content = sent[0]['contents'][0]['parts'][0]['text']
    for secret in ['candidate@example.com', '9999999999', '123-45-6789', 'female', 'private-secret', '1998-01-01']:
        assert secret not in content
    assert 'Python Docker' in content and '20%' in content
    assert 'candidate@example.com' in original


def test_rag_chunks_exclude_contact_and_filter_other_sensitive_data():
    resume, match = _objects()
    resume.sections['summary'] = 'Python engineer.\nReligion: private-religion\nBuilt services.'
    chunks = build_match_chunks(resume, match)
    assert all('private@example.com' not in chunk.text for chunk in chunks)
    from backend.app.services.rag.prompts import build_ask_ai_messages
    messages = build_ask_ai_messages(question='Explain the source.', intent='general', chunks=chunks, recent_messages=[{'role': 'user', 'content': 'Password: private-password'}])
    text = json.dumps(messages)
    assert 'private-religion' not in text and 'private-password' not in text
    assert 'Built services' in text


def _database():
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    with factory() as db:
        db.add(User(id=1, email='owner@example.com', ai_credits=20, tier='free'))
        db.add(Resume(id=1, user_id=1, original_filename='resume.pdf', raw_text='Python engineer', skills=['Python'], sections={'experience': 'Built Python services'}, experience_years=1))
        db.add(JobMatch(id=2, user_id=1, resume_id=1, job_title='Software Engineer', company='Example', job_description='Build Python and Docker services.', match_score=50, required_skills=['Python', 'Docker'], true_gaps=['Docker'], partial_matches=[], dimension_scores=[], improvement_tips=[]))
        db.add(EvidenceItem(id='approved', user_id=1, resume_id=1, title='API work', evidence_text='Built Python services.', category='experience', approval_state='approved'))
        db.add(EvidenceItem(id='pending', user_id=1, resume_id=1, title='Unreviewed', evidence_text='Invented experience', category='experience', approval_state='pending'))
        db.add(EvidenceItem(id='different-resume', user_id=1, resume_id=None, title='Other fact', evidence_text='Unrelated personal fact', category='experience', approval_state='approved'))
        db.add(Opportunity(id='opportunity', user_id=1, resume_id=1, title='Software Engineer', company='Example', job_description='Build Python and Docker services.', job_snapshot={}))
        db.commit()
    return engine, factory


def test_rag_route_quotes_only_owned_approved_resume_evidence(monkeypatch):
    monkeypatch.setattr(llm_client, '_chat', lambda *args: pytest.fail('No model call expected'))
    engine, factory = _database()
    try:
        with factory() as db:
            response = ask_ai_about_match(RagAskRequest(job_match_id=2, question='Show my approved evidence.'), db, db.get(User, 1))
            assert response.sources == ['evidence:approved']
            assert 'Built Python services' in response.answer
            assert 'Invented experience' not in response.answer and 'Unrelated personal fact' not in response.answer
            assert db.query(AnalysisRun).one().generation_attempt_count == 0
    finally:
        engine.dispose()


def test_curated_learning_default_has_no_generation_or_completed_project_claims(monkeypatch):
    monkeypatch.setattr(llm_client, '_chat', lambda *args: pytest.fail('Curated plan must not generate'))
    engine, factory = _database()
    try:
        with factory() as db:
            result = asyncio.run(match_learning_strategy(LearningStrategyRequest(match_id=2), db, db.get(User, 1)))
            assert result['generated_by'] == 'curated' and result['provenance'] == 'curated'
            assert result['learning_priorities'][0]['skill'] == 'docker'
            assert all(project['resume_bullets'] == [] for project in result['project_recommendations'])
            assert result['warnings']
            assert db.query(AnalysisRun).one().generation_attempt_count == 0
    finally:
        engine.dispose()


def test_enhanced_learning_failure_refunds_then_returns_curated_plan(monkeypatch):
    def fail(**kwargs):
        raise RuntimeError('Provider unavailable')
    monkeypatch.setattr(llm_client, 'generate_learning_strategy_llm', fail)
    engine, factory = _database()
    try:
        with factory() as db:
            result = asyncio.run(match_learning_strategy(LearningStrategyRequest(match_id=2, mode='enhanced'), db, db.get(User, 1)))
            assert result['generated_by'] == 'fallback'
            assert db.get(User, 1).ai_credits == 20
            assert db.query(AnalysisRun).one().usage_state == 'released'
    finally:
        engine.dispose()


def test_saved_match_retains_authoritative_mode_after_interview_run(monkeypatch):
    engine, factory = _database()
    monkeypatch.setattr(tasks, 'SessionLocal', factory)
    try:
        with factory() as db:
            run, _ = analysis_service.create_run(db, user_id=1, payload=analysis_schemas.AnalysisRunCreate(operation='job_match', opportunity_id='opportunity', input={'resume_id': 1, 'job_description': 'Build Python and Docker services.'}), header_idempotency_key='match-metadata-001')
            run_id = run.id
        assert tasks.process_analysis_run(run_id) == 'succeeded'
        with factory() as db:
            interview, _ = analysis_service.create_run(db, user_id=1, payload=analysis_schemas.AnalysisRunCreate(operation='interview_questions', opportunity_id='opportunity'), header_idempotency_key='interview-metadata-001')
            interview_id = interview.id
        assert tasks.process_analysis_run(interview_id) == 'succeeded'
        with factory() as db:
            saved = latest_opportunity_match(db, 1, 'opportunity')
            assert saved.mode == 'basic' and saved.provenance == 'local_rules'
            assert saved.scoring_version == 'basic-catalog-overlap-v1'
    finally:
        engine.dispose()


def test_curated_resources_use_canonical_aliases():
    gaps, _ = get_skill_gaps_and_courses(['JS', 'TypeScript', 'React', 'Nextjs', 'HTML', 'CSS', 'Tailwind CSS', 'testing'], 'Frontend Engineer')
    assert 'javascript' not in gaps and 'next.js' not in gaps and 'tailwind' not in gaps


def test_enhanced_answer_cannot_claim_an_unknown_source(monkeypatch):
    from backend.app.services.rag import chat
    monkeypatch.setattr(chat, 'chat_json', lambda messages: {'answer': 'An unsupported confident claim.', 'confidence': 'high', 'suggested_followups': [], 'sources': ['other-candidate-private']})
    resume, match = _objects()
    result = ask_match_ai(resume=resume, match=match, question='Compare different project approaches for this job.', recent_messages=[], mode='enhanced')
    assert result.mode == 'unavailable' and result.confidence == 'low'
    assert 'unsupported confident claim' not in result.answer
    assert result.sources == []


def test_unstructured_legacy_rewrite_cannot_become_ready_bullets(monkeypatch):
    monkeypatch.setattr(llm_client, '_chat', lambda messages: 'Invented accomplishment in unstructured provider prose.')
    with pytest.raises(llm_client.TailoringOutputError, match='could not be validated'):
        llm_client.rewrite_bullets('Built Python services.', 'Build reliable systems.', 'professional')
