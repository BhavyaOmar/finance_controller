"""
Razorpay Financial Reconciliation Q&A Agent
-------------------------------------------
Intelligent Q&A agent powered by Grok (xAI API) for natural-language understanding
and explanation generation, backed by deterministic tool execution grounded in
reconciliation CSVs and audit results.
"""

import os
import sys
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional
import requests

# Add backend directory to sys.path
CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

# Automatically load environment variables from .env file
def _load_env():
    try:
        from dotenv import load_dotenv
        load_dotenv(CURRENT_DIR / ".env")
        load_dotenv(CURRENT_DIR.parent / ".env")
        load_dotenv()
    except Exception:
        for env_file in [CURRENT_DIR / ".env", CURRENT_DIR.parent / ".env", Path(".env")]:
            if env_file.exists() and env_file.is_file():
                try:
                    with open(env_file, "r", encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if line and not line.startswith("#") and "=" in line:
                                k, v = line.split("=", 1)
                                k = k.strip()
                                v = v.strip().strip("'\"")
                                if k and k not in os.environ:
                                    os.environ[k] = v
                except Exception:
                    pass

_load_env()

from tools import (
    TOOL_DEFINITIONS,
    TOOL_FUNCTIONS,
    get_settlement,
    get_payment,
    get_refund,
    get_adjustment,
    get_bank_transaction,
    get_exceptions,
    get_metrics,
    format_inr,
)

SYSTEM_PROMPT = """You are an expert Financial Reconciliation AI Assistant for a Razorpay-style payment aggregator.
Your task is to answer user questions about settlements, payments, refunds, adjustments, bank transactions, exceptions, and batch metrics.

STRICT CONSTRAINTS:
1. ALWAYS ground your answers in the data returned by the tools.
2. DO NOT independently compute or invent financial amounts, fees, taxes, or discrepancy numbers.
3. If an ID or record is not found in the dataset, clearly state that it was not found instead of guessing.
4. When explaining discrepancies, state the exact Expected Amount, Settlement Amount, Bank Credited Amount, variance, root cause, and recommended finance action.
5. Format monetary amounts in readable INR format (e.g. INR 4,211.58).
6. Be concise, professional, and clear.
"""


class GrokReconciliationAgent:
    """Q&A Agent using Grok (xAI) with deterministic tool execution."""

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        self.api_key = (
            api_key
            or os.getenv("GROK_API_KEY")
            or os.getenv("XAI_API_KEY")
        )
        self.model = model or os.getenv("GROK_MODEL", "grok-beta")
        self.base_url = os.getenv("GROK_BASE_URL", "https://api.x.ai/v1")

    def is_grok_configured(self) -> bool:
        """Check if Grok API is configured."""
        return bool(self.api_key)

    def _call_grok_api(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.1,
    ) -> Dict[str, Any]:
        """Make a direct HTTP request to the xAI Grok API without third-party LLM SDKs."""
        endpoint = f"{self.base_url.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        resp = requests.post(endpoint, headers=headers, json=payload, timeout=60)
        resp.raise_for_status()
        return resp.json()

    def _execute_tool(self, tool_name: str, tool_args: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a deterministic tool function safely."""
        if tool_name not in TOOL_FUNCTIONS:
            return {"error": f"Unknown tool '{tool_name}'"}
        try:
            return TOOL_FUNCTIONS[tool_name](**tool_args)
        except Exception as e:
            return {"error": f"Error executing {tool_name}: {str(e)}"}

    def _fallback_deterministic_answering(self, question: str) -> str:
        """
        Deterministic entity & intent extractor when Grok API key is not configured.
        Ensures 100% grounded, factual responses without requiring external API calls.
        """
        q_lower = question.lower()
        
        # 1. Check for settlement ID or UTR
        settle_match = re.search(r"\b(setl_[A-Za-z0-9]+)\b", question)
        utr_match = re.search(r"\b([A-Z]{4}\d{10,18})\b", question)
        
        if settle_match:
            sid = settle_match.group(1)
            data = get_settlement(sid)
            return self._format_settlement_response(data)
        elif utr_match:
            utr = utr_match.group(1)
            # Try settlement first, then bank txn
            data = get_settlement(utr)
            if data.get("found"):
                return self._format_settlement_response(data)
            b_data = get_bank_transaction(utr)
            return self._format_bank_txn_response(b_data)

        # 2. Check for payment ID or order ID
        pay_match = re.search(r"\b(pay_[A-Za-z0-9]+)\b", question)
        order_match = re.search(r"\b(order_[A-Za-z0-9]+)\b", question)
        if pay_match:
            pid = pay_match.group(1)
            data = get_payment(pid)
            return self._format_payment_response(data)
        elif order_match:
            oid = order_match.group(1)
            data = get_payment(oid)
            return self._format_payment_response(data)

        # 3. Check for refund ID
        ref_match = re.search(r"\b(rfnd_[A-Za-z0-9]+)\b", question)
        if ref_match:
            rid = ref_match.group(1)
            data = get_refund(rid)
            return self._format_refund_response(data)

        # 4. Check for adjustment ID
        adj_match = re.search(r"\b(adj_[A-Za-z0-9]+)\b", question)
        if adj_match:
            aid = adj_match.group(1)
            data = get_adjustment(aid)
            return self._format_adjustment_response(data)

        # 5. Check for bank transaction ID
        bank_match = re.search(r"\b(bnktxn_[A-Za-z0-9]+)\b", question)
        if bank_match:
            bid = bank_match.group(1)
            data = get_bank_transaction(bid)
            return self._format_bank_txn_response(data)

        # 6. Check for exceptions / unresolved issues intent
        if any(k in q_lower for k in ["exception", "unresolved", "discrepan", "shortfall", "duplicate credit", "issues", "problem"]):
            data = get_exceptions()
            return self._format_exceptions_response(data)

        # 7. Check for batch metrics / summary / match rate intent
        if any(k in q_lower for k in ["metric", "summary", "overview", "rate", "total", "stats", "volume", "batch"]):
            data = get_metrics()
            return self._format_metrics_response(data)

        return (
            "I could not identify a specific settlement ID (setl_...), payment ID (pay_...), "
            "bank transaction (bnktxn_.../UTR), exception query, or batch metric request in your question.\n\n"
            "Examples of questions you can ask:\n"
            "- 'What is the status of settlement setl_z0FgrLsN1He1Ns?'\n"
            "- 'Show me all unresolved reconciliation exceptions and action items.'\n"
            "- 'What are the overall batch metrics and match rate?'\n"
            "- 'Give me details for payment pay_TKtE0bxvRhALtY.'\n"
            "- 'Check bank transaction bnktxn_0FDQ9zYT3uCi0u.'"
        )

    def _format_settlement_response(self, data: Dict[str, Any]) -> str:
        if not data.get("found"):
            return data.get("message", "Settlement not found.")
        
        fs = data["financial_summary"]
        comp = data["components_breakdown"]
        
        adj_str = comp['adjustment_types']
        if not adj_str or adj_str == "nan":
            adj_str = "None"
            
        resp = [
            f"### Settlement: `{data['settlement_id']}` (UTR: `{data['settlement_utr']}`)",
            f"- **Classification**: `{data['classification']}`",
            f"- **Reconciliation Reason**: {data['reconciliation_reason']}",
            f"- **Action Required**: {data['action_required']}",
            f"- **Gateway Status**: `{data['settlement_status']}` | **Type**: `{data['settlement_type']}`",
            f"- **Settled Date**: `{data['settled_at']}` (Timing delay: {data['timing_delay_days']} days)",
            "",
            "**Financial Breakdown:**",
            f"- Expected Settlement Amount : {fs['expected_settlement_amount']}",
            f"- Settlement Amount Recorded  : {fs['settlement_amount']}",
            f"- Bank Credited Amount        : {fs['bank_credited_amount']}",
            f"- Variance (Bank - Expected)  : {fs['variance_diff']}",
            "",
            "**Component Accounting:**",
            f"- Net Payments ({len(data['linked_payments'])} txns) : {comp['net_payment_amount']} (Gross: {comp['gross_payment_amount']}, Fee: {comp['total_fee']}, GST: {comp['total_tax']})",
            f"- Pre-settlement Refunds ({comp['pre_settle_refund_count']})  : -{comp['pre_settle_refund_amount']}",
            f"- Adjustments ({comp['adjustment_count']})             : +{comp['adjustment_amount']} ({adj_str})",
        ]

        if data["linked_bank_transactions"]:
            resp.append("")
            resp.append(f"**Linked Bank Transactions ({len(data['linked_bank_transactions'])}):**")
            for b in data["linked_bank_transactions"]:
                resp.append(f"- `{b['bank_txn_id']}`: {format_inr(b['amount'])} on {b['value_date']} | Narration: *{b['narration']}*")

        return "\n".join(resp)

    def _format_payment_response(self, data: Dict[str, Any]) -> str:
        if not data.get("found"):
            return data.get("message", "Payment not found.")
        
        records = data["payments"]
        resp = [f"Found {len(records)} payment record(s):"]
        for p in records:
            fb = p["financial_breakdown"]
            resp.extend([
                f"\n### Payment: `{p['payment_id']}` (Order: `{p['order_id']}`)",
                f"- **Status**: `{p['status']}` | **Method**: `{p['method']}`",
                f"- **Gross Amount**: {fb['gross_amount']} (Fee: {fb['fee']}, GST: {fb['tax']}, Net: {fb['net_amount']})",
                f"- **Settlement ID**: `{p['settlement_id'] or 'Not settled / Pre-settlement refund'}`",
                f"- **Captured At**: `{p['captured_at']}`",
            ])
            if p["notes"]:
                resp.append(f"- **Notes**: *{p['notes']}*")
            if p["linked_refunds"]:
                resp.append(f"- **Refunds**: {len(p['linked_refunds'])} refund(s) attached ({', '.join(r['refund_id'] + ': ' + format_inr(r['amount']) for r in p['linked_refunds'])})")
            if p["linked_adjustments"]:
                resp.append(f"- **Adjustments**: {len(p['linked_adjustments'])} adjustment(s) ({', '.join(a['adjustment_id'] + ': ' + format_inr(a['amount']) for a in p['linked_adjustments'])})")
        return "\n".join(resp)

    def _format_refund_response(self, data: Dict[str, Any]) -> str:
        if not data.get("found"):
            return data.get("message", "Refund not found.")
        return (
            f"### Refund: `{data['refund_id']}`\n"
            f"- **Payment ID**: `{data['payment_id']}`\n"
            f"- **Refund Amount**: {data['amount']}\n"
            f"- **Status / Speed**: `{data['status']}` ({data['speed']})\n"
            f"- **Created At**: `{data['created_at']}`\n"
            f"- **Notes**: *{data['notes']}*\n"
            f"- **Associated Payment**: Gross {data['associated_payment'].get('payment_gross_amount')}, Status `{data['associated_payment'].get('payment_status')}`, Settlement `{data['associated_payment'].get('settlement_id') or 'None'}`"
        )

    def _format_adjustment_response(self, data: Dict[str, Any]) -> str:
        if not data.get("found"):
            return data.get("message", "Adjustment not found.")
        return (
            f"### Adjustment: `{data['adjustment_id']}`\n"
            f"- **Settlement ID**: `{data['settlement_id']}` | **Payment ID**: `{data['payment_id']}`\n"
            f"- **Type**: `{data['type']}`\n"
            f"- **Adjustment Amount**: {data['amount']}\n"
            f"- **Description**: *{data['description']}*\n"
            f"- **Created At**: `{data['created_at']}`"
        )

    def _format_bank_txn_response(self, data: Dict[str, Any]) -> str:
        if not data.get("found"):
            return data.get("message", "Bank transaction not found.")
        records = data["transactions"]
        resp = [f"Found {len(records)} bank transaction record(s):"]
        for b in records:
            ls = b["linked_settlement"]
            resp.extend([
                f"\n### Bank Txn: `{b['bank_txn_id']}`",
                f"- **UTR**: `{b['utr']}`",
                f"- **Amount**: {b['amount']} ({b['type']})",
                f"- **Value Date**: `{b['value_date']}`",
                f"- **Narration**: *{b['narration']}*",
                f"- **Account Number**: `{b['account_number']}`",
                f"- **Linked Settlement**: `{ls.get('settlement_id', 'None')}` (Settlement Amount: {ls.get('settlement_amount', 'N/A')}, Status: `{ls.get('status', 'N/A')}`)",
            ])
        return "\n".join(resp)

    def _format_exceptions_response(self, data: Dict[str, Any]) -> str:
        if not data.get("found"):
            return data.get("message", "No exceptions found.")
        
        excs = data["exceptions"]
        resp = [
            f"### Unresolved Reconciliation Exceptions ({len(excs)} Total)",
            "The following cases have active variances and require finance / ops action:\n",
        ]
        for idx, exc in enumerate(excs, 1):
            resp.extend([
                f"**{idx}. Settlement `{exc['settlement_id']}` (UTR: `{exc['settlement_utr']}`)**",
                f"   - Expected: {exc['expected_amount']} | Bank Received: {exc['bank_credited_amount']} (Variance: {exc['variance']})",
                f"   - **Reason**: {exc['reason']}",
                f"   - **Action Required**: {exc['action_required']}\n",
            ])
        return "\n".join(resp)

    def _format_metrics_response(self, data: Dict[str, Any]) -> str:
        bo = data["batch_overview"]
        cs = data["classification_summary"]
        return (
            "### Batch Reconciliation Metrics\n\n"
            "**Batch Overview:**\n"
            f"- Total Settlements Evaluated : {bo['total_settlements_evaluated']}\n"
            f"- Total Payments Ingested     : {bo['total_payments_ingested']}\n"
            f"- Total Refunds Ingested      : {bo['total_refunds_ingested']}\n"
            f"- Total Adjustments Ingested  : {bo['total_adjustments_ingested']}\n"
            f"- Total Bank Transactions     : {bo['total_bank_transactions_ingested']}\n"
            f"- Total Settlement Volume     : {bo['total_settlement_volume']}\n"
            f"- Total Bank Credited Volume  : {bo['total_bank_credited_volume']}\n\n"
            "**Classification & Match Rates:**\n"
            f"- **MATCH (Fully Reconciled)**  : {cs['match_count']} ({cs['match_rate_pct']}%)\n"
            f"- **EXPLAINED (Gateway Failed)**: {cs['explained_count']} ({cs['explained_rate_pct']}%)\n"
            f"- **UNRESOLVED (Exceptions)**   : {cs['unresolved_count']} ({cs['unresolved_rate_pct']}%)\n"
            f"- **Auto-Resolved Success Rate**: **{cs['auto_resolved_success_rate_pct']}%**"
        )

    def ask(self, question: str) -> str:
        """
        Main query interface.
        If Grok API key is configured, uses Grok (xAI API) with Function Calling via HTTP.
        Otherwise, falls back to deterministic grounded parser.
        """
        if not self.is_grok_configured():
            # Zero-dependency deterministic execution
            return self._fallback_deterministic_answering(question)

        # Grok LLM with Tool Calling loop
        try:
            messages: List[Dict[str, Any]] = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": question},
            ]

            first_resp = self._call_grok_api(messages, tools=TOOL_DEFINITIONS, temperature=0.1)
            choices = first_resp.get("choices", [])
            if not choices:
                return self._fallback_deterministic_answering(question)

            first_message = choices[0].get("message", {})
            tool_calls = first_message.get("tool_calls")

            # If no tools called, return model response
            if not tool_calls:
                return first_message.get("content") or self._fallback_deterministic_answering(question)

            # Process tool calls
            messages.append(first_message)
            for tool_call in tool_calls:
                fn_info = tool_call.get("function", {})
                function_name = fn_info.get("name", "")
                raw_args = fn_info.get("arguments", "{}")
                if isinstance(raw_args, str):
                    try:
                        function_args = json.loads(raw_args)
                    except Exception:
                        function_args = {}
                else:
                    function_args = raw_args or {}

                tool_output = self._execute_tool(function_name, function_args)

                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.get("id"),
                    "name": function_name,
                    "content": json.dumps(tool_output),
                })

            # Get final grounded explanation from Grok
            second_resp = self._call_grok_api(messages, temperature=0.2)
            second_choices = second_resp.get("choices", [])
            if not second_choices:
                return self._fallback_deterministic_answering(question)
            
            second_message = second_choices[0].get("message", {})
            return second_message.get("content") or self._fallback_deterministic_answering(question)

        except Exception as e:
            # On any API error, gracefully fall back to deterministic response
            fallback_ans = self._fallback_deterministic_answering(question)
            return f"{fallback_ans}\n\n*(Note: Grok API call encountered an error: {e}. Provided deterministic grounded response.)*"


def main():
    """CLI runner and interactive chat loop."""
    agent = GrokReconciliationAgent()
    status_str = "Grok API connected" if agent.is_grok_configured() else "Grok API Key not set (using deterministic grounded engine)"
    
    print("=" * 80)
    print(f"       RAZORPAY RECONCILIATION Q&A AGENT ({status_str})")
    print("=" * 80)

    if len(sys.argv) > 1:
        # One-shot command line query
        user_query = " ".join(sys.argv[1:])
        print(f"\nQuestion: {user_query}\n")
        answer = agent.ask(user_query)
        print(answer)
        print("=" * 80)
        return

    # Interactive loop
    print("Ask any question about settlements, payments, refunds, bank transactions, exceptions, or metrics.")
    print("Type 'exit' or 'quit' to stop.\n")

    while True:
        try:
            query = input("User Question > ").strip()
            if not query:
                continue
            if query.lower() in ["exit", "quit", "q"]:
                print("Exiting Reconciliation Agent. Goodbye!")
                break
            
            print("\n[Agent is processing...]\n")
            ans = agent.ask(query)
            print(ans)
            print("-" * 80 + "\n")
        except (KeyboardInterrupt, EOFError):
            print("\nExiting. Goodbye!")
            break


if __name__ == "__main__":
    main()
