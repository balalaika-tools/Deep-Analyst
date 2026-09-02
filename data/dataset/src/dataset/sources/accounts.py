"""Build the bank-account roster shared by the transaction ledger and case-story builders."""

from typing import Any

from dataset.core import state
from dataset.core.util import _make_gr_iban


def build_accounts() -> list[dict[str, Any]]:
    roster = [
        ("acct_pa", "Alexandros Mavridis", "person", "7719", "2021-04-12"),
        ("acct_pr", "K. Rossi", "person", "2205", "2022-09-08"),
        ("acct_pd", "Dimitris Mavridis", "person", "8830", "2019-06-20"),
        ("acct_aegean", "Aegean Trade OE", "organization", "6118", "2020-02-17"),
        ("acct_meridian", "Meridian Consulting Ltd", "organization", "4401", "2018-11-05"),
        ("acct_ionian", "Ionian Supplies IKE", "organization", "9034", "2023-03-14"),
        ("nA01", "Elena Vasileiou", "person", "1001", "2017-01-09"),
        ("nA02", "G. Papadakis", "person", "1002", "2020-07-18"),
        ("nA03", "M. Antoniou", "person", "1003", "2016-05-26"),
        ("nA04", "S. Antoniou", "person", "1004", "2016-05-26"),
        ("nA05", "N. Georgiou", "person", "1005", "2024-09-02"),
        ("nA06", "P. Nikolaou", "person", "1006", "2012-03-21"),
        ("nA07", "T. Karra", "person", "1007", "2021-10-11"),
        ("nA08", "A. Vasileiou", "person", "1008", "2014-08-04"),
        ("nA09", "Alexandra Mavridou", "person", "1009", "2023-12-19"),
        ("nA10", "Logistiki Attikis", "organization", "1010", "2015-04-30"),
        ("nA11", "Akinita Saronikou IKE", "organization", "1011", "2013-02-15"),
        ("nA12", "Attica Retail AE", "organization", "1012", "2011-09-28"),
    ]
    accounts: list[dict[str, Any]] = []
    for serial, (account_id, name, holder_type, ending, opened_date) in enumerate(roster, 1):
        accounts.append(
            {
                "account_id": account_id,
                "iban": _make_gr_iban(serial, ending),
                "holder_name": name,
                "holder_type": holder_type,
                "bic": "TRGSGR2A",
                "opened_date": opened_date,
                "source_version": state.SOURCE_VERSIONS["bank"],
            }
        )
    return accounts
