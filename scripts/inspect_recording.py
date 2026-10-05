"""Headless Maxwell inspection entry point."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.recording_inspection import inspect_recording
import yaml


def main() -> None:
    """Parse paths and run a non-overwriting inspection."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--config', required=True, type=Path)
    args = parser.parse_args()
    with args.config.open(encoding='utf-8') as handle:
        config = yaml.safe_load(handle)
    inspect_recording(args.input, args.output, config)


if __name__ == '__main__':
    main()
