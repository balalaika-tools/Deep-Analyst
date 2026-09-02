"""Build the device-extraction message feed."""

from typing import Any

from dataset.core import state
from dataset.core.fixtures import DEVICES, PHONES
from dataset.core.state import _tr


def build_extraction() -> list[dict[str, Any]]:
    def message(
        msg_id: str,
        device: str,
        direction: str,
        peer: str,
        app: str,
        ts_utc: str,
        body: str | None,
    ) -> dict[str, Any]:
        return {
            "msg_id": msg_id,
            "imei": DEVICES[device],
            "subscriber_msisdn": PHONES[device],
            "direction": direction,
            "peer": PHONES[peer],
            "app": app,
            "ts_utc": ts_utc,
            "body": body,
            "source_version": state.SOURCE_VERSIONS["extraction"],
        }

    return [
        message(
            "X-204",
            "pa",
            "out",
            "pr",
            "sms",
            "2026-03-04T21:14:00Z",
            _tr(
                "φεύγει αύριο, ίδιο μέρος με την άλλη φορά",
                "leaving tomorrow, same place as last time",
            ),
        ),
        message(
            "X-205",
            "pa",
            "in",
            "pr",
            "sms",
            "2026-03-04T21:20:00Z",
            _tr("οκ, τα λέμε εκεί", "ok, see you there"),
        ),
        message(
            "X-206", "pa", "out", "pr", "sms", "2026-02-26T19:45:00Z", _tr("έφτασε", "it arrived")
        ),
        message(
            "X-207",
            "pa",
            "out",
            "pr",
            "sms",
            "2026-03-05T19:14:00Z",
            _tr("όλα εντάξει", "all good"),
        ),
        message(
            "X-208",
            "pa",
            "out",
            "pr",
            "sms",
            "2026-02-21T09:05:00Z",
            _tr(
                "Κυρία Ρόσση, η Σοφία είμαι από το γραφείο· θα αργήσω δέκα λεπτά.",
                "Ms Rossi, this is Sofia from the office; I will be ten minutes late.",
            ),
        ),
        message(
            "X-301",
            "pa",
            "out",
            "pr",
            "whatsapp",
            "2026-03-03T08:50:00Z",
            _tr("στείλε τα μισά αύριο, όχι όλα μαζί", "send half tomorrow, not all at once"),
        ),
        message(
            "X-302",
            "pa",
            "in",
            "pr",
            "whatsapp",
            "2026-03-03T08:57:00Z",
            _tr("έγινε, το πακέτο Πέμπτη", "done, the package on Thursday"),
        ),
        message(
            "X-303",
            "pa",
            "out",
            "pr",
            "sms",
            "2026-02-24T10:05:00Z",
            _tr("στείλε το τιμολόγιο όπως είπαμε", "send the invoice as agreed"),
        ),
        message(
            "X-N01",
            "n1",
            "out",
            "n2",
            "sms",
            "2026-02-23T08:00:30Z",
            _tr("Σε περιμένω στην είσοδο.", "I am waiting for you at the entrance."),
        ),
        message(
            "X-N02",
            "n1",
            "in",
            "n7",
            "whatsapp",
            "2026-02-22T17:02:00Z",
            _tr("Θα περάσω το απόγευμα.", "I will stop by this afternoon."),
        ),
        message(
            "X-N03",
            "n2",
            "out",
            "n6",
            "whatsapp",
            "2026-02-21T11:28:00Z",
            _tr("Έφτασα, είμαι έξω.", "I have arrived, I am outside."),
        ),
        message(
            "X-N04",
            "n3",
            "out",
            "n3b",
            "sms",
            "2026-02-22T16:30:00Z",
            _tr("Πήρα ψωμί και γάλα.", "I bought bread and milk."),
        ),
        message(
            "X-N05",
            "n4",
            "in",
            "n7",
            "telegram",
            "2026-02-20T16:48:00Z",
            _tr("Το μάθημα τελείωσε νωρίς.", "The class finished early."),
        ),
        message(
            "X-N06",
            "n5",
            "out",
            "n6",
            "sms",
            "2026-02-26T10:40:00Z",
            _tr("Ευχαριστώ, θα τηλεφωνήσω αύριο.", "Thank you, I will call tomorrow."),
        ),
        message(
            "X-N07",
            "n6",
            "out",
            "n8",
            "whatsapp",
            "2026-03-06T13:12:00Z",
            _tr("Έστειλα το προσχέδιο.", "I sent the draft."),
        ),
        message(
            "X-N08",
            "n7",
            "out",
            "n1",
            "signal",
            "2026-03-02T09:25:00Z",
            _tr("Χρόνια πολλά!", "Happy birthday!"),
        ),
        message(
            "X-N09",
            "n8",
            "out",
            "n4",
            "sms",
            "2026-03-03T15:40:00Z",
            _tr("Θα είμαι εκεί στις έξι.", "I will be there at six."),
        ),
        message("X-N10", "n3b", "in", "n2", "whatsapp", "2026-03-05T16:12:00Z", None),
    ]
