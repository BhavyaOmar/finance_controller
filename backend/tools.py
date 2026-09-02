"""
Deterministic Reconciliation & Financial Data Query Tools
---------------------------------------------------------
Pure data retrieval and inspection tools grounded strictly in reconciliation
results and source CSVs. Zero financial calculations or facts are hallucinated.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional
import pandas as pd

# Load files relative to backend root
CURRENT_DIR = Path(__file__).resolve().parent
DATA_DIR = CURRENT_DIR / "data"
OUTPUT_DIR = CURRENT_DIR / "outputs"


def format_inr(paise: Any) -> str:
    """Format paise to readable INR string."""
    try:
        if pd.isna(paise) or paise is None:
            return "INR 0.00"
        return f"INR {float(paise) / 100.0:,.2f}"
    except Exception:
        return str(paise)


class ReconciliationDataStore:
    """In-memory cache for CSV datasets and reconciliation outputs."""
    _instance = None

    def __init__(self):
        self.payments_df = pd.read_csv(DATA_DIR / "payments.csv")
        self.refunds_df = pd.read_csv(DATA_DIR / "refunds.csv")
        self.adjustments_df = pd.read_csv(DATA_DIR / "adjustments.csv")
        self.settlements_df = pd.read_csv(DATA_DIR / "settlements.csv")
        self.bank_txns_df = pd.read_csv(DATA_DIR / "bank_transactions.csv")
        
        # Load reconciliation results
        results_path = OUTPUT_DIR / "reconciliation_results.csv"
        exceptions_path = OUTPUT_DIR / "exceptions.csv"
        
        if not results_path.exists() or not exceptions_path.exists():
            from reconciliation import run_reconciliation, save_reports
            recon_df, exc_df, _ = run_reconciliation(data_dir=DATA_DIR)
            save_reports(recon_df, exc_df, output_dir=OUTPUT_DIR)
            
        self.recon_results_df = pd.read_csv(results_path)
        self.exceptions_df = pd.read_csv(exceptions_path)

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance


def get_settlement(identifier: str) -> Dict[str, Any]:
    """
    Lookup a settlement by its Settlement ID (e.g. 'setl_z0FgrLsN1He1Ns')
    or Settlement UTR (e.g. 'ICIC17942817934065').
    
    Returns complete reconciliation status, financial components, linked records,
    discrepancies, and recommended action.
    """
    store = ReconciliationDataStore.get_instance()
    ident = str(identifier).strip()

    # Search in reconciliation results by settlement_id or settlement_utr
    match = store.recon_results_df[
        (store.recon_results_df["settlement_id"] == ident)
        | (store.recon_results_df["settlement_utr"] == ident)
    ]

    if match.empty:
        return {
            "found": False,
            "query": identifier,
            "message": f"Settlement with ID or UTR '{identifier}' was not found in the dataset.",
        }

    row = match.iloc[0].to_dict()

    # Find associated payments
    sid = row["settlement_id"]
    linked_pay = store.payments_df[store.payments_df["settlement_id"] == sid]
    linked_pay_list = linked_pay[
        ["payment_id", "order_id", "amount", "net_amount", "status", "notes"]
    ].to_dict(orient="records")

    # Find associated adjustments
    linked_adj = store.adjustments_df[store.adjustments_df["settlement_id"] == sid]
    linked_adj_list = linked_adj[
        ["adjustment_id", "payment_id", "type", "amount", "description"]
    ].to_dict(orient="records")

    # Find associated bank transactions
    s_utr = row["settlement_utr"]
    linked_bank = store.bank_txns_df[
        (store.bank_txns_df["utr"] == s_utr)
        | (store.bank_txns_df["narration"].str.contains(s_utr, na=False))
    ]
    linked_bank_list = linked_bank[
        ["bank_txn_id", "utr", "amount", "value_date", "narration"]
    ].to_dict(orient="records")

    return {
        "found": True,
        "settlement_id": row["settlement_id"],
        "settlement_utr": row["settlement_utr"],
        "classification": row["classification"],
        "reconciliation_reason": row["reconciliation_reason"],
        "action_required": row["action_required"],
        "settlement_status": row["status"],
        "settlement_type": row["type"],
        "created_at": row["created_at"],
        "settled_at": row["settled_at"],
        "timing_delay_days": int(row.get("timing_delay_days", 0)),
        "financial_summary": {
            "settlement_amount": format_inr(row["settlement_amount"]),
            "expected_settlement_amount": format_inr(row["expected_settlement_amount"]),
            "bank_credited_amount": format_inr(row["bank_credited_amount"]),
            "variance_diff": format_inr(row["bank_vs_expected_diff"]),
            "raw_settlement_amount_paise": int(row["settlement_amount"]),
            "raw_expected_amount_paise": int(row["expected_settlement_amount"]),
            "raw_bank_amount_paise": int(row["bank_credited_amount"]),
            "raw_diff_paise": int(row["bank_vs_expected_diff"]),
        },
        "components_breakdown": {
            "gross_payment_amount": format_inr(row["gross_payment_amount"]),
            "total_fee": format_inr(row["payment_fee"]),
            "total_tax": format_inr(row["payment_tax"]),
            "net_payment_amount": format_inr(row["net_payment_amount"]),
            "pre_settle_refund_amount": format_inr(row["pre_settle_refund_amount"]),
            "pre_settle_refund_count": int(row["pre_settle_refund_count"]),
            "adjustment_amount": format_inr(row["adjustment_amount"]),
            "adjustment_count": int(row["adjustment_count"]),
            "adjustment_types": "" if pd.isna(row.get("adjustment_types")) else str(row.get("adjustment_types")),
        },
        "linked_payments": linked_pay_list,
        "linked_adjustments": linked_adj_list,
        "linked_bank_transactions": linked_bank_list,
    }


def get_payment(identifier: str) -> Dict[str, Any]:
    """
    Lookup a payment record by Payment ID (e.g. 'pay_BkDa9U4UqGWlG6')
    or Order ID (e.g. 'order_g3Ot1OGMmjxWkI').
    
    Returns payment status, fee/tax breakdown, refund status, notes, and settlement link.
    """
    store = ReconciliationDataStore.get_instance()
    ident = str(identifier).strip()

    match = store.payments_df[
        (store.payments_df["payment_id"] == ident)
        | (store.payments_df["order_id"] == ident)
    ]

    if match.empty:
        return {
            "found": False,
            "query": identifier,
            "message": f"Payment with ID or Order ID '{identifier}' was not found in the dataset.",
        }

    records = []
    for _, p_row in match.iterrows():
        pid = p_row["payment_id"]
        
        # Check if there are refunds for this payment
        refund_match = store.refunds_df[store.refunds_df["payment_id"] == pid]
        refunds_list = refund_match[
            ["refund_id", "amount", "status", "speed", "created_at", "notes"]
        ].to_dict(orient="records")

        # Check if there are adjustments for this payment
        adj_match = store.adjustments_df[store.adjustments_df["payment_id"] == pid]
        adj_list = adj_match[
            ["adjustment_id", "settlement_id", "type", "amount", "description"]
        ].to_dict(orient="records")

        records.append({
            "payment_id": p_row["payment_id"],
            "order_id": p_row["order_id"],
            "status": p_row["status"],
            "method": p_row["method"],
            "currency": p_row["currency"],
            "created_at": p_row["created_at"],
            "captured_at": str(p_row.get("captured_at", "")),
            "settlement_id": str(p_row.get("settlement_id", "")),
            "notes": str(p_row.get("notes", "")) if pd.notna(p_row.get("notes")) else None,
            "financial_breakdown": {
                "gross_amount": format_inr(p_row["amount"]),
                "fee": format_inr(p_row["fee"]),
                "tax": format_inr(p_row["tax"]),
                "net_amount": format_inr(p_row["net_amount"]),
                "raw_gross_amount_paise": int(p_row["amount"]),
                "raw_net_amount_paise": int(p_row["net_amount"]),
            },
            "linked_refunds": refunds_list,
            "linked_adjustments": adj_list,
        })

    return {
        "found": True,
        "query": identifier,
        "record_count": len(records),
        "payments": records,
    }


def get_refund(refund_id: str) -> Dict[str, Any]:
    """Lookup a refund by Refund ID (e.g. 'rfnd_kPAzLmp1weo98r')."""
    store = ReconciliationDataStore.get_instance()
    ident = str(refund_id).strip()

    match = store.refunds_df[store.refunds_df["refund_id"] == ident]
    if match.empty:
        return {
            "found": False,
            "refund_id": refund_id,
            "message": f"Refund ID '{refund_id}' was not found in the dataset.",
        }

    row = match.iloc[0].to_dict()
    pid = row["payment_id"]
    pay_info = store.payments_df[store.payments_df["payment_id"] == pid]
    
    pay_dict = {}
    if not pay_info.empty:
        p_row = pay_info.iloc[0]
        pay_dict = {
            "payment_id": p_row["payment_id"],
            "order_id": p_row["order_id"],
            "payment_status": p_row["status"],
            "settlement_id": str(p_row.get("settlement_id", "")),
            "payment_gross_amount": format_inr(p_row["amount"]),
            "payment_net_amount": format_inr(p_row["net_amount"]),
        }

    return {
        "found": True,
        "refund_id": row["refund_id"],
        "payment_id": row["payment_id"],
        "amount": format_inr(row["amount"]),
        "raw_amount_paise": int(row["amount"]),
        "status": row["status"],
        "speed": row["speed"],
        "created_at": row["created_at"],
        "notes": str(row.get("notes", "")),
        "associated_payment": pay_dict,
    }


def get_adjustment(adjustment_id: str) -> Dict[str, Any]:
    """Lookup an adjustment by Adjustment ID (e.g. 'adj_hGX1XIDGhEQNrY')."""
    store = ReconciliationDataStore.get_instance()
    ident = str(adjustment_id).strip()

    match = store.adjustments_df[store.adjustments_df["adjustment_id"] == ident]
    if match.empty:
        return {
            "found": False,
            "adjustment_id": adjustment_id,
            "message": f"Adjustment ID '{adjustment_id}' was not found in the dataset.",
        }

    row = match.iloc[0].to_dict()
    return {
        "found": True,
        "adjustment_id": row["adjustment_id"],
        "settlement_id": row["settlement_id"],
        "payment_id": row["payment_id"],
        "type": row["type"],
        "amount": format_inr(row["amount"]),
        "raw_amount_paise": int(row["amount"]),
        "description": row["description"],
        "created_at": row["created_at"],
    }


def get_bank_transaction(identifier: str) -> Dict[str, Any]:
    """
    Lookup a bank transaction by Bank Transaction ID (e.g. 'bnktxn_6YGTEIugZ0MoUw')
    or UTR (e.g. 'SBIN46553585125389').
    """
    store = ReconciliationDataStore.get_instance()
    ident = str(identifier).strip()

    match = store.bank_txns_df[
        (store.bank_txns_df["bank_txn_id"] == ident)
        | (store.bank_txns_df["utr"] == ident)
    ]

    if match.empty:
        return {
            "found": False,
            "query": identifier,
            "message": f"Bank transaction with ID or UTR '{identifier}' was not found in the dataset.",
        }

    records = []
    for _, b_row in match.iterrows():
        b_utr = b_row["utr"]
        narration = str(b_row.get("narration", ""))
        
        # Link to settlement
        s_match = store.settlements_df[store.settlements_df["settlement_utr"] == b_utr]
        if s_match.empty and narration:
            for _, s in store.settlements_df.iterrows():
                if str(s["settlement_utr"]) in narration:
                    s_match = store.settlements_df[store.settlements_df["settlement_id"] == s["settlement_id"]]
                    break

        s_info = {}
        if not s_match.empty:
            s_row = s_match.iloc[0]
            s_info = {
                "settlement_id": s_row["settlement_id"],
                "settlement_utr": s_row["settlement_utr"],
                "settlement_amount": format_inr(s_row["amount"]),
                "status": s_row["status"],
            }

        records.append({
            "bank_txn_id": b_row["bank_txn_id"],
            "utr": b_row["utr"],
            "amount": format_inr(b_row["amount"]),
            "raw_amount_paise": int(b_row["amount"]),
            "type": b_row["type"],
            "value_date": b_row["value_date"],
            "narration": b_row["narration"],
            "account_number": b_row["account_number"],
            "linked_settlement": s_info,
        })

    return {
        "found": True,
        "query": identifier,
        "record_count": len(records),
        "transactions": records,
    }


def get_exceptions(settlement_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Get all unresolved reconciliation exceptions, or a specific exception by Settlement ID.
    """
    store = ReconciliationDataStore.get_instance()
    
    if settlement_id:
        sid = str(settlement_id).strip()
        match = store.exceptions_df[store.exceptions_df["settlement_id"] == sid]
        if match.empty:
            return {
                "found": False,
                "settlement_id": settlement_id,
                "message": f"No unresolved exception found for settlement '{settlement_id}'. It may be a MATCH or EXPLAINED.",
            }
        row = match.iloc[0].to_dict()
        return {
            "found": True,
            "settlement_id": row["settlement_id"],
            "settlement_utr": row["settlement_utr"],
            "classification": row["classification"],
            "reconciliation_reason": row["reconciliation_reason"],
            "action_required": row["action_required"],
            "expected_amount": format_inr(row["expected_settlement_amount"]),
            "settlement_amount": format_inr(row["settlement_amount"]),
            "bank_credited_amount": format_inr(row["bank_credited_amount"]),
            "variance": format_inr(row["bank_vs_expected_diff"]),
            "bank_txn_count": int(row.get("bank_txn_count", 0)),
            "bank_txn_ids": str(row.get("bank_txn_ids", "")),
            "bank_narrations": str(row.get("bank_narrations", "")),
            "settled_at": str(row.get("settled_at", "")),
        }

    # Return list of all exceptions
    exceptions_list = []
    for _, row in store.exceptions_df.iterrows():
        exceptions_list.append({
            "settlement_id": row["settlement_id"],
            "settlement_utr": row["settlement_utr"],
            "expected_amount": format_inr(row["expected_settlement_amount"]),
            "settlement_amount": format_inr(row["settlement_amount"]),
            "bank_credited_amount": format_inr(row["bank_credited_amount"]),
            "variance": format_inr(row["bank_vs_expected_diff"]),
            "reason": row["reconciliation_reason"],
            "action_required": row["action_required"],
        })

    return {
        "found": True,
        "total_unresolved_exceptions": len(exceptions_list),
        "exceptions": exceptions_list,
    }


def get_metrics() -> Dict[str, Any]:
    """
    Get batch-level reconciliation metrics and high-level summary statistics.
    """
    store = ReconciliationDataStore.get_instance()
    recon_df = store.recon_results_df
    
    total_settlements = len(recon_df)
    match_count = int((recon_df["classification"] == "MATCH").sum())
    explained_count = int((recon_df["classification"] == "EXPLAINED").sum())
    unresolved_count = int((recon_df["classification"] == "UNRESOLVED").sum())

    total_setl_paise = int(recon_df["settlement_amount"].sum())
    total_bank_paise = int(store.bank_txns_df["amount"].sum())

    return {
        "batch_overview": {
            "total_settlements_evaluated": total_settlements,
            "total_payments_ingested": len(store.payments_df),
            "total_refunds_ingested": len(store.refunds_df),
            "total_adjustments_ingested": len(store.adjustments_df),
            "total_bank_transactions_ingested": len(store.bank_txns_df),
            "total_settlement_volume": format_inr(total_setl_paise),
            "total_bank_credited_volume": format_inr(total_bank_paise),
        },
        "classification_summary": {
            "match_count": match_count,
            "match_rate_pct": round((match_count / total_settlements) * 100.0, 1),
            "explained_count": explained_count,
            "explained_rate_pct": round((explained_count / total_settlements) * 100.0, 1),
            "unresolved_count": unresolved_count,
            "unresolved_rate_pct": round((unresolved_count / total_settlements) * 100.0, 1),
            "auto_resolved_success_rate_pct": round(
                ((match_count + explained_count) / total_settlements) * 100.0, 1
            ),
        },
    }


# Tool definitions for Grok (xAI) function calling
TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "get_settlement",
            "description": "Get detailed reconciliation data for a settlement by settlement_id (e.g. 'setl_z0FgrLsN1He1Ns') or settlement_utr. Returns expected payout, bank amount, status, discrepancy, and linked records.",
            "parameters": {
                "type": "object",
                "properties": {
                    "identifier": {
                        "type": "string",
                        "description": "The settlement_id (starts with 'setl_') or settlement_utr",
                    }
                },
                "required": ["identifier"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_payment",
            "description": "Get payment details by payment_id (starts with 'pay_') or order_id (starts with 'order_'). Returns amounts, fee/tax, refund status, notes, and settlement link.",
            "parameters": {
                "type": "object",
                "properties": {
                    "identifier": {
                        "type": "string",
                        "description": "The payment_id (e.g. 'pay_BkDa9U4UqGWlG6') or order_id",
                    }
                },
                "required": ["identifier"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_refund",
            "description": "Get refund details by refund_id (starts with 'rfnd_').",
            "parameters": {
                "type": "object",
                "properties": {
                    "refund_id": {
                        "type": "string",
                        "description": "The refund_id (e.g. 'rfnd_kPAzLmp1weo98r')",
                    }
                },
                "required": ["refund_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_adjustment",
            "description": "Get adjustment details by adjustment_id (starts with 'adj_').",
            "parameters": {
                "type": "object",
                "properties": {
                    "adjustment_id": {
                        "type": "string",
                        "description": "The adjustment_id (e.g. 'adj_hGX1XIDGhEQNrY')",
                    }
                },
                "required": ["adjustment_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_bank_transaction",
            "description": "Get bank transaction details by bank_txn_id (starts with 'bnktxn_') or UTR.",
            "parameters": {
                "type": "object",
                "properties": {
                    "identifier": {
                        "type": "string",
                        "description": "The bank_txn_id (e.g. 'bnktxn_6YGTEIugZ0MoUw') or UTR",
                    }
                },
                "required": ["identifier"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_exceptions",
            "description": "Get all unresolved reconciliation exceptions requiring action, or look up a specific settlement exception.",
            "parameters": {
                "type": "object",
                "properties": {
                    "settlement_id": {
                        "type": "string",
                        "description": "Optional settlement_id to filter for a specific exception",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_metrics",
            "description": "Get high-level batch reconciliation metrics, total volume, match rates, and exception counts.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
]

TOOL_FUNCTIONS = {
    "get_settlement": get_settlement,
    "get_payment": get_payment,
    "get_refund": get_refund,
    "get_adjustment": get_adjustment,
    "get_bank_transaction": get_bank_transaction,
    "get_exceptions": get_exceptions,
    "get_metrics": get_metrics,
}
