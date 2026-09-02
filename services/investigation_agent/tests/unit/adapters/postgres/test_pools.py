from investigation_agent.adapters.postgres.pools import (
    PoolBounds,
    create_reader_pool,
    create_writer_pool,
)
from psycopg import AsyncRawCursor
from psycopg.rows import dict_row


def test_reader_and_writer_pools_have_distinct_bounded_settings() -> None:
    bounds = PoolBounds(min_size=1, max_size=3, acquisition_timeout_s=2.5, max_waiting=7)

    reader = create_reader_pool(
        dsn="postgresql+psycopg://agent_reader:secret@postgres/app",
        bounds=bounds,
    )
    writer = create_writer_pool(
        dsn="postgresql+psycopg://agent_writer:secret@postgres/app",
        bounds=bounds,
    )

    assert isinstance(reader.conninfo, str) and reader.conninfo.startswith(
        "postgresql://agent_reader:"
    )
    assert isinstance(writer.conninfo, str) and writer.conninfo.startswith(
        "postgresql://agent_writer:"
    )
    assert (reader.min_size, reader.max_size, reader.timeout, reader.max_waiting) == (1, 3, 2.5, 7)
    assert (writer.min_size, writer.max_size, writer.timeout, writer.max_waiting) == (1, 3, 2.5, 7)
    assert reader.kwargs == {
        "autocommit": False,
        "row_factory": dict_row,
        "cursor_factory": AsyncRawCursor,
        "prepare_threshold": 0,
        "options": "-c search_path=pg_catalog,public",
    }
    assert writer.kwargs == {
        "autocommit": True,
        "row_factory": dict_row,
        "prepare_threshold": 0,
        "options": "-c search_path=agent_runtime,pg_catalog",
    }
    assert reader.closed and writer.closed
