# Synthetic Multi-Source Financial Reconciliation Dataset
(Razorpay-style — synthetic, for hackathon use only. **This is not real Razorpay data.**
Field names/IDs are stylistically inspired by Razorpay's public payments/settlements
documentation purely for realism.)

All monetary amounts are integers in **paise** (smallest INR unit, 100 paise = ₹1),
matching Razorpay's API convention. Merchant: `acc_ACMERT0001`.

## Files (agent-facing — give these to your reconciliation agent)

### `payments.csv`
| column | meaning |
|---|---|
| payment_id | `pay_...` unique ID |
| order_id | `order_...` — note: a failed attempt + its successful retry share the same order_id (looks duplicate, isn't) |
| merchant_id | constant |
| amount | gross amount (paise) |
| currency | INR |
| method | card / upi / netbanking / wallet |
| status | captured / failed / refunded / partially_refunded |
| fee, tax | Razorpay fee + 18% GST on fee (0 for failed) |
| net_amount | amount − fee − tax (0 for failed) |
| created_at, captured_at | timestamps |
| settlement_id | which settlement this payment's net amount was paid out in (blank if failed, or if fully refunded pre-settlement, or if not yet settled) |
| notes | free text; flags retries / duplicate records |

### `refunds.csv`
`refund_id, payment_id, amount, status, speed, created_at, notes`
Refunds before the settlement cutoff (capture_date + 2 days) are deducted directly from
that settlement's total. Refunds **after** the cutoff instead generate an `adjustments.csv`
row (`type = refund_deduction`) inside the **next** settlement.

### `adjustments.csv`
`adjustment_id, settlement_id, payment_id, type, amount, description, created_at`
`type` ∈ `refund_deduction` (negative), `fee_correction` (+/−), `chargeback` (negative).
`amount` is already netted into the parent settlement's `amount`.

### `settlements.csv`
`settlement_id, settlement_utr, merchant_id, amount, status, type, payment_count, created_at, settled_at`
`status` = processed / failed. `type` = regular (one per day, daily batch) or on_demand
(the hand-crafted special cases below). `amount` is the final net payout figure
(after refunds + adjustments) — this is the number that *should* show up at the bank.

### `bank_transactions.csv`
`bank_txn_id, utr, amount, type, value_date, narration, account_number`
Join to `settlements.csv` via `utr` ↔ `settlement_utr`. **Not every settlement has a
matching bank row** (see missing-transaction case), and **one settlement has two bank
rows** (duplicate-credit case) — simple `1:1 ID==ID` joins will not fully solve this
dataset; you need amount/date/UTR reasoning.

## `ground_truth.csv` (⚠️ evaluation only — do not feed to the agent)
One row per reconciliation "case" the agent should ideally discover, columns:
`case_id, case_type, related_payment_ids, related_refund_ids, related_adjustment_ids,
related_settlement_ids, related_bank_txn_ids, expected_relationship,
expected_settlement_amount, actual_amount, discrepancy, reason, auto_resolvable,
intentional_unresolved_exception`

`related_*` columns are `;`-separated lists of the exact IDs that make up the correct
match for that case — use these to grade an agent's proposed matches.

### Case types included
- `settlement_bank_match` (51) — clean exact matches, the bulk of the data
- `timing_difference` (5) — amount matches, bank value_date is 1–2 days later
- `payment_fully_refunded_pre_settlement` (16) / `payment_partially_refunded_pre_settlement` (12)
- `duplicate_looking_payment_pair` (6) — failed attempt + captured retry, same order_id (not a true duplicate)
- `true_duplicate_payment_record` (1) — genuine logging duplicate, must be deduped
- `partial_match_unexplained_tds` (1) — bank shortfall with no adjustment record → **unresolved**
- `duplicate_bank_credit` (1) — two bank credits for one settlement → **unresolved**
- `missing_bank_transaction` (1) — settlement processed, no bank row at all → **unresolved**
- `incorrect_amount_unresolved` (1) — random unexplained amount gap → **unresolved**
- `genuinely_unresolved_discrepancy` (1) — same idea, deliberately left with no findable cause → **unresolved**
- `failed_settlement` (1) — status=failed, correctly zero bank credit (not a real discrepancy)
- `post_settlement_refund_adjustment` (1) — refund after payout, deducted from the *next* settlement — fully explainable across 3 tables
- `fee_correction_adjustment` (1), `chargeback_adjustment` (1) — adjustment fully explains a non-trivial settlement amount

Everything is generated programmatically so `ground_truth.csv` is derived directly from
the actual relationships baked into the other five files — it is internally consistent
by construction, not hand-typed separately.
