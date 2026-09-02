"""Fixed phone-number and device catalog shared by the case-story builders."""

from dataset.core.util import _make_imei

PHONES = {
    "pa": "+306971234567",
    "pr": "+306949876543",
    "pd": "+306912223344",
    "sofia": "+306971234567",
    "aegean": "+302104455667",
    "u1": "+306955512399",
    "n1": "+306930000101",
    "n2": "+306930000102",
    "n3": "+306930000103",
    "n3b": "+306930000104",
    "n4": "+306930000105",
    "n5": "+306930000106",
    "n6": "+306930000107",
    "n7": "+306930000108",
    "n8": "+306930000109",
    "b1": "+302101112233",
    "b2": "+302109000202",
    "b3": "+302109000203",
}


DEVICES = {
    "pa": "356923107744818",
    "pr": "353210559812001",
    "n1": _make_imei(1),
    "n2": _make_imei(2),
    "n3": _make_imei(3),
    "n3b": _make_imei(4),
    "n4": _make_imei(5),
    "n5": _make_imei(6),
    "n6": _make_imei(7),
    "n7": _make_imei(8),
    "n8": _make_imei(9),
}
