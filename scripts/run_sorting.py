"""Explicit future single-recording sorting stage (not executed during build)."""
import argparse
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.pipeline import run_single_recording
from src.utils import load_config


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ('input','output','config'): parser.add_argument('--'+flag, required=True, type=Path)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    run_single_recording(args.input, load_config(args.config), args.output, args.dry_run, stop_after_sorting=True)


if __name__ == '__main__': main()
