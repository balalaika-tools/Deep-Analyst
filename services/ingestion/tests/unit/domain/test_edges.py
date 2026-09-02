from datetime import UTC, date, datetime

from evidence_model import (
    EntityType,
    ExtractionMethod,
    Predicate,
    RelationshipDraft,
    RelationshipStatus,
)
from ingestion.domain.edges import (
    account_edges,
    communication_edges,
    identifier_entities,
    transaction_edges,
)
from ingestion.domain.identifiers import find_identifiers
from ingestion.domain.records import (
    AccountProjection,
    CommunicationProjection,
    TransactionProjection,
)


def _confirmed(relationships: list[RelationshipDraft]) -> None:
    for relationship in relationships:
        assert relationship.status is RelationshipStatus.CONFIRMED
        assert relationship.method is ExtractionMethod.DETERMINISTIC
        assert all(ref.locator.kind == "field" for ref in relationship.source_refs)


def test_cdr_row_yields_phone_entities_a_device_and_communicated_with() -> None:
    comm = CommunicationProjection(
        record_id="cdr:c01",
        channel="sms",
        direction="out",
        from_endpoint="306971234567",
        to_endpoint="306949876543",
        from_field="calling_msisdn",
        to_field="called_msisdn",
        event_time_utc=datetime(2026, 3, 4, 21, 14, tzinfo=UTC),
        original_time="2026-03-04T23:14:00+02:00",
        device_id="356923107744818",
    )
    output = communication_edges(comm)

    assert [(e.entity_type, e.normalized_key) for e in output.entities] == [
        (EntityType.PHONE, "306971234567"),
        (EntityType.PHONE, "306949876543"),
        (EntityType.DEVICE, "356923107744818"),
    ]
    (edge,) = output.relationships
    assert edge.predicate is Predicate.COMMUNICATED_WITH
    assert (edge.subject.entity_id, edge.object.entity_id) == (
        "PHONE:306971234567",
        "PHONE:306949876543",
    )
    assert edge.occurred_at == comm.event_time_utc
    assert [ref.locator.field for ref in edge.source_refs] == ["calling_msisdn", "called_msisdn"]
    _confirmed(list(output.relationships))


def test_email_yields_email_address_endpoints() -> None:
    comm = CommunicationProjection(
        record_id="email:eM1",
        channel="email",
        direction="out",
        from_endpoint="alex@meridian-consulting.example",
        to_endpoint="k.rossi@aegeantrade.example",
        from_field="from",
        to_field="to",
        event_time_utc=datetime(2026, 3, 4, 16, 40, 11, tzinfo=UTC),
        original_time="Wed, 4 Mar 2026 18:40:11 +0200",
    )
    output = communication_edges(comm)
    assert {e.entity_type for e in output.entities} == {EntityType.EMAIL_ADDRESS}
    assert output.relationships[0].subject.entity_type is EntityType.EMAIL_ADDRESS


def test_account_row_yields_held_by_with_a_record_scoped_holder() -> None:
    account = AccountProjection(
        record_id="bank:acct_pa",
        account_id="acct_pa",
        iban="GR8001100010000000000017719",
        holder_name="Alexandros Mavridis",
        holder_type="person",
        bic="TRGSGR2A",
        opened_date=date(2021, 4, 12),
    )
    output = account_edges(account)

    account_entity, holder = output.entities
    assert account_entity.entity_id == "FINANCIAL_ACCOUNT:GR8001100010000000000017719"
    assert holder.entity_type is EntityType.PERSON and holder.scope_record_id == "bank:acct_pa"
    (edge,) = output.relationships
    assert edge.predicate is Predicate.HELD_BY
    assert (edge.subject.entity_id, edge.object.entity_id) == (
        account_entity.entity_id,
        holder.entity_id,
    )
    _confirmed(list(output.relationships))


def test_transaction_row_yields_transferred_to_and_references() -> None:
    txn = TransactionProjection(
        record_id="bank:t_88",
        txn_id="t_88",
        booking_ts_utc=datetime(2026, 3, 5, 14, 30, tzinfo=UTC),
        value_date=date(2026, 3, 5),
        debtor_iban="GR9201100010000000000046118",
        debtor_name="Aegean Trade OE",
        creditor_iban="GR3601100010000000000054401",
        creditor_name="Meridian Consulting Ltd",
        amount_minor=980000,
        amount_text="9800.00",
        currency="EUR",
        status="booked",
        remittance_info="consulting services INV-2231",
    )
    output = transaction_edges(txn, find_identifiers(txn.remittance_info or ""))

    predicates = [
        (r.predicate, r.subject.entity_id, r.object.entity_id) for r in output.relationships
    ]
    assert predicates == [
        (
            Predicate.TRANSFERRED_TO,
            "FINANCIAL_ACCOUNT:GR9201100010000000000046118",
            "FINANCIAL_ACCOUNT:GR3601100010000000000054401",
        ),
        (Predicate.REFERENCES, "TRANSACTION:t_88", "INVOICE_REF:INV-2231"),
    ]
    transfer, reference = output.relationships
    assert transfer.attributes["amount_minor"] == 980000
    assert transfer.status is RelationshipStatus.CONFIRMED
    assert reference.source_refs[0].locator.kind == "text_span"
    assert reference.source_refs[0].locator.quote == "INV-2231"


def test_identifier_entities_carry_text_span_evidence() -> None:
    text = "He uses telephone +30 697 123 4567."
    (entity,) = identifier_entities("docs:R-01", find_identifiers(text))
    assert entity.entity_id == "PHONE:306971234567"
    locator = entity.source_refs[0].locator
    assert locator.kind == "text_span" and locator.matches(text)
