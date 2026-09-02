"""
Main execution script for Razorpay-style financial reconciliation engine.
"""

import sys
from pathlib import Path

# Add backend directory to sys.path if needed
current_dir = Path(__file__).resolve().parent
if str(current_dir) not in sys.path:
    sys.path.insert(0, str(current_dir))

from reconciliation import run_reconciliation, save_reports, print_batch_metrics
from agent import GrokReconciliationAgent


def main():
    data_dir = current_dir / "data"
    output_dir = current_dir / "outputs"

    # If --ask or --interactive passed in CLI arguments, launch the Q&A Agent
    args = sys.argv[1:]
    if args and ("--ask" in args or "-a" in args or "--interactive" in args or "-i" in args):
        agent = GrokReconciliationAgent()
        if "--ask" in args or "-a" in args:
            idx = args.index("--ask") if "--ask" in args else args.index("-a")
            query = " ".join(args[idx + 1:])
            print(f"\nQuestion: {query}\n")
            print(agent.ask(query))
        else:
            print("=" * 80)
            print("        RAZORPAY RECONCILIATION Q&A AGENT (Interactive Mode)")
            print("=" * 80)
            while True:
                try:
                    q = input("\nUser Question > ").strip()
                    if not q or q.lower() in ["exit", "quit", "q"]:
                        print("Exiting. Goodbye!")
                        break
                    print("\n" + agent.ask(q))
                except (KeyboardInterrupt, EOFError):
                    break
        return

    print("Running deterministic financial reconciliation engine...")
    recon_df, exceptions_df, metrics = run_reconciliation(data_dir=data_dir)

    results_path, exceptions_path = save_reports(
        recon_df=recon_df, exceptions_df=exceptions_df, output_dir=output_dir
    )

    print(f"Generated reconciliation results at: {results_path}")
    print(f"Generated exceptions report at: {exceptions_path}\n")

    print_batch_metrics(metrics, exceptions_df)


if __name__ == "__main__":
    main()