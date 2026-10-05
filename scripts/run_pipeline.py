"""CLI for one recording, separate-recording batch, or validated concatenation."""
import argparse
import logging
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.utils import load_config, write_json
from src.pipeline import run_single_recording, run_concatenated_recordings


def main() -> None:
    """Default to explicit commands; dry-run never calls a sorter."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, nargs='+', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--concatenate', action='store_true')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    config = load_config(args.config)
    if args.concatenate:
        run_concatenated_recordings(args.input, config, args.output, args.dry_run)
    elif len(args.input)==1:
        run_single_recording(args.input[0], config, args.output, args.dry_run)
    else:
        names = [path.name.removesuffix('.raw.h5') for path in args.input]
        if len(set(names)) != len(names): raise ValueError('Batch output names collide')
        args.output.mkdir(parents=True, exist_ok=False)
        statuses = []
        for path, name in zip(args.input, names):
            try:
                run_single_recording(path, config, args.output/name, args.dry_run)
                statuses.append(dict(input=str(path), status='completed'))
            except Exception as error:
                logging.exception('Recording failed: %s', path)
                statuses.append(dict(input=str(path),status='failed',error=str(error)))
            write_json(args.output/'batch_manifest.json', statuses)
        if any(s['status']=='failed' for s in statuses): raise SystemExit(1)


if __name__ == '__main__': main()
