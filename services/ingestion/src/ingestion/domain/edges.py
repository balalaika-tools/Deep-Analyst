"""Deterministic entities and confirmed relationships from explicit structured fields."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from evidence_model import (
    EndpointRef,
    EntityDraft,
    EntityType,
    ExtractionMethod,
    FieldLocator,
    Predicate,
    RelationshipDraft,
    RelationshipStatus,
    SourceRef,
    TextSpanLocator,
)

from ingestion.domain.identifiers import IdentifierSpan
from ingestion.domain.records import (
    AccountProjection,
    CommunicationProjection,
    TransactionProjection,
)

_ACTOR_TYPES = {"person": EntityType.PERSON, "organization": EntityType.ORGANIZATION}


@dataclass(slots=True)
class RuleOutput:
    entities: list[EntityDraft] = field(default_factory=list)
    relationships: list[RelationshipDraft] = field(default_factory=list)

    def extend(self, other: RuleOutput) -> None:
        self.entities.extend(other.entities)
        self.relationships.extend(other.relationships)


def _field_ref(record_id: str, field_name: str) -> SourceRef:
    return SourceRef(record_id=record_id, locator=FieldLocator(field=field_name))


def _keyed(
    case_id: str, entity_type: EntityType, key: str, ref: SourceRef, *, label: str | None = None
) -> EntityDraft:
    return EntityDraft(
        case_id=case_id,
        entity_type=entity_type,
        label=label or key,
        normalized_key=key,
        source_refs=[ref],
    )


def _confirmed(
    case_id: str,
    subject: EntityDraft,
    predicate: Predicate,
    obj: EntityDraft,
    *,
    record_id: str,
    refs: list[SourceRef],
    occurred_at: datetime | None = None,
    attributes: dict[str, object] | None = None,
) -> RelationshipDraft:
    return RelationshipDraft(
        case_id=case_id,
        subject=subject.endpoint(),
        predicate=predicate,
        object=obj.endpoint(),
        status=RelationshipStatus.CONFIRMED,
        method=ExtractionMethod.DETERMINISTIC,
        source_record_id=record_id,
        occurred_at=occurred_at,
        source_refs=refs,
        attributes=dict(attributes or {}),
    )


def communication_edges(comm: CommunicationProjection) -> RuleOutput:
    """`COMMUNICATED_WITH` between the two normalized endpoints, plus the device if known."""
    endpoint_type = EntityType.EMAIL_ADDRESS if comm.channel == "email" else EntityType.PHONE
    sender = _keyed(
        comm.case_id, endpoint_type, comm.from_endpoint, _field_ref(comm.record_id, comm.from_field)
    )
    receiver = _keyed(
        comm.case_id, endpoint_type, comm.to_endpoint, _field_ref(comm.record_id, comm.to_field)
    )
    output = RuleOutput(entities=[sender, receiver])
    output.relationships.append(
        _confirmed(
            comm.case_id,
            sender,
            Predicate.COMMUNICATED_WITH,
            receiver,
            record_id=comm.record_id,
            refs=[sender.source_refs[0], receiver.source_refs[0]],
            occurred_at=comm.event_time_utc,
            attributes={"channel": comm.channel, "direction": comm.direction},
        )
    )
    if comm.device_id:
        output.entities.append(
            _keyed(
                comm.case_id, EntityType.DEVICE, comm.device_id, _field_ref(comm.record_id, "imei")
            )
        )
    return output


def account_edges(account: AccountProjection) -> RuleOutput:
    """The account as an asset and, when the row names a holder, a confirmed `HELD_BY`."""
    account_entity = _keyed(
        account.case_id,
        EntityType.FINANCIAL_ACCOUNT,
        account.iban,
        _field_ref(account.record_id, "iban"),
    )
    output = RuleOutput(entities=[account_entity])
    holder_type = _ACTOR_TYPES.get(account.holder_type or "")
    if not account.holder_name or holder_type is None:
        return output
    holder = EntityDraft(
        case_id=account.case_id,
        entity_type=holder_type,
        label=account.holder_name,
        scope_record_id=account.record_id,
        source_refs=[_field_ref(account.record_id, "holder_name")],
    )
    output.entities.append(holder)
    output.relationships.append(
        _confirmed(
            account.case_id,
            account_entity,
            Predicate.HELD_BY,
            holder,
            record_id=account.record_id,
            refs=[_field_ref(account.record_id, "holder_name")],
            attributes={"account_id": account.account_id},
        )
    )
    return output


def transaction_edges(txn: TransactionProjection, references: list[IdentifierSpan]) -> RuleOutput:
    """`TRANSFERRED_TO` between accounts and `REFERENCES` for each invoice in the remittance."""
    debtor = _keyed(
        txn.case_id,
        EntityType.FINANCIAL_ACCOUNT,
        txn.debtor_iban,
        _field_ref(txn.record_id, "debtor_iban"),
    )
    creditor = _keyed(
        txn.case_id,
        EntityType.FINANCIAL_ACCOUNT,
        txn.creditor_iban,
        _field_ref(txn.record_id, "creditor_iban"),
    )
    transaction = _keyed(
        txn.case_id, EntityType.TRANSACTION, txn.txn_id, _field_ref(txn.record_id, "txn_id")
    )
    output = RuleOutput(entities=[debtor, creditor, transaction])
    output.relationships.append(
        _confirmed(
            txn.case_id,
            debtor,
            Predicate.TRANSFERRED_TO,
            creditor,
            record_id=txn.record_id,
            refs=[debtor.source_refs[0], creditor.source_refs[0]],
            occurred_at=txn.booking_ts_utc,
            attributes={
                "txn_id": txn.txn_id,
                "amount_minor": txn.amount_minor,
                "currency": txn.currency,
            },
        )
    )
    for span in references:
        if span.entity_type is not EntityType.INVOICE_REF or txn.remittance_info is None:
            continue
        ref = SourceRef(
            record_id=txn.record_id,
            locator=TextSpanLocator(
                field="remittance_info",
                char_start=span.char_start,
                char_end=span.char_end,
                quote=span.raw,
            ),
        )
        invoice = _keyed(txn.case_id, EntityType.INVOICE_REF, span.normalized_key, ref)
        output.entities.append(invoice)
        output.relationships.append(
            _confirmed(
                txn.case_id,
                transaction,
                Predicate.REFERENCES,
                invoice,
                record_id=txn.record_id,
                refs=[ref],
                occurred_at=txn.booking_ts_utc,
            )
        )
    return output


def identifier_entities(
    case_id: str, record_id: str, spans: list[IdentifierSpan], *, field_name: str = "text"
) -> list[EntityDraft]:
    """Typed identifier entities for every rule match in prose, with text-span evidence."""
    return [
        _keyed(
            case_id,
            span.entity_type,
            span.normalized_key,
            SourceRef(
                record_id=record_id,
                locator=TextSpanLocator(
                    field=field_name,
                    char_start=span.char_start,
                    char_end=span.char_end,
                    quote=span.raw,
                ),
            ),
            label=span.raw,
        )
        for span in spans
    ]


def endpoint_of(entity: EntityDraft) -> EndpointRef:
    return entity.endpoint()
