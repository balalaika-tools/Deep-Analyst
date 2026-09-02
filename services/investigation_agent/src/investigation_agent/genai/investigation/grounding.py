"""Deterministic citation verification for a private ``AnswerDraft``."""

from __future__ import annotations

from investigation_agent.domain.history import Citation
from investigation_agent.domain.investigation_state import EvidenceCard, InvestigationState
from investigation_agent.genai.investigation.schemas import (
    AnswerClaim,
    AnswerDraft,
    ClaimKind,
    GroundingVerdict,
    VerifiedAnswer,
)

_ABSENCE_PHRASES = (
    "does not exist",
    "did not happen",
    "never occurred",
    "no evidence exists",
    "there is no record",
    "is absent",
    "never took place",
)


class GroundingValidationError(ValueError):
    """The draft failed a deterministic or entailment check; nothing is exposed."""

    def __init__(self, violations: tuple[str, ...]) -> None:
        self.violations = violations
        super().__init__("; ".join(violations))


def deterministic_violations(
    draft: AnswerDraft,
    state: InvestigationState,
    *,
    max_answer_chars: int,
    coverage_incomplete: bool,
) -> tuple[str, ...]:
    """Check identifiers, provenance, status qualification, size, and absence claims."""

    violations: list[str] = []
    cards = state.evidence.cards
    if len(draft.answer) > max_answer_chars:
        violations.append("answer_too_long")
    cited = {item for claim in draft.claims for item in claim.evidence_ids}
    if unknown := sorted(item for item in cited if item not in cards):
        violations.append(f"unknown_evidence_ids:{','.join(unknown)}")
    known = [cards[item] for item in sorted(cited) if item in cards]
    if any(not card.source_refs or not card.content_hash for card in known):
        violations.append("missing_provenance")
    for claim in draft.claims:
        if not claim.material:
            continue
        if claim.kind is ClaimKind.VERIFIED and any(
            cards[item].evidentiary_status == "proposed"
            for item in claim.evidence_ids
            if item in cards
        ):
            violations.append(f"unqualified_proposed:{claim.claim_id}")
        if claim.kind is not ClaimKind.LIMITATION and not claim.evidence_ids:
            violations.append(f"uncited_material_claim:{claim.claim_id}")
    if coverage_incomplete and _claims_absence(draft):
        violations.append("absence_claim_from_incomplete_coverage")
    if not draft.claims and _looks_factual(draft.answer):
        violations.append("no_material_claims")
    return tuple(dict.fromkeys(violations))


def verify_answer_draft(
    draft: AnswerDraft,
    state: InvestigationState,
    *,
    verdict: GroundingVerdict | None,
    max_answer_chars: int,
    coverage_incomplete: bool = False,
) -> VerifiedAnswer:
    """Both layers must pass: deterministic checks and the bounded entailment verdict."""

    violations = list(
        deterministic_violations(
            draft,
            state,
            max_answer_chars=max_answer_chars,
            coverage_incomplete=coverage_incomplete,
        )
    )
    material = [
        claim for claim in draft.claims if claim.material and claim.kind is not ClaimKind.LIMITATION
    ]
    if material:
        if verdict is None:
            violations.append("verifier_unavailable")
        else:
            violations.extend(_entailment_violations(material, verdict))
    if violations:
        raise GroundingValidationError(tuple(dict.fromkeys(violations)))
    return VerifiedAnswer(answer=draft.answer, citations=citations_for(draft, state))


def citations_for(draft: AnswerDraft, state: InvestigationState) -> tuple[Citation, ...]:
    cited = sorted({item for claim in draft.claims for item in claim.evidence_ids})
    citations: list[Citation] = []
    for evidence_id in cited:
        card = state.evidence.cards.get(evidence_id)
        if card is None:
            continue
        citations.extend(_card_citations(card))
    return tuple(citations)


def _card_citations(card: EvidenceCard) -> list[Citation]:
    return [
        Citation(evidence_id=card.evidence_id, content_hash=card.content_hash, source_ref=ref)
        for ref in card.source_refs
    ]


def _entailment_violations(material: list[AnswerClaim], verdict: GroundingVerdict) -> list[str]:
    verdicts = {item.claim_id: item for item in verdict.claims}
    expected = {claim.claim_id for claim in material}
    if set(verdicts) != expected or len(verdict.claims) != len(verdicts):
        return ["malformed_verifier_output"]
    return [
        f"unsupported_claims:{item.claim_id}"
        for item in verdict.claims
        if not item.supported or item.safe_reason_code != "entailed"
    ]


def _claims_absence(draft: AnswerDraft) -> bool:
    text = " ".join((draft.answer, *(claim.text for claim in draft.claims))).lower()
    return any(phrase in text for phrase in _ABSENCE_PHRASES)


def _looks_factual(answer: str) -> bool:
    lowered = answer.lower()
    limitation_markers = ("could not", "no supporting evidence", "not retrieved", "unable to")
    return not any(marker in lowered for marker in limitation_markers)


__all__ = [
    "GroundingValidationError",
    "citations_for",
    "deterministic_violations",
    "verify_answer_draft",
]
