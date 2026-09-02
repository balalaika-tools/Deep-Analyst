"""Service telemetry vocabulary: run span names, the two ingestion counters, log events.

Business decisions stay in the application; this module only names things and owns
the instruments so labels cannot drift between call sites.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Final

from opentelemetry.metrics import Counter as OtelCounter
from opentelemetry.metrics import Meter

JOB_NAME: Final = "ingestion"
SPAN_RUN: Final = "run ingestion"
SPAN_RECORD: Final = "ingest record"
SPAN_EXTRACT: Final = "invoke_workflow extract_chunk"
SPAN_FINALIZE: Final = "finalize ingestion"
SPAN_PERSIST_CHUNKS: Final = "persist chunks"
SPAN_PERSIST_GRAPH: Final = "persist graph"


def source_span_name(source_system: str) -> str:
    return f"load {source_system}"


class LogEvent:
    RUN_STARTED: Final = "ingestion.run_started"
    RUN_SKIPPED: Final = "ingestion.run_skipped"
    SOURCE_LOADED: Final = "ingestion.source_loaded"
    CANDIDATE_REJECTED: Final = "ingestion.candidate_rejected"
    RUN_COMPLETED: Final = "ingestion.run_completed"
    RUN_FAILED: Final = "ingestion.run_failed"


class Outcome:
    SUCCESS: Final = "success"
    SKIPPED: Final = "skipped"
    ERROR: Final = "error"


@dataclass(slots=True)
class IngestionInstruments:
    candidates: OtelCounter
    chunks_indexed: OtelCounter

    @classmethod
    def create(cls, meter: Meter) -> IngestionInstruments:
        return cls(
            candidates=meter.create_counter(
                "app.ingestion.candidates",
                unit="{candidate}",
                description="Model candidates by kind (entity, relationship) and validation outcome.",
            ),
            chunks_indexed=meter.create_counter(
                "app.ingestion.chunks_indexed",
                unit="{chunk}",
                description="Chunks embedded and stored, by source system.",
            ),
        )

    def record_candidates(self, kind: str, counts: Counter[str]) -> None:
        for outcome, count in counts.items():
            self.candidates.add(count, {"kind": kind, "outcome": outcome})

    def record_chunks(self, source_system: str, count: int) -> None:
        if count:
            self.chunks_indexed.add(count, {"source_system": source_system})
