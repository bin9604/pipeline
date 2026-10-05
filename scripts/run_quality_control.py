"""Postprocess an existing pipeline sorting into a fresh independent QC run."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import spikeinterface as si
from src.pipeline import finish_sorting
from src.utils import load_config, run_context, write_json


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ('source','output','config'): parser.add_argument('--'+flag, required=True, type=Path)
    args = parser.parse_args()
    config = load_config(args.config)
    if not config['metrics']['definitions_acknowledged']: raise ValueError('Metric definitions must be acknowledged')
    recording = si.load(args.source/'metadata/filtered_recording.json')
    sorting = si.load(args.source/'sorting_raw')
    mapping = json.loads((args.source/'metadata/time_mapping.json').read_text(encoding='utf-8'))
    with run_context(args.output, [Path(m['input_file']) for m in mapping], config, 'postprocessing') as manifest:
        manifest['source_run'] = str(args.source.resolve())
        recording.dump_to_json(args.output/'metadata/filtered_recording.json')
        write_json(args.output/'metadata/time_mapping.json', mapping)
        finish_sorting(sorting, recording, mapping, args.output, config, manifest)


if __name__ == '__main__': main()
