from datetime import UTC, datetime

from evidence_model import FieldLocator, SourceRef
from investigation_agent.domain.history import (
    Citation,
    HistoryRole,
    HistoryState,
    ThreadFullError,
    TurnStatus,
    append_assistant_message,
    append_user_message,
)

NOW = datetime(2026, 9, 2, tzinfo=UTC)


def test_history_keeps_exact_pii_and_idempotent_user_message() -> None:
    history = append_user_message(
        HistoryState(),
        message_id="message-1",
        turn_id="turn-1",
        request_id="request-1",
        content="Call +30 210 555 0101 about account GR123",
        created_at=NOW,
        max_turns=2,
    )

    replay = append_user_message(
        history,
        message_id="different-id",
        turn_id="turn-1",
        request_id="request-1",
        content="different",
        created_at=NOW,
        max_turns=2,
    )

    assert replay is history
    assert history.messages[0].content == "Call +30 210 555 0101 about account GR123"


def test_assistant_commit_is_exact_and_updates_the_turn_status() -> None:
    history = append_user_message(
        HistoryState(),
        message_id="user-1",
        turn_id="turn-1",
        request_id="request-1",
        content="What happened?",
        created_at=NOW,
        max_turns=2,
    )
    citation = Citation(
        evidence_id="ev-1",
        content_hash="a" * 64,
        source_ref=SourceRef(record_id="record-1", locator=FieldLocator(field="amount_minor")),
    )

    committed = append_assistant_message(
        history,
        message_id="assistant-1",
        turn_id="turn-1",
        request_id="request-1",
        content="The payment was recorded. [ev-1]",
        citations=(citation,),
        created_at=NOW,
    )

    assert [message.role for message in committed.messages] == [
        HistoryRole.USER,
        HistoryRole.ASSISTANT,
    ]
    assert {message.turn_status for message in committed.messages} == {TurnStatus.COMPLETED}
    assert committed.messages[-1].content == "The payment was recorded. [ev-1]"


def test_new_turn_is_rejected_at_the_configured_history_bound() -> None:
    history = append_user_message(
        HistoryState(),
        message_id="message-1",
        turn_id="turn-1",
        request_id="request-1",
        content="first",
        created_at=NOW,
        max_turns=1,
    )

    try:
        append_user_message(
            history,
            message_id="message-2",
            turn_id="turn-2",
            request_id="request-2",
            content="second",
            created_at=NOW,
            max_turns=1,
        )
    except ThreadFullError as exc:
        assert exc.code == "thread_full"
    else:
        raise AssertionError("a full thread accepted another turn")
