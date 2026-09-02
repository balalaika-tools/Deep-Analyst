"""Build the carrier call-detail-record (CDR) feed."""

import random
from typing import Any

from dataset.core import state
from dataset.core.fixtures import DEVICES, PHONES
from dataset.core.util import _digits


def build_cdr(rng: random.Random) -> list[dict[str, Any]]:
    def row(
        record_id: str,
        record_type: str,
        subscriber: str,
        caller: str,
        called: str,
        imei: str,
        cell_id: str,
        ts_local: str,
        duration_s: Any = "",
        sms_len: Any = "",
    ) -> dict[str, Any]:
        return {
            "record_id": record_id,
            "seq": 0,
            "record_type": record_type,
            "subscriber_msisdn": _digits(PHONES[subscriber]),
            "calling_msisdn": _digits(PHONES[caller]),
            "called_msisdn": _digits(PHONES[called]),
            "imei": imei,
            "cell_id": cell_id,
            "ts_local": ts_local,
            "duration_s": duration_s,
            "sms_len": sms_len,
            "source_version": state.SOURCE_VERSIONS["cdr"],
        }

    rows = [
        row(
            "c01",
            "SMS-MO",
            "pa",
            "pa",
            "pr",
            DEVICES["pa"],
            "AT-20210-4312",
            "2026-03-04T23:14:00+02:00",
            sms_len=58,
        ),
        row(
            "c02",
            "SMS-MT",
            "pa",
            "pr",
            "pa",
            DEVICES["pa"],
            "AT-20210-4312",
            "2026-03-04T23:20:00+02:00",
            sms_len=22,
        ),
        row(
            "c03",
            "MOC",
            "pa",
            "pa",
            "pr",
            DEVICES["pa"],
            "AT-20210-4312",
            "2026-02-25T20:30:00+02:00",
            duration_s=145,
        ),
        row(
            "c04",
            "SMS-MO",
            "pa",
            "pa",
            "pr",
            DEVICES["pa"],
            "MAR-20530-0091",
            "2026-02-26T21:45:00+02:00",
            sms_len=6,
        ),
        row(
            "c05",
            "MOC",
            "pd",
            "pd",
            "pa",
            "",
            "AT-20210-7755",
            "2026-03-01T19:05:00+02:00",
            duration_s=620,
        ),
        row(
            "c06",
            "MOC",
            "pa",
            "pa",
            "pr",
            DEVICES["pa"],
            "AT-20210-4312",
            "2026-03-05T14:10:00+02:00",
            duration_s=60,
        ),
        row(
            "c07",
            "MOC",
            "pa",
            "pa",
            "u1",
            DEVICES["pa"],
            "MAR-20530-0091",
            "2026-03-05T20:40:00+02:00",
            duration_s=95,
        ),
        row(
            "c08",
            "MOC",
            "pr",
            "pr",
            "aegean",
            DEVICES["pr"],
            "AT-20210-1177",
            "2026-03-05T11:02:14+02:00",
            duration_s=312,
        ),
        row(
            "c09",
            "MOC",
            "pr",
            "pr",
            "pa",
            DEVICES["pr"],
            "AT-20210-1177",
            "2026-03-05T16:45:00+02:00",
            duration_s=30,
        ),
        row(
            "c10",
            "SMS-MO",
            "pa",
            "pa",
            "pr",
            DEVICES["pa"],
            "MAR-20530-0091",
            "2026-03-05T21:14:00+02:00",
            sms_len=14,
        ),
        row(
            "c11",
            "MOC",
            "pd",
            "pd",
            "b1",
            "",
            "AT-20210-7755",
            "2026-03-02T10:15:00+02:00",
            duration_s=240,
        ),
        row(
            "c12",
            "SMS-MO",
            "pa",
            "pa",
            "pr",
            DEVICES["pa"],
            "AT-20210-4312",
            "2026-02-24T12:05:00+02:00",
            sms_len=35,
        ),
        row(
            "c13",
            "SMS-MO",
            "pa",
            "pa",
            "pr",
            DEVICES["pa"],
            "AT-20210-4312",
            "2026-02-21T11:05:00+02:00",
            sms_len=78,
        ),
        row(
            "c14",
            "MOC",
            "pa",
            "pa",
            "n2",
            DEVICES["pa"],
            "AT-20210-4312",
            "2026-03-04T15:20:00+02:00",
            duration_s=210,
        ),
    ]

    # c15/c16 are deliberately close candidates for X-N01.  Policy must
    # abstain instead of forcing either match.
    background_specs = [
        ("c15", "SMS-MO", "n1", "n2", "2026-02-23T10:00:00+02:00", "MAR-20530-0091"),
        ("c16", "SMS-MO", "n1", "n2", "2026-02-23T10:01:20+02:00", "AT-20210-5501"),
        ("c17", "MOC", "n3", "n3b", "2026-02-25T18:30:00+02:00", "MAR-20530-0091"),
        ("c18", "MTC", "n2", "n1", "2026-02-20T09:10:00+02:00", "AT-20210-5502"),
        ("c19", "SMS-MO", "n2", "n6", "2026-02-21T13:25:00+02:00", "AT-20210-5503"),
        ("c20", "MOC", "n2", "n3", "2026-02-28T17:15:00+02:00", "AT-20210-5504"),
        ("c21", "MOC", "n1", "b2", "2026-02-26T10:40:00+02:00", "AT-20210-5501"),
        ("c22", "SMS-MO", "n1", "n7", "2026-02-22T19:20:00+02:00", "AT-20210-5502"),
        ("c23", "MOC", "n1", "n8", "2026-03-07T11:05:00+02:00", "AT-20210-5503"),
        ("c24", "SMS-MO", "n3", "n3b", "2026-02-22T18:25:00+02:00", "AT-20210-5504"),
        ("c25", "MOC", "n3b", "n3", "2026-03-01T18:50:00+02:00", "AT-20210-5505"),
        ("c26", "MOC", "n3", "b2", "2026-02-23T12:00:00+02:00", "AT-20210-5506"),
        ("c27", "SMS-MO", "n3b", "n5", "2026-02-27T20:10:00+02:00", "MAR-20530-0091"),
        ("c28", "MOC", "n4", "n7", "2026-02-20T18:45:00+02:00", "AT-20210-5507"),
        ("c29", "SMS-MT", "n7", "n4", "2026-02-20T18:49:00+02:00", "AT-20210-5507"),
        ("c30", "MOC", "n4", "n5", "2026-02-24T16:30:00+02:00", "AT-20210-5508"),
        ("c31", "SMS-MO", "n4", "b3", "2026-03-06T09:15:00+02:00", "AT-20210-5508"),
        ("c32", "MOC", "n5", "n7", "2026-02-21T10:20:00+02:00", "AT-20210-5509"),
        ("c33", "SMS-MO", "n5", "n6", "2026-02-26T12:35:00+02:00", "AT-20210-5509"),
        ("c34", "MTC", "n5", "n3b", "2026-03-04T19:50:00+02:00", "AT-20210-5505"),
        ("c35", "MOC", "n6", "n3", "2026-02-24T12:55:00+02:00", "AT-20210-5510"),
        ("c36", "SMS-MO", "n6", "n8", "2026-03-06T15:10:00+02:00", "AT-20210-5510"),
        ("c37", "MOC", "n6", "b3", "2026-03-10T09:40:00+02:00", "AT-20210-5511"),
        ("c38", "MOC", "n7", "n1", "2026-02-22T19:00:00+02:00", "AT-20210-5512"),
        ("c39", "SMS-MO", "n7", "n5", "2026-03-02T11:20:00+02:00", "AT-20210-5512"),
        ("c40", "MOC", "n7", "b2", "2026-03-08T10:30:00+02:00", "AT-20210-5513"),
        ("c41", "MOC", "n8", "n6", "2026-02-25T14:15:00+02:00", "AT-20210-5514"),
        ("c42", "SMS-MO", "n8", "n4", "2026-03-03T17:35:00+02:00", "AT-20210-5514"),
        ("c43", "MOC", "n8", "b3", "2026-03-09T10:10:00+02:00", "AT-20210-5515"),
        ("c44", "SMS-MO", "n2", "n8", "2026-03-07T16:25:00+02:00", "AT-20210-5504"),
        ("c45", "MOC", "n1", "n3", "2026-03-05T08:50:00+02:00", "AT-20210-5501"),
        ("c46", "MOC", "n3b", "n2", "2026-03-05T18:10:00+02:00", "AT-20210-5505"),
        ("c47", "SMS-MO", "n4", "n1", "2026-02-28T12:05:00+02:00", "AT-20210-5508"),
        ("c48", "MOC", "n5", "n2", "2026-03-07T12:40:00+02:00", "AT-20210-5509"),
        ("c49", "SMS-MO", "n6", "n4", "2026-02-27T13:30:00+02:00", "AT-20210-5510"),
        ("c50", "MOC", "n7", "n3", "2026-03-04T11:10:00+02:00", "AT-20210-5512"),
        ("c51", "SMS-MO", "n8", "n5", "2026-02-21T21:15:00+02:00", "AT-20210-5514"),
        ("c52", "MOC", "n1", "b3", "2026-02-27T09:05:00+02:00", "AT-20210-5501"),
        ("c53", "MOC", "n2", "n6", "2026-03-09T18:20:00+02:00", "AT-20210-5504"),
        ("c54", "SMS-MO", "n3", "n7", "2026-03-08T20:00:00+02:00", "AT-20210-5506"),
        ("c55", "MOC", "n4", "n8", "2026-03-10T17:25:00+02:00", "AT-20210-5508"),
    ]
    for record_id, record_type, subscriber, peer, ts_local, cell_id in background_specs:
        outbound = record_type in {"MOC", "SMS-MO"}
        caller, called = (subscriber, peer) if outbound else (peer, subscriber)
        is_sms = record_type.startswith("SMS")
        rows.append(
            row(
                record_id,
                record_type,
                subscriber,
                caller,
                called,
                DEVICES.get(subscriber, ""),
                cell_id,
                ts_local,
                duration_s="" if is_sms else rng.randint(20, 900),
                sms_len=rng.randint(12, 90) if is_sms else "",
            )
        )

    # Carrier sequence is chronological, independent of the semantic IDs.
    rows.sort(key=lambda item: (item["ts_local"], item["record_id"]))
    sequence = 10000
    for item in rows:
        sequence += rng.randint(1, 7)
        item["seq"] = sequence
    return rows
