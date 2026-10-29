"""Finite, conservative guards for explicit personal-claim families.

This is not an entailment model. A match requires one local approved assertion
with the same relation, polarity and object anchors; words from unrelated facts
cannot be pooled. Unrecognized implications still require semantic evaluation.
The guards reject a deliverable rather than rewriting or shortening it.
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass

VERSION = "personal-claim-scopes-v1"


class UnsupportedPersonalClaim(ValueError):
    def __init__(self):
        super().__init__("A personal claim is not supported by its cited approved facts.")


_PERSON = re.compile(r"\b(?:you|your|i|my|we|our|the candidate|this candidate|the applicant|this applicant)\b")
_SENTENCE = re.compile(r"(?<=[.!?;])\s+|[\r\n]+|[•●▪◦]+")
_CONDITIONAL = re.compile(r"\b(?:if|unless|whether|assuming|suppose|provided that)\b")
_FUTURE = re.compile(
    r"\b(?:would|could|should|might|may|will|can|plan(?:s|ned)? to|planning to|"
    r"intend(?:s|ed)? to|aim(?:s|ed)? to|want(?:s|ed)? to|seeking|aspiring|hope(?:s|d)? to)\b"
)
_NEGATED = re.compile(r"\b(?:not|never|no|without|lack(?:s|ed)?|neither)\b")
_WEAK = re.compile(r"\b(?:help(?:s|ed)?|assist(?:s|ed)?|support(?:s|ed)?|contribut(?:e|es|ed)|"
                   r"participat(?:e|es|ed)|under (?:supervision|guidance))\b")
_NONASSERTING = re.compile(r"\b(?:do not (?:assume|infer)(?: that)?|"
                          r"does not (?:mean|show|prove|establish)(?: that)?)\s+(?:you|i|the candidate)\b")
_IMPLICIT_PAST = re.compile(r"^(?:[-*]\s*)?(?:led|managed|owned|built|developed|implemented|"
    r"completed|delivered|launched|shipped|deployed|achieved|created|designed|automated|"
    r"graduated|earned|supervised|spearheaded|mentored)\b")
_METHOD_QUESTION = re.compile(r"^(?:how|what|which|when|why|where)\b.*\b"
                              r"(?:do|does|would|could|should|can|might|will)\s+(?:you|the candidate)\b")
_METHOD_VERBS = frozenset({"lead", "manage", "own", "build", "develop", "implement", "complete",
    "deliver", "launch", "ship", "deploy", "achieve", "create", "design", "automate", "finish",
    "improve", "enhance", "reduce", "increase", "save", "optimize", "resolve"})
_OTHER_ACTOR = re.compile(r"\b(?:(?:my|your|the|our)\s+(?:manager|supervisor|mentor|colleague|employer)|he|she|they)\b")
_CURRENT = re.compile(r"\b(?:currently|presently|now|works|am|are|is)\b")
_PAST = re.compile(r"\b(?:previously|formerly|worked|was|were)\b")
_TECHNICAL_SUBJECT = re.compile(r"\b(?:the|a|an|this|that)\s+(?:service|system|worker|pipeline|server|"
                                r"database|application|tool|model|api|query|code|index|cache|process)\b")
_BIO_FRAGMENT = re.compile(r"\b(?:degree|diploma|university|college|ph\.?d|b\.?tech|m\.?tech|"
    r"b\.?sc|m\.?sc|mba|certified|certification|certificate|licensed)\b|"
    r"^\s*(?:employment|employer|experience|education|qualifications?|credentials?)\s*:")
_ROLE_REQUIREMENT = re.compile(r"^\s*(?:(?:the|this|a|an)\s+)?(?:job|role|position|employer|posting)\b"
                               r".*\b(?:requires?|expects?|prefers?|needs?|requirements?|qualifications?)\b")
_NEUTRAL_INSTRUCTION = re.compile(r"^\s*(?:compare|explain|discuss|evaluate|review|check|verify|"
    r"consider|learn|study|practice|plan|outline|assess|explore|read|earn|obtain|pursue)\b")
_FRAGMENT_GOAL = re.compile(r"\b(?:preparation|planning|comparison|research|curriculum|requirements)\b")
_SHARED_OBJECT_LINK = re.compile(r"^\s*and(?:\s+then)?\s*$")
_UNCERTAIN = re.compile(r"\b(?:or|perhaps|possibly|maybe)\b")
_ACTION_ALIASES = {
    **dict.fromkeys(("lead", "leads", "led", "leadership", "head", "headed", "spearheaded"), "lead"),
    **dict.fromkeys(("manage", "manages", "managed", "management", "directed", "oversaw", "oversee", "supervise", "supervised"), "manage"),
    **dict.fromkeys(("mentor", "mentored"), "mentor"),
    **dict.fromkeys(("own", "owns", "owned", "owner", "ownership"), "own"),
    **dict.fromkeys(("responsible for", "accountable"), "responsible"),
    **dict.fromkeys(("build", "builds", "built", "develop", "develops", "developed", "create", "created", "implement", "implemented"), "build"),
    **dict.fromkeys(("design", "designed"), "design"),
    **dict.fromkeys(("deploy", "deployed"), "deploy"),
    **dict.fromkeys(("launch", "launched"), "launch"),
    **dict.fromkeys(("ship", "shipped"), "ship"),
    **dict.fromkeys(("deliver", "delivered"), "deliver"),
    **dict.fromkeys(("complete", "completed", "finish", "finished"), "complete"),
    **dict.fromkeys(("automate", "automated"), "automate"),
    **dict.fromkeys(("achieve", "achieved"), "achieve"),
    **dict.fromkeys(("reduce", "reduced", "cut"), "reduce"),
    **dict.fromkeys(("improve", "improved", "enhance", "enhanced"), "improve"),
    **dict.fromkeys(("increase", "increased"), "increase"),
    **dict.fromkeys(("save", "saved"), "save"),
    **dict.fromkeys(("optimize", "optimized"), "optimize"),
    **dict.fromkeys(("resolve", "resolved"), "resolve"),
    **dict.fromkeys(("certified", "certification", "certificate"), "certification"),
    **dict.fromkeys(("licensed", "licence", "license"), "license"),
    **dict.fromkeys(("experience", "experienced"), "experience"),
    **dict.fromkeys(("skill", "skills"), "skill"),
}
_RELATIONS = {
    "leadership": re.compile(r"\b(?:lead|leads|led|leadership|manage|manages|managed|management|"
        r"supervise|supervised|oversaw|oversee|headed|head|directed|spearheaded|mentored)\b"),
    "ownership": re.compile(r"\b(?:own|owns|owned|owner|ownership|accountable|responsible for)\b"),
    "completion": re.compile(r"\b(?:build|builds|built|develop|develops|developed|implement|implemented|"
        r"complete|completed|deliver|delivered|launch|launched|ship|shipped|deploy|deployed|"
        r"achieve|achieved|create|created|design|designed|automate|automated|finish|finished)\b"),
    "outcome": re.compile(r"\b(?:improve|improved|enhance|enhanced|reduce|reduced|cut|increase|increased|save|saved|"
                          r"optimize|optimized|resolve|resolved)\b"),
    "seniority": re.compile(r"\b(?:senior|principal|staff|director|executive|seniority)\b"),
    "tenure": re.compile(r"\b\d+(?:\.\d+)?\s*\+?\s*(?:years?|yrs?|months?)\b"),
    "education": re.compile(r"\b(?:earned|graduated|bachelor'?s?|master'?s?|doctorate|ph\.?d|"
        r"degree|diploma|b\.?tech|m\.?tech|b\.?sc|m\.?sc|mba)\b"),
    "credential": re.compile(r"\b(?:certified|certification|certificate|licensed|licence|license|credential)\b"),
    "employment": re.compile(r"\b(?:employed|employment|works? (?:at|for)|worked (?:at|for)|"
        r"engineer|developer|analyst|manager|consultant|designer|scientist|architect)\b"),
    "experience": re.compile(r"\b(?:experience|experienced|expertise|knowledge|familiarity|proficient|skills?)\b"),
}
_TOKENS = re.compile(r"[a-z0-9]+(?:[+#./-][a-z0-9+#]+)*")
_STOP = set("""a an the and or as at by for from in into of on to with without over under
    you your i my me we our they their candidate applicant this that these those
    how what why when where which whether explain describe discuss compare
    have has had am is are was were been being be do does did it its currently
    previously personally directly successfully approved verified documented relevant
    practical technical reliable scalable strong effective useful demonstrated
    use uses using used work works worked working past prior history background
    development implementation delivery creation design engineering years year yrs yr
    months month more than least about approximately up held holds hold earned
    graduated completed build builds built develop develops developed implement implemented
    complete deliver delivered launch launched ship shipped deploy deployed achieve
    achieved create created designed automate automated finish finished seniority
    lead leads led leadership manage manages managed management supervise supervised
    oversaw oversee headed head directed spearheaded mentored own owns owned owner
    ownership accountable responsible help helps helped assist assists assisted support
    supports supported contribute contributes contributed participate participated
    experience experienced expertise knowledge familiarity proficient skill skills
    employed employment certified certification certificate licensed licence license credential
    improve improved reduce reduced cut increase increased save saved optimize optimized resolve resolved
    enhance enhanced
    not never no lack lacks lacked neither can could would should might may will
    if unless whether assuming suppose provided plan plans planned planning intend
    intends intended aim aims aimed want wants wanted seeking aspiring hope hopes hoped
""".split())
_ALIASES = {"apis": "api", "services": "service", "platforms": "platform", "teams": "team",
            "projects": "project", "systems": "system", "applications": "application"}


def _anchors(scope: str, original: str) -> set[str]:
    from .market.skill_taxonomy import canonical_skill

    # Preserve complete technical aliases and legitimate acronym plurals.
    # 'AWS' is not the plural of 'AW'; only a lowercase trailing s is plural.
    acronyms = {token.casefold(): token[:-1].casefold() if token.endswith("s") else token.casefold()
                for token in re.findall(r"\b[A-Z]{2,}s?\b", original)}
    return {_ALIASES.get(token, acronyms.get(token, canonical_skill(token).casefold()))
            for token in _TOKENS.findall(scope)} - _STOP


def _normalized(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold().replace("’", "'")
    for short, full in (("you've", "you have"), ("i've", "i have"),
                        ("you're", "you are"), ("i'm", "i am"),
                        ("haven't", "have not"), ("hasn't", "has not"),
                        ("didn't", "did not"), ("don't", "do not"), ("doesn't", "does not")):
        value = re.sub(r"\b" + re.escape(short) + r"\b", full, value)
    return " ".join(value.split())


@dataclass(frozen=True)
class Claim:
    family: str
    predicate: str
    anchors: frozenset[str]
    numbers: tuple[str, ...]
    negative: bool
    weakened: bool
    employment_period: str


def _claims(value: str, *, personal_only: bool, support: bool = False) -> list[Claim]:
    result = []
    for sentence in _SENTENCE.split(value):
        text = _normalized(sentence)
        method_question = bool(_METHOD_QUESTION.search(text))
        if support and method_question:
            continue
        if support and (_CONDITIONAL.search(text) or _FUTURE.search(text) or _UNCERTAIN.search(text)):
            continue
        matches = sorted((match.start(), match.end(), family)
                         for family, pattern in _RELATIONS.items()
                         for match in pattern.finditer(text))
        for index, (start, end, family) in enumerate(matches):
            if personal_only and method_question and text[start:end] in _METHOD_VERBS:
                continue
            if support and start == 0 and text[start:end] in _METHOD_VERBS:
                # Imperative project tasks are not completed achievements.
                continue
            # A new predicate starts a local object scope. Repeated degree or
            # experience nouns in one predicate must not hide its object.
            later = [item[0] for item in matches[index + 1:]
                     if item[0] >= end and item[2] in {"leadership", "ownership", "completion", "employment", "outcome"}]
            stop = min(later) if later else len(text)
            if later:
                boundaries = list(re.finditer(r"\b(?:and|but|while|however|whereas|then)\b|[,;]",
                                              text[end:stop]))
                if boundaries:
                    stop = end + boundaries[-1].start()
            prior = [item for item in matches[:index]
                     if item[2] in {"leadership", "ownership", "completion", "employment", "outcome"}]
            begin = prior[-1][1] if prior else 0
            prefix = text[begin:start]
            people = list(_PERSON.finditer(text[:start]))
            fragments = (_BIO_FRAGMENT.search(text) or family == "tenure" and "experience" in text)
            if personal_only and not people and not _IMPLICIT_PAST.search(text) and not (
                    fragments and not _NEUTRAL_INSTRUCTION.search(text) and not _FRAGMENT_GOAL.search(text)):
                continue
            subject_start = people[-1].start() if people else 0
            actor_prefix = text[subject_start:start]
            independent = re.split(r"\b(?:and|but|while|however|whereas)\b|[,;]", prefix)[-1]
            attributed = re.split(r"\b(?:and|but|while|however|whereas)\b|[,;]", text[:start])[-1]
            if _ROLE_REQUIREMENT.search(attributed):
                continue
            if (personal_only or support) and _TECHNICAL_SUBJECT.search(independent):
                continue
            # A condition or future goal does not establish a completed fact.
            # Stop its scope at an independent clause: an earlier unrelated 'if'
            # cannot excuse a later categorical assertion.
            governing_start = max(text.rfind(",", 0, subject_start), text.rfind(";", 0, subject_start)) + 1
            governing = text[governing_start:start]
            local_prefix = re.split(r"\b(?:but|however|whereas)\b|[,;]", prefix)[-1]
            if (_CONDITIONAL.search(governing) or _FUTURE.search(governing)
                    or _NONASSERTING.search(governing)):
                continue
            if support and _OTHER_ACTOR.search(actor_prefix):
                continue
            if prior and family != "outcome":
                separators = list(re.finditer(r"\b(?:and|but|while|however|whereas|then)\b|[,;]", prefix))
                if separators:
                    begin += separators[-1].end()
            if family == "experience":
                # Separate an experience noun phrase from the advice/inference
                # which follows it: 'Your Python experience fits this role'.
                following = re.search(r"\b(?:fits?|matches?|means?|shows?|makes?|helps?|supports?|"
                                      r"demonstrates?|is|can|will|would)\b", text[end:stop])
                if following:
                    stop = end + following.start()
            scope = text[begin:stop]
            tokens = _anchors(scope, sentence)
            numbers = tuple(sorted(re.findall(r"\b\d+(?:\.\d+)?\b", scope)))
            if not tokens and later:
                next_start, next_end, _ = next(item for item in matches if item[0] == min(later))
                if _SHARED_OBJECT_LINK.fullmatch(text[end:next_start]):
                    following_predicates = [item[0] for item in matches if item[0] >= next_end
                                            and item[2] in {"leadership", "ownership", "completion", "employment", "outcome"}]
                    shared_end = min(following_predicates) if following_predicates else len(text)
                    shared = text[next_end:shared_end]
                    tokens = _anchors(shared, sentence)
                    numbers = tuple(sorted(re.findall(r"\b\d+(?:\.\d+)?\b", shared)))
            tokens -= set(numbers)
            if family == "tenure":
                tokens.add("month" if "month" in text[start:end] else "year")
            predicate = ("employment" if family == "employment" else "tenure" if family == "tenure"
                         else _ACTION_ALIASES.get(text[start:end], text[start:end]))
            result.append(Claim(family, predicate, frozenset(tokens), numbers,
                                bool(_NEGATED.search(local_prefix)),
                                bool(_WEAK.search(scope)) or family in {"leadership", "ownership"}
                                and bool(re.search(r"\b(?:we|our team)\b", actor_prefix)),
                                ("past" if _PAST.search(prefix + " " + text[start:end]) else
                                 "current" if _CURRENT.search(prefix + " " + text[start:end]) else
                                 "unspecified") if family == "employment" else "unspecified"))
    return result


def has_personal_assertion(value: str) -> bool:
    """Interview coaching must not assert these personal facts at all."""
    return bool(_claims(value, personal_only=True))


def require_supported_claims(value: str, approved_facts: list[dict], *, original: str | None = None) -> None:
    """Require each explicit claim to have one local, genuinely cited support.

    Original text supports a rewrite, never unrelated generated guidance. Only
    approved fact *text* can establish work/history; skill tags and job data do
    not establish leadership, tenure, employment or qualifications.
    """
    supports = [str(item.get("text") or "") for item in approved_facts]
    if original is not None:
        supports.append(original)
    available = [claim for text in supports for claim in _claims(text, personal_only=False, support=True)]
    for claim in _claims(value, personal_only=original is None):
        if not any((claim.family == source.family and claim.predicate == source.predicate
                    or claim.family == "experience" and claim.predicate in {"experience", "skill"}
                    and source.family in {"completion", "employment", "leadership", "ownership"})
                   and claim.anchors <= source.anchors
                   and not (Counter(claim.numbers) - Counter(source.numbers))
                   and claim.negative == source.negative
                   and (claim.employment_period == "unspecified"
                        or claim.employment_period == source.employment_period)
                   and (claim.weakened or not source.weakened) for source in available):
            raise UnsupportedPersonalClaim()
