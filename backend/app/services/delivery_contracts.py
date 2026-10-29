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
                                 "enhanced_qa", "learning_strategy", "interview_questions"})


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
    if operation not in AFFECTED_OPERATIONS:
        return None
    from . import (
        interview_contract,
        llm_client,
        personal_claims,
        synchronous_contracts,
        tailoring_contract,
    )
    from .market import skill_taxonomy

    common = (personal_claims.Claim, personal_claims._normalized, personal_claims._anchors, personal_claims._claims,
              personal_claims.has_personal_assertion, personal_claims.require_supported_claims,
              skill_taxonomy.canonical_skill)
    constants: dict[str, Any] = {
        "version": personal_claims.VERSION,
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
    if operation == "interview_questions":
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
                   synchronous_contracts._text, synchronous_contracts._texts)
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
        else:
            helpers += (llm_client.generate_learning_strategy_llm,)
    material = {"operation": operation, "versions": versions, "constants": constants,
                "helpers": [_source(item) for item in (*common, *helpers)]}
    return hashlib.sha256(json.dumps(material, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()
