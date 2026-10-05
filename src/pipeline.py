"""Single-recording, batch and explicitly validated concatenation orchestration."""
from __future__ import annotations
import csv
import logging
from pathlib import Path
import numpy as np
import pandas as pd
import spikeinterface as si
from spikeinterface.core import BaseRecording, BaseSorting
from src.io import read_recording, save_recording_metadata, validate_concatenation
from src.preprocessing import preprocess_recording
from src.quality_control import signal_qc
from src.sorting import run_kilosort2, sorter_parameters
from src.postprocessing import analyze_sorting
from src.curation import save_curation
from src.isi_analysis import run_isi_analysis
from src.visualization import plot_unit_qc
from src.utils import run_context, write_json, source_fingerprint


def export_spike_mapping(sorting: BaseSorting, mapping: list[dict], path: Path) -> None:
    """Stream each unit's spikes to CSV with original file/local frame identity."""
    with path.open('w', newline='', encoding='utf-8') as handle:
        writer = csv.writer(handle)
        writer.writerow(['unit_id','concatenated_frame','input_file','local_frame','local_time_sec'])
        for unit in sorting.unit_ids:
            spikes = sorting.get_unit_spike_train(unit)
            for origin in mapping:
                selected = spikes[(spikes>=origin['start_frame']) & (spikes<origin['end_frame'])]
                for frame in selected:
                    local = int(frame)-origin['start_frame']
                    writer.writerow([unit,int(frame),origin['input_file'],local,local/origin['fs']])


def finish_sorting(sorting: BaseSorting, recording: BaseRecording, mapping: list[dict], output: Path, config: dict, manifest: dict) -> None:
    """Save raw/curated results and run independently auditable downstream stages."""
    if sorting.get_num_segments()!=1: raise ValueError('Expected one sorter segment')
    sorting.save(folder=output / 'sorting_raw')
    export_spike_mapping(sorting, mapping, output / 'metrics/spike_source_mapping_raw.csv')
    manifest['stages_completed'].append('sorting_raw')
    if len(sorting.unit_ids):
        analyzer, table = analyze_sorting(sorting, recording, output, config)
    else:
        analyzer = None
        table = pd.DataFrame(columns=['num_spikes','firing_rate','isi_violations_ratio','snr'])
        table.index.name = 'unit_id'; table.to_csv(output / 'metrics/quality_metrics.csv')
        manifest['warnings'].append('Sorter returned zero units; analyzer and unit figures omitted.')
    curated, decisions = save_curation(sorting, table, output, config)
    manifest['stages_completed'].extend(['quality_metrics','automatic_curation'])
    manifest.update(raw_units=len(sorting.unit_ids), curated_units=len(curated.unit_ids))
    export_spike_mapping(curated, mapping, output / 'metrics/spike_source_mapping_curated.csv')
    if analyzer is not None: plot_unit_qc(analyzer, decisions, output, config)
    manifest['stages_completed'].append('qc_visualization')
    if config['isi_analysis']['conventions_acknowledged']:
        run_isi_analysis(curated, mapping, output, config)
        manifest['stages_completed'].append('downstream_isi')
    else:
        manifest['warnings'].append('ISI analysis intentionally deferred: conventions_acknowledged=false')


def _run(files: list[Path], config: dict, output: Path, concatenated: bool, dry_run: bool, stop_after_sorting: bool = False) -> Path:
    files, output = [Path(p).resolve() for p in files], output.resolve()
    with run_context(output, files, config, 'dry_run' if dry_run else 'full_pipeline') as manifest:
        recordings = [read_recording(path, config) for path in files]
        if concatenated:
            mapping = validate_concatenation(files, recordings, config)
        else:
            raw = recordings[0]
            if raw.get_num_segments()!=1: raise ValueError('Single-recording pipeline requires one segment; choose a reader recording explicitly')
            mapping = [dict(input_file=str(files[0]),start_frame=0,end_frame=raw.get_num_samples(),
                            num_samples=raw.get_num_samples(),fs=raw.sampling_frequency)]
        write_json(output / 'metadata/time_mapping.json', mapping)
        for i, recording in enumerate(recordings):
            save_recording_metadata(recording, output / f'metadata/recording_{i}.json')
        # Filter each file BEFORE time concatenation; no filter is applied across seams.
        filtered_parts = [preprocess_recording(r, config['preprocessing']) for r in recordings]
        recording = si.concatenate_recordings(filtered_parts) if concatenated else filtered_parts[0]
        recording.dump_to_json(output / 'metadata/filtered_recording.json')
        defaults, requested = sorter_parameters(config)
        write_json(output / 'metadata/sorter_defaults.json', defaults)
        write_json(output / 'metadata/sorter_requested_parameters.json', requested)
        signal_qc(recording, output, config['seed'], config['signal_qc'])
        manifest['stages_completed'].extend(['recording_qc','lazy_bandpass'])
        manifest['warnings'].extend(['No channels removed; channel 678 in the initial recording remains a diagnostic concern.',
                                    'MATLAB/GPU seed not exposed by SI wrapper; no bitwise sorter determinism claimed.',
                                    'Scientific defaults are not all confirmed paper settings.'])
        if concatenated:
            manifest['warnings'].append('Concatenation seams may produce filter/sorter artifacts; raw spikes retain identity and should be reviewed near boundaries. QC ISI includes adjacent seam intervals.')
        if dry_run:
            manifest.update(sorting_started=False, readiness='not_assessed_by_dry_run')
            return output
        if not config['metrics']['definitions_acknowledged']:
            raise ValueError('Set metrics.definitions_acknowledged after reviewing METHODS.md; no sorting started')
        manifest['sorting_started'] = False
        sorting = run_kilosort2(recording, output, config, manifest)
        if stop_after_sorting:
            sorting.save(folder=output / 'sorting_raw')
            export_spike_mapping(sorting, mapping, output / 'metrics/spike_source_mapping_raw.csv')
            manifest['stages_completed'].append('sorting_raw')
            return output
        finish_sorting(sorting, recording, mapping, output, config, manifest)
    return output


def run_single_recording(input_file: Path, config: dict, output_dir: Path | None = None, dry_run: bool = False, stop_after_sorting: bool = False) -> Path:
    """Run one input; output defaults to a fresh recording-specific directory."""
    output = output_dir or Path('results') / input_file.name.removesuffix('.raw.h5') / 'pipeline_run'
    return _run([input_file], config, output, False, dry_run, stop_after_sorting)


def run_concatenated_recordings(files: list[Path], config: dict, output_dir: Path, dry_run: bool = False) -> Path:
    """Sort jointly only after explicit chip/config/time compatibility validation."""
    return _run(files, config, output_dir, True, dry_run)
