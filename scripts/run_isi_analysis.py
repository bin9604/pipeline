"""Analyze curated units from an existing pipeline run using explicit conventions."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import spikeinterface as si
from src.isi_analysis import run_isi_analysis
from src.utils import load_config, run_context


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ('source','output','config'): parser.add_argument('--'+flag, required=True, type=Path)
    args = parser.parse_args()
    config = load_config(args.config)
    mapping = json.loads((args.source/'metadata/time_mapping.json').read_text(encoding='utf-8'))
    sorting = si.load(args.source/'sorting_curated')
    with run_context(args.output, [Path(m['input_file']) for m in mapping], config, 'downstream_isi') as manifest:
        manifest['source_run'] = str(args.source.resolve())
        run_isi_analysis(sorting, mapping, args.output, config)


if __name__ == '__main__': main()
