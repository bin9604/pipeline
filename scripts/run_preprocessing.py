"""Validate lazy bandpass on Stage 2 windows without writing a full binary."""
from __future__ import annotations

import argparse
import logging
import platform
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import importlib.metadata
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import spikeinterface as si
import spikeinterface.extractors as se
import yaml
from src.preprocessing import preprocess_recording
from src.recording_inspection import _json


def run(input_file: Path, inspection_dir: Path, output: Path, config: dict) -> None:
    """Compare bounded raw/filtered windows and validate saved extractor reload."""
    import json
    input_file = input_file.resolve()
    previous = json.loads((inspection_dir / 'run_manifest.json').read_text(encoding='utf-8'))
    stat = input_file.stat()
    if previous['status'] != 'completed' or Path(previous['input_file']).resolve() != input_file:
        raise ValueError('Inspection must be completed and correspond to this input')
    if (stat.st_size, stat.st_mtime_ns) != (previous['input_size_bytes'], previous['input_mtime_ns']):
        raise ValueError('Input file changed since inspection')
    output.mkdir(parents=True, exist_ok=False)
    for folder in ('metadata', 'metrics', 'figures', 'logs'):
        (output / folder).mkdir()
    logging.basicConfig(level=logging.INFO, handlers=[logging.StreamHandler(),
                        logging.FileHandler(output / 'logs/preprocessing.log', encoding='utf-8')])
    start = time.perf_counter()
    manifest = dict(status='running', input_file=str(input_file), input_size_bytes=stat.st_size,
                    input_mtime_ns=stat.st_mtime_ns, inspection_dir=str(inspection_dir.resolve()),
                    config=config, start_utc=datetime.now(timezone.utc).isoformat(),
                    python=sys.version, executable=sys.executable, os=platform.platform(),
                    versions={p: importlib.metadata.version(p) for p in
                              ('spikeinterface', 'neo', 'numpy', 'scipy', 'h5py', 'matplotlib', 'PyYAML')},
                    warnings=[], limited_sampling=True, channels_removed=0,
                    parameter_provenance='Cutoffs: paper. Filter settings: explicit SI 0.105.0 defaults; float32: engineering choice.')
    try:
        raw = se.read_maxwell(input_file, **config['reader'])
        filtered = preprocess_recording(raw, config['preprocessing'])
        ids, fs = raw.channel_ids, raw.get_sampling_frequency()
        assert np.array_equal(ids, filtered.channel_ids)
        assert np.array_equal(raw.get_channel_locations(), filtered.get_channel_locations())
        for s in range(raw.get_num_segments()):
            assert raw.get_num_samples(s) == filtered.get_num_samples(s)
        filtered.dump_to_json(output / 'metadata/filtered_recording.json')
        reloaded = si.load(output / 'metadata/filtered_recording.json')
        _json(output / 'metadata/filter_parameters.json', dict(
            requested=config['preprocessing'], effective_kwargs={k: v for k, v in filtered._kwargs.items() if k != 'recording'},
            coefficients=filtered._recording_segments[0].coeff,
            gain_to_uV=filtered.get_channel_gains(), offset_to_uV=filtered.get_channel_offsets(),
            dtype=str(filtered.get_dtype()), sampling_frequency_hz=fs))
        windows = pd.read_csv(inspection_dir / 'metadata/sampling_windows.csv', dtype={'plot_channel_ids': str})
        windows.to_csv(output / 'metadata/sampling_windows.csv', index=False)
        rows, comparisons = [], []
        for w in windows.itertuples():
            s, a, b = int(w.segment_index), int(w.start_frame), int(w.end_frame)
            logging.info('Filtering bounded window segment=%d frames=[%d,%d)', s, a, b)
            x = raw.get_traces(segment_index=s, start_frame=a, end_frame=b, channel_ids=ids, return_in_uV=True)
            y = filtered.get_traces(segment_index=s, start_frame=a, end_frame=b, return_in_uV=True)
            if x.shape != y.shape or not np.isfinite(y).all():
                raise ValueError('Invalid filtered trace shape or nonfinite samples')
            pad = round(config['diagnostics']['context_check_sec'] * fs)
            left, right = max(0, a-pad), min(raw.get_num_samples(s), b+pad)
            expanded = filtered.get_traces(segment_index=s, start_frame=left, end_frame=right, return_in_uV=True)
            error = float(np.max(np.abs(y - expanded[a-left:b-left])))
            if error > config['diagnostics']['chunk_comparison_atol_uV']:
                raise ValueError(f'Chunk/context discrepancy {error} µV exceeds configured tolerance')
            if not np.array_equal(y, reloaded.get_traces(segment_index=s, start_frame=a, end_frame=b, return_in_uV=True)):
                raise ValueError('Reloaded lazy recording disagrees with current object')
            comparisons.append(dict(segment_index=s, window_index=int(w.window_index),
                                    max_context_error_uV=error, reload_equal=True))
            for label, values in (('raw', x), ('filtered', y)):
                median = np.median(values, axis=0)
                mad = np.median(np.abs(values - median), axis=0)
                std, ptp = np.std(values, axis=0), np.ptp(values, axis=0)
                for c, channel in enumerate(ids):
                    rows.append(dict(segment_index=s, window_index=int(w.window_index), channel_id=channel,
                                     signal=label, median_uV=float(median[c]), mad_uV=float(mad[c]),
                                     mad_sigma_uV=float(mad[c] / 0.6744897501960817),
                                     std_uV=float(std[c]), ptp_uV=float(ptp[c])))
            chosen = [list(ids).index(ch) for ch in w.plot_channel_ids.split(';')]
            n = min(len(x), round(config['diagnostics']['plot_duration_sec'] * fs))
            fig, axes = plt.subplots(len(chosen), 2, figsize=(14, 2*len(chosen)), sharex=True)
            for row, c in enumerate(chosen):
                for ax, values, label in zip(axes[row], (x, y), ('Raw', 'Filtered')):
                    ax.plot((np.arange(n)+a)/fs, values[:n, c], linewidth=0.5)
                    ax.set_ylabel(f'{ids[c]} / µV'); ax.set_title(label)
                    ax.ticklabel_format(axis='x', style='plain', useOffset=False)
            for ax in axes[-1]:
                ax.set_xlabel('Time within segment (s)')
            fig.tight_layout(); fig.savefig(output / f'figures/comparison_s{s}_w{w.window_index}.png', dpi=110); plt.close(fig)
        frame = pd.DataFrame(rows)
        frame.to_csv(output / 'metrics/preprocessing_qc_windows.csv', index=False)
        _json(output / 'metadata/chunk_reload_validation.json', comparisons)
        summary = frame.groupby(['signal', 'channel_id'], sort=False).median(numeric_only=True).reset_index()
        summary.to_csv(output / 'metrics/preprocessing_qc_summary.csv', index=False)
        fig, ax = plt.subplots(figsize=(9, 4))
        for signal in ('raw', 'filtered'):
            v = summary.loc[summary.signal == signal, 'mad_sigma_uV']
            ax.hist(v, bins=np.geomspace(max(v.min(), 0.01), max(v.max(), 0.02), 50), alpha=0.5, label=signal)
        ax.set_xscale('log'); ax.set_xlabel('Median sampled MAD sigma (µV)'); ax.set_ylabel('Channels'); ax.legend()
        fig.tight_layout(); fig.savefig(output / 'figures/noise_diagnostics.png', dpi=150); plt.close(fig)
        manifest.update(status='completed', sampled_windows=len(windows), num_channels=len(ids),
                        max_context_error_uV=max(c['max_context_error_uV'] for c in comparisons))
        manifest['warnings'].extend(['Sampled MAD sigma is a diagnostic, not unit SNR.',
                                    'Channel 678 retained; Stage 2 flagged it for inspection.',
                                    '179.83 s recording: downstream 180 s policy unresolved.'])
    except Exception as error:
        manifest.update(status='failed', error_type=type(error).__name__, error=str(error))
        logging.exception('Preprocessing validation failed')
        raise
    finally:
        manifest.update(end_utc=datetime.now(timezone.utc).isoformat(), runtime_sec=time.perf_counter()-start)
        _json(output / 'run_manifest.json', manifest)


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ('input', 'inspection', 'output', 'config'):
        parser.add_argument(f'--{flag}', required=True, type=Path)
    args = parser.parse_args()
    with args.config.open(encoding='utf-8') as handle:
        config = yaml.safe_load(handle)
    run(args.input, args.inspection, args.output, config)


if __name__ == '__main__':
    main()
