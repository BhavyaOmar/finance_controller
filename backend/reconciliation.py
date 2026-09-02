"""
Core Reconciliation Engine
--------------------------
Deterministic, settlement-level 3-way financial reconciliation using Pandas.
Reconciles Payments + Refunds + Adjustments -> Settlements -> Bank Transactions.
"""

import sys
import os
from pathlib import Path
from typing import Dict, Tuple
import pandas as pd

# Safe console encoding for Windows environments
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def format_inr(paise: int | float) -> str:
    """Format paise amount into clean INR currency string."""
    rupees = (paise or 0) / 100.0
    return f"INR {rupees:,.2f}"


def load_datasets(data_dir: str | Path) -> Dict[str, pd.DataFrame]:
    """Load all required CSV datasets."""
    data_path = Path(data_dir)
    return {
        "payments": pd.read_csv(data_path / "payments.csv"),
        "refunds": pd.read_csv(data_path / "refunds.csv"),
        "adjustments": pd.read_csv(data_path / "adjustments.csv"),
        "settlements": pd.read_csv(data_path / "settlements.csv"),
        "bank_transactions": pd.read_csv(data_path / "bank_transactions.csv"),
    }


def aggregate_payments_and_refunds(
    payments_df: pd.DataFrame, refunds_df: pd.DataFrame, adjustments_df: pd.DataFrame
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Aggregate payment totals, pre-settlement refund deductions, and adjustments per settlement.
    """
    # 1. Filter settled payments and aggregate
    settled_payments = payments_df[payments_df["settlement_id"].notna()].copy()
    pay_agg = (
        settled_payments.groupby("settlement_id")
        .agg(
            gross_payment_amount=("amount", "sum"),
            payment_fee=("fee", "sum"),
            payment_tax=("tax", "sum"),
            net_payment_amount=("net_amount", "sum"),
            settled_payment_count=("payment_id", "count"),
        )
        .reset_index()
    )

    # 2. Identify pre-settlement refund deductions
    # Pre-settlement partial refunds are deducted directly from the parent settlement payout
    settled_payment_subset = settled_payments[["payment_id", "settlement_id", "status"]].rename(
        columns={"status": "payment_status"}
    )
    refunds_merged = refunds_df.merge(settled_payment_subset, on="payment_id", how="inner")
    pre_settle_refunds = refunds_merged[refunds_merged["payment_status"] == "partially_refunded"]
    
    ref_agg = (
        pre_settle_refunds.groupby("settlement_id")
        .agg(
            pre_settle_refund_amount=("amount", "sum"),
            pre_settle_refund_count=("refund_id", "count"),
            pre_settle_refund_ids=("refund_id", lambda x: ";".join(x.astype(str))),
        )
        .reset_index()
    )

    # 3. Aggregate adjustments per settlement
    adj_agg = (
        adjustments_df.groupby("settlement_id")
        .agg(
            adjustment_amount=("amount", "sum"),
            adjustment_count=("adjustment_id", "count"),
            adjustment_types=("type", lambda x: ";".join(x.astype(str))),
            adjustment_ids=("adjustment_id", lambda x: ";".join(x.astype(str))),
        )
        .reset_index()
    )

    return pay_agg, ref_agg, adj_agg


def link_bank_transactions_to_settlements(
    settlements_df: pd.DataFrame, bank_txns_df: pd.DataFrame
) -> pd.DataFrame:
    """
    Link bank transactions to settlements by UTR with narration fallback for duplicate credits.
    """
    bank_matches = []
    
    for _, b_row in bank_txns_df.iterrows():
        b_utr = str(b_row["utr"])
        narration = str(b_row.get("narration", ""))
        
        # Primary match: exact UTR equality
        matched_s = settlements_df[settlements_df["settlement_utr"] == b_utr]
        
        # Secondary match: check if narration references any settlement UTR
        if matched_s.empty and narration:
            for _, s_row in settlements_df.iterrows():
                s_utr = str(s_row["settlement_utr"])
                if s_utr in narration:
                    matched_s = settlements_df[settlements_df["settlement_id"] == s_row["settlement_id"]]
                    break

        if not matched_s.empty:
            bank_matches.append({
                "bank_txn_id": b_row["bank_txn_id"],
                "settlement_id": matched_s.iloc[0]["settlement_id"],
                "bank_amount": b_row["amount"],
                "value_date": b_row["value_date"],
                "narration": narration,
            })
        else:
            bank_matches.append({
                "bank_txn_id": b_row["bank_txn_id"],
                "settlement_id": None,
                "bank_amount": b_row["amount"],
                "value_date": b_row["value_date"],
                "narration": narration,
            })

    bank_df = pd.DataFrame(bank_matches)
    
    bank_agg = (
        bank_df.groupby("settlement_id")
        .agg(
            bank_credited_amount=("bank_amount", "sum"),
            bank_txn_count=("bank_txn_id", "count"),
            bank_txn_ids=("bank_txn_id", lambda x: ";".join(x.astype(str))),
            bank_value_dates=("value_date", lambda x: ";".join(x.astype(str))),
            bank_narrations=("narration", lambda x: " | ".join(x.astype(str))),
        )
        .reset_index()
    )
    
    return bank_agg


def run_reconciliation(data_dir: str | Path = "./data") -> Tuple[pd.DataFrame, pd.DataFrame, Dict]:
    """
    Execute deterministic settlement-level reconciliation across all 5 datasets.
    
    Returns:
        reconciliation_results_df: All settlement records with full audit and classification.
        exceptions_df: Only UNRESOLVED records requiring finance investigation.
        metrics: Batch-level summary statistics dictionary.
    """
    data = load_datasets(data_dir)
    settlements = data["settlements"]
    payments = data["payments"]
    refunds = data["refunds"]
    adjustments = data["adjustments"]
    bank_txns = data["bank_transactions"]

    # 1. Aggregate payments, refunds, and adjustments
    pay_agg, ref_agg, adj_agg = aggregate_payments_and_refunds(payments, refunds, adjustments)

    # 2. Link bank transactions
    bank_agg = link_bank_transactions_to_settlements(settlements, bank_txns)

    # 3. Master Merge
    recon = settlements.copy()
    recon = recon.merge(pay_agg, on="settlement_id", how="left")
    recon = recon.merge(ref_agg, on="settlement_id", how="left")
    recon = recon.merge(adj_agg, on="settlement_id", how="left")
    recon = recon.merge(bank_agg, on="settlement_id", how="left")

    # Fill defaults for non-null arithmetic
    recon["gross_payment_amount"] = recon["gross_payment_amount"].fillna(0).astype(int)
    recon["payment_fee"] = recon["payment_fee"].fillna(0).astype(int)
    recon["payment_tax"] = recon["payment_tax"].fillna(0).astype(int)
    recon["net_payment_amount"] = recon["net_payment_amount"].fillna(0).astype(int)
    recon["settled_payment_count"] = recon["settled_payment_count"].fillna(0).astype(int)
    
    recon["pre_settle_refund_amount"] = recon["pre_settle_refund_amount"].fillna(0).astype(int)
    recon["pre_settle_refund_count"] = recon["pre_settle_refund_count"].fillna(0).astype(int)
    recon["pre_settle_refund_ids"] = recon["pre_settle_refund_ids"].fillna("")
    
    recon["adjustment_amount"] = recon["adjustment_amount"].fillna(0).astype(int)
    recon["adjustment_count"] = recon["adjustment_count"].fillna(0).astype(int)
    recon["adjustment_types"] = recon["adjustment_types"].fillna("")
    recon["adjustment_ids"] = recon["adjustment_ids"].fillna("")
    
    recon["bank_credited_amount"] = recon["bank_credited_amount"].fillna(0).astype(int)
    recon["bank_txn_count"] = recon["bank_txn_count"].fillna(0).astype(int)
    recon["bank_txn_ids"] = recon["bank_txn_ids"].fillna("")
    recon["bank_value_dates"] = recon["bank_value_dates"].fillna("")
    recon["bank_narrations"] = recon["bank_narrations"].fillna("")

    # Rename settlement amount column for clarity
    recon = recon.rename(columns={"amount": "settlement_amount"})

    # Calculate expected payout
    recon["expected_settlement_amount"] = (
        recon["net_payment_amount"]
        - recon["pre_settle_refund_amount"]
        + recon["adjustment_amount"]
    )

    # Discrepancy calculations
    recon["calc_vs_settlement_diff"] = (
        recon["expected_settlement_amount"] - recon["settlement_amount"]
    )
    recon["bank_vs_expected_diff"] = (
        recon["bank_credited_amount"] - recon["expected_settlement_amount"]
    )

    # Calculate timing delay in days (audit evidence)
    def calculate_delay(row):
        if not row["bank_value_dates"] or pd.isna(row["settled_at"]):
            return 0
        s_date = pd.to_datetime(row["settled_at"]).date()
        b_date = pd.to_datetime(str(row["bank_value_dates"]).split(";")[0]).date()
        return (b_date - s_date).days

    recon["timing_delay_days"] = recon.apply(calculate_delay, axis=1)

    # Deterministic Classification Rules
    classifications = []
    reasons = []
    action_items = []

    for _, row in recon.iterrows():
        s_status = row["status"]
        s_amt = row["settlement_amount"]
        exp_amt = row["expected_settlement_amount"]
        bank_amt = row["bank_credited_amount"]
        b_count = row["bank_txn_count"]
        delay = row["timing_delay_days"]
        has_ref = row["pre_settle_refund_amount"] > 0
        has_adj = row["adjustment_amount"] != 0
        diff = bank_amt - exp_amt

        if s_status == "failed":
            if bank_amt == 0:
                classifications.append("EXPLAINED")
                reasons.append("Settlement failed at gateway, explains zero bank payout")
                action_items.append("None (Expected zero payout due to settlement failure)")
            else:
                classifications.append("UNRESOLVED")
                reasons.append(f"Settlement failed but received unexpected bank credit of {format_inr(bank_amt)}")
                action_items.append("Finance investigation: Verify unexpected bank credit against failed settlement")

        elif b_count == 0:
            classifications.append("UNRESOLVED")
            reasons.append(f"Missing bank transaction: processed settlement of {format_inr(s_amt)} not credited in bank")
            action_items.append("Bank Ops: Contact bank partner with settlement UTR to trace missing credit")

        elif b_count > 1:
            classifications.append("UNRESOLVED")
            reasons.append(
                f"Duplicate bank credit: {b_count} bank credits received totaling {format_inr(bank_amt)} "
                f"(expected {format_inr(exp_amt)}, excess credit of {format_inr(diff)})"
            )
            action_items.append("Treasury: Initiate bank clawback/reversal for duplicate settlement credit")

        elif exp_amt == s_amt == bank_amt:
            classifications.append("MATCH")
            notes = []
            if delay > 0:
                notes.append(f"{delay}d timing difference")
            if has_ref:
                notes.append(f"refund deduction {format_inr(row['pre_settle_refund_amount'])}")
            if has_adj:
                notes.append(f"adjustment {format_inr(row['adjustment_amount'])} ({row['adjustment_types']})")
            
            reasons.append("Financial records fully reconciled" + (f" [{', '.join(notes)}]" if notes else ""))
            action_items.append("None (Fully Reconciled)")

        else:
            # Non-zero difference between expected payout and bank credited amount
            classifications.append("UNRESOLVED")
            if "TDS" in str(row["bank_narrations"]).upper():
                reasons.append(
                    f"Bank TDS deduction shortfall of {format_inr(abs(diff))} without corresponding internal adjustment record"
                )
                action_items.append("Tax/Finance: Post TDS adjustment entry to clear bank variance")
            else:
                reasons.append(
                    f"Bank amount discrepancy: expected {format_inr(exp_amt)}, received {format_inr(bank_amt)} "
                    f"(variance: {format_inr(diff)})"
                )
                action_items.append("Audit: Investigate bank debit/credit variance with partner bank")

    recon["classification"] = classifications
    recon["reconciliation_reason"] = reasons
    recon["action_required"] = action_items

    # Filter exceptions to ONLY UNRESOLVED cases as per specification
    exceptions_df = recon[recon["classification"] == "UNRESOLVED"].copy()
    
    # Order columns logically for clean output reporting
    column_order = [
        "settlement_id",
        "settlement_utr",
        "status",
        "type",
        "settlement_amount",
        "expected_settlement_amount",
        "bank_credited_amount",
        "bank_vs_expected_diff",
        "classification",
        "reconciliation_reason",
        "action_required",
        "timing_delay_days",
        "settled_payment_count",
        "gross_payment_amount",
        "payment_fee",
        "payment_tax",
        "net_payment_amount",
        "pre_settle_refund_amount",
        "pre_settle_refund_count",
        "pre_settle_refund_ids",
        "adjustment_amount",
        "adjustment_count",
        "adjustment_types",
        "adjustment_ids",
        "bank_txn_count",
        "bank_txn_ids",
        "bank_value_dates",
        "bank_narrations",
        "created_at",
        "settled_at",
    ]
    recon = recon[column_order]

    exception_columns = [
        "settlement_id",
        "settlement_utr",
        "status",
        "type",
        "expected_settlement_amount",
        "settlement_amount",
        "bank_credited_amount",
        "bank_vs_expected_diff",
        "classification",
        "reconciliation_reason",
        "action_required",
        "bank_txn_count",
        "bank_txn_ids",
        "bank_value_dates",
        "bank_narrations",
        "settled_at",
    ]
    exceptions_df = exceptions_df[exception_columns]

    # Compute batch-level metrics
    total_settlements = len(recon)
    match_count = (recon["classification"] == "MATCH").sum()
    explained_count = (recon["classification"] == "EXPLAINED").sum()
    unresolved_count = (recon["classification"] == "UNRESOLVED").sum()
    
    metrics = {
        "total_settlements": total_settlements,
        "total_payments": len(payments),
        "total_refunds": len(refunds),
        "total_adjustments": len(adjustments),
        "total_bank_transactions": len(bank_txns),
        "match_count": match_count,
        "explained_count": explained_count,
        "unresolved_count": unresolved_count,
        "match_rate_pct": (match_count / total_settlements) * 100.0,
        "explained_rate_pct": (explained_count / total_settlements) * 100.0,
        "unresolved_rate_pct": (unresolved_count / total_settlements) * 100.0,
        "total_settlement_volume_paise": int(settlements["amount"].sum()),
        "total_bank_credited_volume_paise": int(bank_txns["amount"].sum()),
    }

    return recon, exceptions_df, metrics


def save_reports(
    recon_df: pd.DataFrame, exceptions_df: pd.DataFrame, output_dir: str | Path = "./outputs"
) -> Tuple[Path, Path]:
    """Save reconciliation results and exceptions to CSV files."""
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    results_file = out_path / "reconciliation_results.csv"
    exceptions_file = out_path / "exceptions.csv"

    recon_df.to_csv(results_file, index=False)
    exceptions_df.to_csv(exceptions_file, index=False)

    return results_file, exceptions_file


def print_batch_metrics(metrics: Dict, exceptions_df: pd.DataFrame) -> None:
    """Print clean, formatted batch-level reconciliation metrics."""
    print("=" * 80)
    print("           RAZORPAY FINANCIAL RECONCILIATION ENGINE - SUMMARY")
    print("=" * 80)
    print(f"Total Settlements Evaluated       : {metrics['total_settlements']:>6}")
    print(f"Total Payments Ingested           : {metrics['total_payments']:>6}")
    print(f"Total Refunds Ingested            : {metrics['total_refunds']:>6}")
    print(f"Total Adjustments Ingested        : {metrics['total_adjustments']:>6}")
    print(f"Total Bank Transactions Ingested  : {metrics['total_bank_transactions']:>6}")
    print("-" * 80)
    print(f"Total Settlement Batch Amount     : {format_inr(metrics['total_settlement_volume_paise']):>22}")
    print(f"Total Bank Credited Amount        : {format_inr(metrics['total_bank_credited_volume_paise']):>22}")
    print("=" * 80)
    print("                     CLASSIFICATION BREAKDOWN")
    print("=" * 80)
    print(f" MATCH (Fully Reconciled)         : {metrics['match_count']:>4} ({metrics['match_rate_pct']:.1f}%)")
    print(f" EXPLAINED (Gateway Failures/etc.): {metrics['explained_count']:>4} ({metrics['explained_rate_pct']:.1f}%)")
    print(f" UNRESOLVED (Action Required)     : {metrics['unresolved_count']:>4} ({metrics['unresolved_rate_pct']:.1f}%)")
    print("-" * 80)
    print(f" Auto-Resolved Success Rate       : {metrics['match_rate_pct'] + metrics['explained_rate_pct']:.1f}%")
    print("=" * 80)
    print(f" UNRESOLVED EXCEPTIONS REQUIRING ATTENTION ({len(exceptions_df)} items):")
    print("-" * 80)
    for idx, (_, exc) in enumerate(exceptions_df.iterrows(), 1):
        print(f"{idx}. [{exc['settlement_id']}] UTR: {exc['settlement_utr']}")
        print(f"   Amount Expected : {format_inr(exc['expected_settlement_amount'])} | Bank Received: {format_inr(exc['bank_credited_amount'])}")
        print(f"   Reason          : {exc['reconciliation_reason']}")
        print(f"   Action Required : {exc['action_required']}")
        print()
    print("=" * 80)


if __name__ == "__main__":
    current_dir = Path(__file__).resolve().parent
    data_path = current_dir / "data"
    output_path = current_dir / "outputs"
    
    print("Executing Reconciliation Engine standalone...")
    recon_df, exceptions_df, metrics = run_reconciliation(data_dir=data_path)
    res_file, exc_file = save_reports(recon_df, exceptions_df, output_dir=output_path)
    print(f"Results saved to: {res_file}")
    print(f"Exceptions saved to: {exc_file}\n")
    print_batch_metrics(metrics, exceptions_df)
