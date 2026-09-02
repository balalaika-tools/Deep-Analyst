-- TRG synthetic bank fixture (PostgreSQL 14+)
-- Synthetic data only. No identifier or event belongs to a real subject.

BEGIN;

CREATE TABLE accounts (
    account_id     TEXT NOT NULL,
    iban           TEXT NOT NULL,
    holder_name    TEXT,
    holder_type    TEXT CHECK (holder_type IN ('person', 'organization')),
    bic            TEXT,
    opened_date    TEXT,
    source_version TEXT NOT NULL,
    PRIMARY KEY (account_id),
    UNIQUE (iban)
);

CREATE TABLE transactions (
    txn_id          TEXT NOT NULL,
    booking_ts_utc  TEXT NOT NULL,
    value_date      TEXT NOT NULL,
    debtor_name     TEXT,
    debtor_iban     TEXT NOT NULL,
    debtor_bic      TEXT,
    creditor_name   TEXT,
    creditor_iban   TEXT NOT NULL,
    creditor_bic    TEXT,
    amount_text     TEXT NOT NULL,
    currency        TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'booked',
    remittance_info TEXT,
    source_version  TEXT NOT NULL,
    PRIMARY KEY (txn_id),
    FOREIGN KEY (debtor_iban) REFERENCES accounts (iban),
    FOREIGN KEY (creditor_iban) REFERENCES accounts (iban)
);

INSERT INTO accounts (account_id, iban, holder_name, holder_type, bic, opened_date, source_version) VALUES
    ('acct_pa', 'GR8001100010000000000017719', 'Alexandros Mavridis', 'person', 'TRGSGR2A', '2021-04-12', 'bank@1-el'),
    ('acct_pr', 'GR1401100010000000000022205', 'K. Rossi', 'person', 'TRGSGR2A', '2022-09-08', 'bank@1-el'),
    ('acct_pd', 'GR5501100010000000000038830', 'Dimitris Mavridis', 'person', 'TRGSGR2A', '2019-06-20', 'bank@1-el'),
    ('acct_aegean', 'GR9201100010000000000046118', 'Aegean Trade OE', 'organization', 'TRGSGR2A', '2020-02-17', 'bank@1-el'),
    ('acct_meridian', 'GR3601100010000000000054401', 'Meridian Consulting Ltd', 'organization', 'TRGSGR2A', '2018-11-05', 'bank@1-el'),
    ('acct_ionian', 'GR2601100010000000000069034', 'Ionian Supplies IKE', 'organization', 'TRGSGR2A', '2023-03-14', 'bank@1-el'),
    ('nA01', 'GR7301100010000000000071001', 'Elena Vasileiou', 'person', 'TRGSGR2A', '2017-01-09', 'bank@1-el'),
    ('nA02', 'GR9401100010000000000081002', 'G. Papadakis', 'person', 'TRGSGR2A', '2020-07-18', 'bank@1-el'),
    ('nA03', 'GR1801100010000000000091003', 'M. Antoniou', 'person', 'TRGSGR2A', '2016-05-26', 'bank@1-el'),
    ('nA04', 'GR3901100010000000000101004', 'S. Antoniou', 'person', 'TRGSGR2A', '2016-05-26', 'bank@1-el'),
    ('nA05', 'GR6001100010000000000111005', 'N. Georgiou', 'person', 'TRGSGR2A', '2024-09-02', 'bank@1-el'),
    ('nA06', 'GR8101100010000000000121006', 'P. Nikolaou', 'person', 'TRGSGR2A', '2012-03-21', 'bank@1-el'),
    ('nA07', 'GR0501100010000000000131007', 'T. Karra', 'person', 'TRGSGR2A', '2021-10-11', 'bank@1-el'),
    ('nA08', 'GR2601100010000000000141008', 'A. Vasileiou', 'person', 'TRGSGR2A', '2014-08-04', 'bank@1-el'),
    ('nA09', 'GR4701100010000000000151009', 'Alexandra Mavridou', 'person', 'TRGSGR2A', '2023-12-19', 'bank@1-el'),
    ('nA10', 'GR6801100010000000000161010', 'Logistiki Attikis', 'organization', 'TRGSGR2A', '2015-04-30', 'bank@1-el'),
    ('nA11', 'GR8901100010000000000171011', 'Akinita Saronikou IKE', 'organization', 'TRGSGR2A', '2013-02-15', 'bank@1-el'),
    ('nA12', 'GR1301100010000000000181012', 'Attica Retail AE', 'organization', 'TRGSGR2A', '2011-09-28', 'bank@1-el');

INSERT INTO transactions (txn_id, booking_ts_utc, value_date, debtor_name, debtor_iban, debtor_bic, creditor_name, creditor_iban, creditor_bic, amount_text, currency, status, remittance_info, source_version) VALUES
    ('t_60', '2026-02-27T10:20:00Z', '2026-02-27', 'Aegean Trade OE', 'GR9201100010000000000046118', 'TRGSGR2A', 'Meridian Consulting Ltd', 'GR3601100010000000000054401', 'TRGSGR2A', '9400.00', 'EUR', 'booked', 'consulting services INV-2108', 'bank@1-el'),
    ('t_85', '2026-03-03T09:40:00Z', '2026-03-03', 'Aegean Trade OE', 'GR9201100010000000000046118', 'TRGSGR2A', 'Ionian Supplies IKE', 'GR2601100010000000000069034', 'TRGSGR2A', '9500.00', 'EUR', 'booked', 'INV-1102', 'bank@1-el'),
    ('t_86', '2026-03-04T10:05:00Z', '2026-03-04', 'Aegean Trade OE', 'GR9201100010000000000046118', 'TRGSGR2A', 'Ionian Supplies IKE', 'GR2601100010000000000069034', 'TRGSGR2A', '9700.00', 'EUR', 'booked', 'INV-1103', 'bank@1-el'),
    ('t_88', '2026-03-05T14:30:00Z', '2026-03-05', 'Aegean Trade OE', 'GR9201100010000000000046118', 'TRGSGR2A', 'Meridian Consulting Ltd', 'GR3601100010000000000054401', 'TRGSGR2A', '9800.00', 'EUR', 'booked', 'consulting services INV-2231', 'bank@1-el'),
    ('t_90', '2026-03-09T09:05:00Z', '2026-03-09', 'Meridian Consulting Ltd', 'GR3601100010000000000054401', 'TRGSGR2A', 'Alexandros Mavridis', 'GR8001100010000000000017719', 'TRGSGR2A', '2500.00', 'EUR', 'booked', 'ΑΜΟΙΒΗ ΣΥΜΒΟΥΛΟΥ 03/2026', 'bank@1-el'),
    ('t_B1', '2026-03-02T08:00:00Z', '2026-03-02', 'Dimitris Mavridis', 'GR5501100010000000000038830', 'TRGSGR2A', 'Akinita Saronikou IKE', 'GR8901100010000000000171011', 'TRGSGR2A', '9200.00', 'EUR', 'booked', 'ΕΝΟΙΚΙΟ ΕΤΗΣΙΟ 2026', 'bank@1-el'),
    ('t_B2', '2026-02-27T16:00:00Z', '2026-02-27', 'Logistiki Attikis', 'GR6801100010000000000161010', 'TRGSGR2A', 'Dimitris Mavridis', 'GR5501100010000000000038830', 'TRGSGR2A', '1450.00', 'EUR', 'booked', 'ΜΙΣΘΟΔΟΣΙΑ 02/2026', 'bank@1-el'),
    ('t_B3', '2026-03-06T11:30:00Z', '2026-03-06', 'Dimitris Mavridis', 'GR5501100010000000000038830', 'TRGSGR2A', 'Alexandros Mavridis', 'GR8001100010000000000017719', 'TRGSGR2A', '200.00', 'EUR', 'booked', 'ΧΡΟΝΙΑ ΠΟΛΛΑ', 'bank@1-el'),
    ('nT01', '2026-02-27T14:00:00Z', '2026-02-27', 'Attica Retail AE', 'GR1301100010000000000181012', 'TRGSGR2A', 'Elena Vasileiou', 'GR7301100010000000000071001', 'TRGSGR2A', '1600.00', 'EUR', 'booked', 'ΜΙΣΘΟΔΟΣΙΑ 02/2026', 'bank@1-el'),
    ('nT02', '2026-03-02T08:15:00Z', '2026-03-02', 'Elena Vasileiou', 'GR7301100010000000000071001', 'TRGSGR2A', 'Akinita Saronikou IKE', 'GR8901100010000000000171011', 'TRGSGR2A', '750.00', 'EUR', 'booked', 'ΕΝΟΙΚΙΟ 03/2026', 'bank@1-el'),
    ('nT03', '2026-03-04T18:05:00Z', '2026-03-04', 'Elena Vasileiou', 'GR7301100010000000000071001', 'TRGSGR2A', 'G. Papadakis', 'GR9401100010000000000081002', 'TRGSGR2A', '24.50', 'EUR', 'booked', 'TAXI', 'bank@1-el'),
    ('nT04', '2026-02-23T10:30:00Z', '2026-02-23', 'M. Antoniou', 'GR1801100010000000000091003', 'TRGSGR2A', 'Akinita Saronikou IKE', 'GR8901100010000000000171011', 'TRGSGR2A', '9300.00', 'EUR', 'booked', 'ΕΤΗΣΙΑ ΜΙΣΘΩΣΗ ΑΠΟΘΗΚΗΣ', 'bank@1-el'),
    ('nT05', '2026-02-25T12:45:00Z', '2026-02-25', 'Attica Retail AE', 'GR1301100010000000000181012', 'TRGSGR2A', 'Logistiki Attikis', 'GR6801100010000000000161010', 'TRGSGR2A', '9600.00', 'EUR', 'booked', 'ΕΞΟΠΛΙΣΜΟΣ INV-2237', 'bank@1-el'),
    ('nT06', '2026-02-26T17:10:00Z', '2026-02-26', 'Alexandros Mavridis', 'GR8001100010000000000017719', 'TRGSGR2A', 'Attica Retail AE', 'GR1301100010000000000181012', 'TRGSGR2A', '68.40', 'EUR', 'booked', 'ΑΓΟΡΑ ΓΡΑΦΙΚΗΣ ΥΛΗΣ', 'bank@1-el'),
    ('nT07', '2026-03-03T17:40:00Z', '2026-03-03', 'Elena Vasileiou', 'GR7301100010000000000071001', 'TRGSGR2A', 'Attica Retail AE', 'GR1301100010000000000181012', 'TRGSGR2A', '42.80', 'EUR', 'booked', 'SUPERMARKET', 'bank@1-el'),
    ('nT08', '2026-03-05T17:20:00Z', '2026-03-05', 'Elena Vasileiou', 'GR7301100010000000000071001', 'TRGSGR2A', 'Attica Retail AE', 'GR1301100010000000000181012', 'TRGSGR2A', '18.35', 'EUR', 'booked', 'ΦΑΡΜΑΚΕΙΟ', 'bank@1-el'),
    ('nT09', '2026-02-20T09:15:00Z', '2026-02-20', 'A. Vasileiou', 'GR2601100010000000000141008', 'TRGSGR2A', 'N. Georgiou', 'GR6001100010000000000111005', 'TRGSGR2A', '350.00', 'EUR', 'booked', 'ΜΗΝΙΑΙΟ ΕΠΙΔΟΜΑ', 'bank@1-el'),
    ('nT10', '2026-03-06T16:10:00Z', '2026-03-06', 'N. Georgiou', 'GR6001100010000000000111005', 'TRGSGR2A', 'Attica Retail AE', 'GR1301100010000000000181012', 'TRGSGR2A', '27.90', 'EUR', 'booked', 'ΒΙΒΛΙΑ', 'bank@1-el'),
    ('nT11', '2026-02-26T08:25:00Z', '2026-02-26', 'Attica Retail AE', 'GR1301100010000000000181012', 'TRGSGR2A', 'P. Nikolaou', 'GR8101100010000000000121006', 'TRGSGR2A', '780.00', 'EUR', 'booked', 'ΣΥΝΤΑΞΗ 02/2026', 'bank@1-el'),
    ('nT12', '2026-03-02T11:35:00Z', '2026-03-02', 'P. Nikolaou', 'GR8101100010000000000121006', 'TRGSGR2A', 'Attica Retail AE', 'GR1301100010000000000181012', 'TRGSGR2A', '54.20', 'EUR', 'booked', 'ΦΑΡΜΑΚΕΙΟ', 'bank@1-el'),
    ('nT13', '2026-02-24T13:00:00Z', '2026-02-24', 'M. Antoniou', 'GR1801100010000000000091003', 'TRGSGR2A', 'T. Karra', 'GR0501100010000000000131007', 'TRGSGR2A', '450.00', 'EUR', 'booked', 'ΤΙΜΟΛΟΓΙΟ WEB-104', 'bank@1-el'),
    ('nT14', '2026-03-06T13:20:00Z', '2026-03-06', 'Alexandra Mavridou', 'GR4701100010000000000151009', 'TRGSGR2A', 'T. Karra', 'GR0501100010000000000131007', 'TRGSGR2A', '620.00', 'EUR', 'booked', 'ΤΙΜΟΛΟΓΙΟ DESIGN-22', 'bank@1-el'),
    ('nT15', '2026-03-10T10:10:00Z', '2026-03-10', 'Attica Retail AE', 'GR1301100010000000000181012', 'TRGSGR2A', 'T. Karra', 'GR0501100010000000000131007', 'TRGSGR2A', '780.00', 'EUR', 'booked', 'ΤΙΜΟΛΟΓΙΟ SUPPORT-8', 'bank@1-el'),
    ('nT16', '2026-02-22T18:30:00Z', '2026-02-22', 'M. Antoniou', 'GR1801100010000000000091003', 'TRGSGR2A', 'S. Antoniou', 'GR3901100010000000000101004', 'TRGSGR2A', '120.00', 'EUR', 'booked', 'ΚΟΙΝΑ ΕΞΟΔΑ', 'bank@1-el'),
    ('nT17', '2026-03-01T19:00:00Z', '2026-03-01', 'S. Antoniou', 'GR3901100010000000000101004', 'TRGSGR2A', 'M. Antoniou', 'GR1801100010000000000091003', 'TRGSGR2A', '95.00', 'EUR', 'booked', 'ΛΟΓΑΡΙΑΣΜΟΙ', 'bank@1-el'),
    ('nT18', '2026-02-21T12:10:00Z', '2026-02-21', 'G. Papadakis', 'GR9401100010000000000081002', 'TRGSGR2A', 'Elena Vasileiou', 'GR7301100010000000000071001', 'TRGSGR2A', '40.00', 'EUR', 'booked', 'ΕΠΙΣΤΡΟΦΗ', 'bank@1-el'),
    ('nT19', '2026-02-28T20:35:00Z', '2026-02-28', 'G. Papadakis', 'GR9401100010000000000081002', 'TRGSGR2A', 'Attica Retail AE', 'GR1301100010000000000181012', 'TRGSGR2A', '71.60', 'EUR', 'booked', 'ΚΑΥΣΙΜΑ', 'bank@1-el'),
    ('nT20', '2026-03-07T12:20:00Z', '2026-03-07', 'G. Papadakis', 'GR9401100010000000000081002', 'TRGSGR2A', 'P. Nikolaou', 'GR8101100010000000000121006', 'TRGSGR2A', '65.00', 'EUR', 'booked', 'ΟΙΚΟΓΕΝΕΙΑΚΑ', 'bank@1-el'),
    ('nT21', '2026-02-27T19:15:00Z', '2026-02-27', 'A. Vasileiou', 'GR2601100010000000000141008', 'TRGSGR2A', 'Elena Vasileiou', 'GR7301100010000000000071001', 'TRGSGR2A', '100.00', 'EUR', 'booked', 'ΔΩΡΟ', 'bank@1-el'),
    ('nT22', '2026-03-08T10:45:00Z', '2026-03-08', 'Elena Vasileiou', 'GR7301100010000000000071001', 'TRGSGR2A', 'A. Vasileiou', 'GR2601100010000000000141008', 'TRGSGR2A', '80.00', 'EUR', 'booked', 'ΕΠΙΣΤΡΟΦΗ', 'bank@1-el'),
    ('nT23', '2026-02-24T09:50:00Z', '2026-02-24', 'Alexandra Mavridou', 'GR4701100010000000000151009', 'TRGSGR2A', 'Attica Retail AE', 'GR1301100010000000000181012', 'TRGSGR2A', '39.90', 'EUR', 'booked', 'ΟΙΚΙΑΚΑ', 'bank@1-el'),
    ('nT24', '2026-03-04T12:25:00Z', '2026-03-04', 'Alexandra Mavridou', 'GR4701100010000000000151009', 'TRGSGR2A', 'G. Papadakis', 'GR9401100010000000000081002', 'TRGSGR2A', '31.00', 'EUR', 'booked', 'TAXI', 'bank@1-el'),
    ('nT25', '2026-03-09T15:45:00Z', '2026-03-09', 'Logistiki Attikis', 'GR6801100010000000000161010', 'TRGSGR2A', 'Attica Retail AE', 'GR1301100010000000000181012', 'TRGSGR2A', '310.00', 'EUR', 'booked', 'ΑΝΑΛΩΣΙΜΑ', 'bank@1-el'),
    ('nT26', '2026-02-20T16:40:00Z', '2026-02-20', 'Akinita Saronikou IKE', 'GR8901100010000000000171011', 'TRGSGR2A', 'Logistiki Attikis', 'GR6801100010000000000161010', 'TRGSGR2A', '520.00', 'EUR', 'booked', 'ΛΟΓΙΣΤΙΚΕΣ ΥΠΗΡΕΣΙΕΣ', 'bank@1-el'),
    ('nT27', '2026-03-10T18:00:00Z', '2026-03-10', 'S. Antoniou', 'GR3901100010000000000101004', 'TRGSGR2A', 'Attica Retail AE', 'GR1301100010000000000181012', 'TRGSGR2A', '88.70', 'EUR', 'booked', 'SUPERMARKET', 'bank@1-el');

COMMIT;
