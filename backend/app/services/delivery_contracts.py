"""Current output contracts for six affected AI routes, without provider calls.

Hashes bind reviewed routes to actual related helpers, finite guard constants and
declared versions. This never creates qualification or upgrades old evidence.
"""
from __future__ import annotations

import hashlib
import inspect
import json
from typing import Any

AFFECTED_OPERATIONS = frozenset({"resume_tailor", "resume_tailor_extended", "bullet_rewrite",
                                 "enhanced_qa", "learning_strategy", "interview_questions",
                                 "enhanced_job_match", "resume_enrichment"})


class OutputContractUnavailable(ValueError):
    pass


def _source(value) -> str:
    try:
        result = inspect.getsource(value)
        if not result.strip():
            raise ValueError
        return result
    except (OSError, TypeError, ValueError):
        raise OutputContractUnavailable("The current output implementation cannot be verified.") from None


def current_output_contract(operation: str) -> str | None:
    if operation == "job_match":
        operation = "enhanced_job_match"
    if operation not in AFFECTED_OPERATIONS:
        return None
    from . import (
        interview_contract,
        llm_client,
        personal_claims,
        prompt_privacy,
        synchronous_contracts,
        tailoring_contract,
    )
    from .market import skill_taxonomy

    common = (prompt_privacy.redact_messages, prompt_privacy.redact_prompt_text, prompt_privacy.redact_structured,
              personal_claims.Claim, personal_claims._normalized, personal_claims._anchors, personal_claims._claims,
              personal_claims.has_personal_assertion, personal_claims.require_supported_claims,
              skill_taxonomy.canonical_skill)
    constants: dict[str, Any] = {
        "version": personal_claims.VERSION,
        "privacy_version": prompt_privacy.PROMPT_PRIVACY_VERSION,
        "privacy_redacted": prompt_privacy.REDACTED,
        "privacy_patterns": {name: (getattr(prompt_privacy, name).pattern, getattr(prompt_privacy, name).flags)
            for name in ("_EMAIL", "_INTERNATIONAL_PHONE", "_SENSITIVE_KEY", "_JSON_LABELED", "_PLAIN_LABELED", "_GOVERNMENT_NUMBER")},
        "complete_source_prefix": prompt_privacy.COMPLETE_SOURCE_PREFIX,
        "relations": {key: (value.pattern, value.flags) for key, value in personal_claims._RELATIONS.items()},
        "patterns": {key: (getattr(personal_claims, key).pattern, getattr(personal_claims, key).flags) for key in (
            "_PERSON", "_SENTENCE", "_CONDITIONAL", "_FUTURE", "_NEGATED", "_WEAK",
            "_NONASSERTING", "_IMPLICIT_PAST", "_METHOD_QUESTION", "_OTHER_ACTOR", "_CURRENT", "_PAST", "_TECHNICAL_SUBJECT",
            "_BIO_FRAGMENT", "_ROLE_REQUIREMENT", "_NEUTRAL_INSTRUCTION", "_FRAGMENT_GOAL", "_SHARED_OBJECT_LINK", "_UNCERTAIN", "_TOKENS")},
        "stop": sorted(personal_claims._STOP), "aliases": personal_claims._ALIASES,
        "method_verbs": sorted(personal_claims._METHOD_VERBS),
        "action_aliases": personal_claims._ACTION_ALIASES,
        "skill_aliases": skill_taxonomy.ALIASES, "skill_taxonomy": skill_taxonomy.SKILL_TAXONOMY,
    }
    helpers: tuple[Any, ...]
    if operation == "enhanced_job_match":
        from . import matching, matching_contract
        from .market import skill_extractor

        helpers = (matching_contract.messages, matching_contract.validate,
                   matching_contract._facts, matching_contract._prose, matching_contract.source_skill,
                   matching_contract.score, llm_client.analyze_job_match_complete_llm,
                   matching_contract.extract_skill_mentions, matching_contract._term_pattern, skill_taxonomy.all_search_terms,
                   interview_contract.unsupported_experience_inference, interview_contract._normalized_personal_text,
                   matching.compute_skill_scores, matching.combine_scores, matching.get_dynamic_weights)
        versions = {"prompt": matching_contract.PROMPT_VERSION, "validator": matching_contract.VALIDATOR_VERSION}
        constants["matching"] = {"dimensions": matching_contract.DIMENSIONS,
            "context_max": matching_contract.MAX_CONTEXT_ESTIMATE,
            "outcome_probability": (matching_contract._OUTCOME_PROBABILITY.pattern, matching_contract._OUTCOME_PROBABILITY.flags),
            "negative_experience": (interview_contract._NEGATIVE_EXPERIENCE.pattern, interview_contract._NEGATIVE_EXPERIENCE.flags),
            "nonasserting_prefix": (interview_contract._NONASSERTING_PREFIX.pattern, interview_contract._NONASSERTING_PREFIX.flags),
            "schema": matching_contract.Output.model_json_schema(),
            "extractor": {"version": skill_extractor.SKILL_EXTRACTOR_VERSION,
                "negation": (skill_extractor._NEGATION.pattern, skill_extractor._NEGATION.flags),
                "literal_negation": (matching_contract._NEGATION.pattern, matching_contract._NEGATION.flags),
                "ambiguity": sorted(skill_extractor._AMBIGUOUS_TERMS),
                "technical_context": (skill_extractor._TECH_CONTEXT.pattern, skill_extractor._TECH_CONTEXT.flags),
                "patterns": {name: [(term, pattern.pattern, pattern.flags) for term, pattern in patterns]
                    for name, patterns in skill_extractor._PATTERNS.items()}},
            "skill_weights": matching.SKILL_WEIGHTS, "dimension_weights": matching.HOLISTIC_DIMENSION_WEIGHTS}
    elif operation == "interview_questions":
        helpers = (interview_contract.build_interview_messages, interview_contract.require_complete_context,
                   interview_contract._normalized_personal_text,
                   interview_contract.unsupported_experience_inference,
                   interview_contract.unsupported_positive_history,
                   llm_client._parse_interview_questions, llm_client.generate_interview_questions)
        versions = interview_contract.contract_versions()
        constants["interview"] = {key: (getattr(interview_contract, key).pattern,
                                        getattr(interview_contract, key).flags) for key in (
            "_NEGATIVE_EXPERIENCE", "_NONASSERTING_PREFIX", "_POSITIVE_HISTORY")}
        constants["preparation"] = (interview_contract.MIN_PREPARATION_STEPS,
                                    interview_contract.MAX_PREPARATION_STEPS,
                                    interview_contract.EXAMPLE_INSTRUCTION)
    elif operation in {"resume_tailor", "resume_tailor_extended"}:
        helpers = (llm_client.build_tailoring_messages, llm_client._validate_factual_terms,
                   llm_client._validate_tailoring_edit, llm_client.tailor_resume_from_evidence,
                   llm_client.tailor_resume_from_evidence_full, llm_client._factual_skill_patterns,
                   llm_client._factual_acronym_base, llm_client._factual_term_pattern,
                   llm_client._factual_acronym_pattern, skill_taxonomy.all_search_terms)
        versions = {"prompt": tailoring_contract.COMPLETE_PROMPT_VERSION,
                    "validator": tailoring_contract.VALIDATOR_VERSION,
                    "context": tailoring_contract.CONTEXT_VERSION}
    else:
        helpers = (synchronous_contracts.messages, synchronous_contracts.validate,
                   synchronous_contracts._supported_personal_prose,
                   synchronous_contracts._text, synchronous_contracts._texts, llm_client.chat_json)
        versions = dict(synchronous_contracts.VERSIONS)
        constants["synchronous_context_max"] = synchronous_contracts.MAX_CONTEXT_ESTIMATE
        if operation == "bullet_rewrite":
            helpers += (llm_client._validate_factual_terms, llm_client._factual_skill_patterns,
                        llm_client._factual_acronym_base, llm_client._factual_term_pattern,
                        llm_client._factual_acronym_pattern, llm_client.rewrite_bullets,
                        skill_taxonomy.all_search_terms)
        elif operation == "enhanced_qa":
            from .rag import chat, prompts

            helpers += (chat.ask_match_ai, prompts.build_ask_ai_messages)
        elif operation == "learning_strategy":
            helpers += (llm_client.generate_learning_strategy_llm,)
    material = {"operation": operation, "versions": versions, "constants": constants,
                "helpers": [_source(item) for item in (*common, *helpers)]}
    return hashlib.sha256(json.dumps(material, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()
