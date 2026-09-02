"""Build the email feed."""

from typing import Any

from dataset.core import state
from dataset.core.state import _tr


def build_emails(case_id: str) -> list[dict[str, Any]]:
    specs = [
        {
            "email_id": "eM1",
            "message_id": "<7f3a91@meridian-consulting.example>",
            "from": '"A. Mavridis" <alex@meridian-consulting.example>',
            "to": "k.rossi@aegeantrade.example",
            "date": "Wed, 4 Mar 2026 18:40:11 +0200",
            "subject": _tr("Πέμπτη", "Thursday"),
            "body": _tr("Το πακέτο την Πέμπτη.", "The package on Thursday.")
            + "\n\nA. Mavridis\n697 123 4567",
        },
        {
            "email_id": "eM2",
            "message_id": "<inv2231-draft@aegeantrade.example>",
            "from": '"K. Rossi" <k.rossi@aegeantrade.example>',
            "to": "alex@meridian-consulting.example",
            "date": "Tue, 24 Feb 2026 12:10:00 +0200",
            "subject": "INV-2231",
            "body": _tr(
                "Προσχέδιο: consulting services / market analysis, INV-2231.\nΠαρακαλώ επιβεβαίωσε τη διατύπωση.",
                "Draft: consulting services / market analysis, INV-2231.\nPlease confirm the wording.",
            )
            + "\n\nK. Rossi\n6949876543",
        },
        {
            "email_id": "eM3",
            "message_id": "<payment-20260309@meridian-consulting.example>",
            "from": '"Meridian Consulting Ltd" <noreply@meridian-consulting.example>',
            "to": '"Alexandros Mavridis" <alex@meridian-consulting.example>',
            "date": "Mon, 9 Mar 2026 09:00:00 +0200",
            "subject": _tr("Αμοιβή Μαρτίου", "March fee"),
            "body": _tr(
                "Εντολή πληρωμής €2,500 για ΑΜΟΙΒΗ ΣΥΜΒΟΥΛΟΥ 03/2026.\nΗ πίστωση θα εμφανιστεί στον δηλωμένο λογαριασμό.",
                "Payment instruction for €2,500 for CONSULTING FEE 03/2026.\nThe credit will appear in the designated account.",
            ),
        },
        {
            "email_id": "eM4",
            "message_id": "<rent-nt02@akinita-saronikou.example>",
            "from": '"Akinita Saronikou IKE" <receipts@akinita-saronikou.example>',
            "to": '"Elena Vasileiou" <elena.vasileiou@personal.example>',
            "date": "Mon, 2 Mar 2026 08:20:00 +0200",
            "subject": _tr("Απόδειξη ενοικίου", "Rent receipt"),
            "body": _tr(
                "Λάβαμε το ενοίκιο Μαρτίου ύψους €750.00. Αναφορά συναλλαγής nT02.",
                "We received the March rent of €750.00. Transaction reference nT02.",
            ),
        },
        {
            "email_id": "eM5",
            "message_id": "<employment-dm@logistiki-attikis.example>",
            "from": '"Logistiki Attikis" <hr@logistiki-attikis.example>',
            "to": '"Dimitris Mavridis" <dimitris.mavridis@personal.example>',
            "date": "Mon, 2 Mar 2026 09:50:00 +0200",
            "subject": _tr("Βεβαίωση εργασίας", "Employment confirmation"),
            "body": _tr(
                "Βεβαιώνεται ότι ο Dimitris Mavridis εργάζεται στη Logistiki Attikis.\nΤηλεφωνικό κέντρο: +30 210 111 2233",
                "This is to confirm that Dimitris Mavridis is employed by Logistiki Attikis.\nMain telephone: +30 210 111 2233",
            ),
        },
        {
            "email_id": "eM6",
            "message_id": "<ride-20260304@atticataxi.example>",
            "from": '"Attica Taxi receipt service" <receipts@atticataxi.example>',
            "to": '"A. Mavridis" <alex@meridian-consulting.example>',
            "date": "Fri, 6 Mar 2026 10:00:00 +0200",
            "subject": _tr("Απόδειξη διαδρομής", "Trip receipt"),
            "body": _tr(
                "Διαδρομή 4 Μαρτίου 2026. Οδηγός: G. Papadakis, τηλέφωνο +30 693 000 0102.\nΗ απόδειξη αφορά συνήθη υπηρεσία ταξί.",
                "Trip on 4 March 2026. Driver: G. Papadakis, telephone +30 693 000 0102.\nThe receipt concerns an ordinary taxi service.",
            ),
        },
    ]

    records: list[dict[str, Any]] = []
    for spec in specs:
        headers = {
            "Message-ID": spec["message_id"],
            "From": spec["from"],
            "To": spec["to"],
            "Date": spec["date"],
            "Subject": spec["subject"],
            "Content-Type": "text/plain; charset=UTF-8",
            "X-Case-ID": case_id,
            "X-Source-Record-ID": spec["email_id"],
            "X-Source-Version": state.SOURCE_VERSIONS["email"],
        }
        header_order = [
            "Message-ID",
            "From",
            "To",
            "Date",
            "Subject",
            "Content-Type",
            "X-Case-ID",
            "X-Source-Record-ID",
            "X-Source-Version",
        ]
        raw = "\n".join(f"{name}: {headers[name]}" for name in header_order)
        raw += "\n\n" + spec["body"] + "\n"
        records.append(
            {
                "email_id": spec["email_id"],
                "headers": headers,
                "body": spec["body"],
                "raw_bytes": raw.encode("utf-8"),
            }
        )
    return records
