import subprocess
import sys
import argparse
import os

def run_command(cmd, step_name):
    print(f"\n{'='*60}")
    print(f"🚀 [STEP] {step_name}")
    print(f"💻 Executing: {' '.join(cmd)}")
    print(f"{'='*60}\n")
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        print(f"\n❌ Pipeline failed at step: {step_name}")
        print(f"Error details: {e}")
        sys.exit(1)

def main():
    parser = argparse.ArgumentParser(description="PaperOps Master Pipeline - Run the entire automation flow sequentially.")
    parser.add_argument("--limit", type=int, default=20, help="Limit for collect and other commands.")
    parser.add_argument("--skip-downloads", action="store_true", help="Skip the PDF download step to save time.")
    args = parser.parse_args()

    # Determine paths
    script_dir = os.path.dirname(os.path.abspath(__file__))
    paperops_script = os.path.join(script_dir, "paperops.py")

    # Sequence of pipeline commands based on PaperOps standard flow
    pipeline_steps = [
        ([sys.executable, paperops_script, "collect", "--limit", str(args.limit)], "Collect Papers"),
        ([sys.executable, paperops_script, "score"], "Score Papers"),
        ([sys.executable, paperops_script, "screen", "--limit", str(args.limit * 2)], "Screen Papers"),
        ([sys.executable, paperops_script, "gap"], "Find Research Gaps"),
    ]

    if not args.skip_downloads:
        pipeline_steps.append(([sys.executable, paperops_script, "download-pdfs", "--limit", str(args.limit)], "Download PDFs"))
    
    pipeline_steps.extend([
        ([sys.executable, paperops_script, "cards"], "Generate Paper Cards"),
        ([sys.executable, paperops_script, "render-figures"], "Render Figures"),
    ])

    print("🌟 Starting PaperOps Master Pipeline...")
    for cmd, step_name in pipeline_steps:
        run_command(cmd, step_name)

    print(f"\n{'='*60}")
    print("✅ Pipeline execution completed successfully!")
    print(f"{'='*60}\n")

if __name__ == "__main__":
    main()
