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


def main():
    data_dir = current_dir / "data"
    output_dir = current_dir / "outputs"

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