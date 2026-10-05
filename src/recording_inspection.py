"""Bounded-window recording QC; flags never remove channels."""
from __future__ import annotations

import importlib.metadata
import json
import logging
import platform
import sys
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import h5py
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import spikeinterface.extractors as se


def _json(path: Path, value: Any) -> None:
    """Serialize numpy and path values without discarding metadata."""
    def convert(obj: Any) -> Any:
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        if isinstance(obj, np.generic):
            return obj.item()
        if isinstance(obj, bytes):
            return obj.decode('utf-8', errors='replace')
        return str(obj)
    path.write_text(json.dumps(value, default=convert, ensure_ascii=False,
                               indent=2, allow_nan=False), encoding='utf-8')


def inspect_recording(input_file: Path, output_dir: Path, config: dict) -> dict:
    """Inspect raw Maxwell data in serial bounded windows and preserve provenance.

    An existing output directory is rejected. Nonfinite raw/scaled samples fail
    inspection after diagnostic rows are saved; heuristic outliers only warn.
    """
    input_file, output_dir = input_file.resolve(), output_dir.resolve()
    if not input_file.is_file():
        raise FileNotFoundError(input_file)
    output_dir.mkdir(parents=True, exist_ok=False)
    for folder in ('metadata', 'metrics', 'figures', 'logs'):
        (output_dir / folder).mkdir()
    logger = logging.getLogger(f'inspection.{output_dir.name}')
    logger.setLevel(logging.INFO)
    handlers = [logging.FileHandler(output_dir / 'logs/inspection.log', encoding='utf-8'),
                logging.StreamHandler()]
    for handler in handlers:
        handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
        logger.addHandler(handler)
    started = time.perf_counter()
    stat = input_file.stat()
    manifest = dict(input_file=str(input_file), input_size_bytes=stat.st_size,
                    input_mtime_ns=stat.st_mtime_ns, config=config,
                    python=sys.version, executable=sys.executable, os=platform.platform(),
                    start_utc=datetime.now(timezone.utc).isoformat(), warnings=[], status='running')
    manifest['versions'] = {p: importlib.metadata.version(p) for p in
                            ('spikeinterface', 'neo', 'h5py', 'numpy', 'pandas', 'matplotlib', 'PyYAML')}
    _json(output_dir / 'run_manifest.json', manifest)
    try:
        seed = int(config['seed'])
        rng = np.random.default_rng(seed)
        nwin, winsec = int(config['windows_per_segment']), float(config['window_sec'])
        nplot, plotsec = int(config['plot_channels']), float(config['plot_duration_sec'])
        factor = float(config['relative_outlier_factor'])
        if min(nwin, winsec, nplot, plotsec) <= 0 or factor <= 1:
            raise ValueError('Invalid inspection window/plot/outlier settings')
        reader_options = dict(config.get('reader', {}))
        # Preserve the HDF5 structure without loading signal datasets.
        h5_tree = []
        with h5py.File(input_file, 'r') as handle:
            def visit(name: str, obj: Any) -> None:
                entry = dict(path=name, type=type(obj).__name__, attributes=dict(obj.attrs))
                if isinstance(obj, h5py.Dataset):
                    entry.update(shape=obj.shape, dtype=str(obj.dtype), compression=obj.compression)
                h5_tree.append(entry)
            handle.visititems(visit)
            h5_tree.insert(0, dict(path='/', attributes=dict(handle.attrs)))
        _json(output_dir / 'metadata/hdf5_structure.json', h5_tree)
        with warnings.catch_warnings(record=True) as captured:
            warnings.simplefilter('always')
            recording = se.read_maxwell(input_file, **reader_options)
            manifest['warnings'].extend(str(w.message) for w in captured)
        fs = float(recording.get_sampling_frequency())
        if not np.isfinite(fs) or fs <= 0:
            raise ValueError('Invalid sampling frequency')
        ids = recording.get_channel_ids()
        locations = recording.get_channel_locations()
        gains, offsets = recording.get_channel_gains(), recording.get_channel_offsets()
        if len(set(ids.tolist())) != len(ids):
            raise ValueError('Channel IDs are not unique')
        if locations.shape[0] != len(ids) or not np.isfinite(locations).all():
            raise ValueError('Invalid channel geometry')
        if gains is None or offsets is None:
            raise ValueError('Missing raw-to-uV conversion')
        if any(np.asarray(a).shape != (len(ids),) or not np.isfinite(a).all()
               for a in (gains, offsets)) or np.any(gains == 0):
            raise ValueError('Invalid raw-to-uV conversion arrays')
        properties = {key: recording.get_property(key) for key in recording.get_property_keys()}
        segments = [dict(segment_index=s, samples=recording.get_num_samples(s),
                         duration_sec=recording.get_num_samples(s) / fs)
                    for s in range(recording.get_num_segments())]
        metadata = dict(sampling_frequency_hz=fs, num_channels=len(ids),
                        dtype=str(recording.get_dtype()), channel_ids=ids,
                        channel_id_dtype=str(ids.dtype), channel_locations=locations,
                        properties=properties, segments=segments,
                        annotations={k: recording.get_annotation(k) for k in recording.get_annotation_keys()},
                        analyzed_trace_unit='uV', nyquist_hz=fs / 2,
                        supports_6000_hz_cutoff=bool(fs / 2 > 6000),
                        reader_options=reader_options, limited_sampling=True)
        _json(output_dir / 'metadata/recording_metadata.json', metadata)
        channel_map = pd.DataFrame(dict(channel_id=ids, gain_to_uV=gains, offset_to_uV=offsets,
                                       x_um=locations[:, 0], y_um=locations[:, 1]))
        for key, values in properties.items():
            if np.asarray(values).ndim == 1:
                channel_map[key] = values
        channel_map.to_csv(output_dir / 'metadata/channel_map.csv', index=False)
        plot_indices = np.sort(rng.choice(len(ids), size=min(nplot, len(ids)), replace=False))
        rows, windows, scaling = [], [], []
        bad_values = False
        for segment in segments:
            s, total = segment['segment_index'], segment['samples']
            length = min(round(winsec * fs), total)
            if length < 2:
                raise ValueError('Segment/window has fewer than two samples')
            for w, start in enumerate(rng.integers(0, total - length + 1, size=nwin)):
                start, end = int(start), int(start + length)
                logger.info('Reading segment %d window %d [%d, %d)', s, w, start, end)
                # Maxwell/Neo may expose extra HDF5 rows when channel_ids=None.
                # Explicit IDs preserve the mapped recording channels and order.
                raw = recording.get_traces(segment_index=s, start_frame=start, end_frame=end, channel_ids=ids)
                uv = recording.get_traces(segment_index=s, start_frame=start, end_frame=end,
                                          channel_ids=ids, return_in_uV=True)
                if raw.shape != (length, len(ids)) or uv.shape != raw.shape:
                    raise ValueError('Reader trace shape disagrees with mapped channels')
                expected = raw.astype(np.float32) * gains + offsets
                passed = bool(np.allclose(uv, expected, rtol=config['scaling_rtol'],
                                          atol=config['scaling_atol_uV'], equal_nan=False))
                scaling.append(dict(segment_index=s, window_index=w, pass_scaling=passed,
                                    max_absolute_error_uV=float(np.max(np.abs(uv - expected)))))
                del expected
                finite = np.isfinite(uv)
                bad_values |= not bool(finite.all()) or not bool(np.isfinite(raw).all())
                if not finite.all():
                    raise ValueError('Nonfinite signal samples in inspection window')
                med = np.median(uv, axis=0)
                mad = np.median(np.abs(uv - med), axis=0)
                ptp, std = np.ptp(uv, axis=0), np.std(uv, axis=0)
                changes = np.count_nonzero(np.diff(raw, axis=0), axis=0)
                limits = np.iinfo(raw.dtype) if np.issubdtype(raw.dtype, np.integer) else None
                extremes = np.mean((raw == limits.min) | (raw == limits.max), axis=0) if limits else np.zeros(len(ids))
                for c, channel in enumerate(ids):
                    rows.append(dict(segment_index=s, window_index=w, channel_id=channel,
                                     nonfinite_count=int(np.count_nonzero(~finite[:, c])),
                                     min_uV=float(uv[:, c].min()), max_uV=float(uv[:, c].max()),
                                     median_uV=float(med[c]), std_uV=float(std[c]),
                                     ptp_uV=float(ptp[c]), raw_mad_uV=float(mad[c]),
                                     changed_sample_pairs=int(changes[c]), flat_window=bool(ptp[c] == 0),
                                     dtype_extreme_fraction=float(extremes[c]),
                                     amplitude_outlier=bool(ptp[c] > factor * np.median(ptp)),
                                     raw_mad_outlier=bool(mad[c] > factor * np.median(mad))))
                windows.append(dict(segment_index=s, window_index=w, start_frame=start, end_frame=end,
                                    start_sec=start / fs, end_sec=end / fs,
                                    plot_channel_ids=';'.join(str(ids[c]) for c in plot_indices)))
                n = min(length, max(2, round(plotsec * fs)))
                diagnostic_indices = np.flatnonzero((ptp > factor * np.median(ptp)) |
                                                    (mad > factor * np.median(mad)))[:8]
                shown_indices = np.unique(np.concatenate((plot_indices, diagnostic_indices)))
                windows[-1]['plot_channel_ids'] = ';'.join(str(ids[c]) for c in shown_indices)
                fig, axes = plt.subplots(len(shown_indices), 1, figsize=(10, 2 * len(shown_indices)), sharex=True)
                for ax, c in zip(np.atleast_1d(axes), shown_indices):
                    ax.plot((np.arange(n) + start) / fs, uv[:n, c], linewidth=0.5)
                    ax.set_ylabel(f'{ids[c]}\nµV')
                    ax.ticklabel_format(axis='x', style='plain', useOffset=False)
                np.atleast_1d(axes)[-1].set_xlabel('Time within segment (s)')
                fig.suptitle(f'Raw scaled traces: segment {s}, window {w}')
                fig.tight_layout()
                fig.savefig(output_dir / f'figures/raw_s{s}_w{w}.png', dpi=120)
                plt.close(fig)
                del raw, uv
        frame = pd.DataFrame(rows)
        frame.to_csv(output_dir / 'metrics/channel_qc_windows.csv', index=False)
        pd.DataFrame(windows).to_csv(output_dir / 'metadata/sampling_windows.csv', index=False)
        _json(output_dir / 'metadata/scaling_validation.json', scaling)
        summary = frame.groupby('channel_id', sort=False).agg(
            flat_windows=('flat_window', 'sum'), max_ptp_uV=('ptp_uV', 'max'),
            median_raw_mad_uV=('raw_mad_uV', 'median'),
            amplitude_outlier_windows=('amplitude_outlier', 'sum'),
            raw_mad_outlier_windows=('raw_mad_outlier', 'sum'))
        summary.to_csv(output_dir / 'metrics/channel_qc_summary.csv')
        fig, ax = plt.subplots(figsize=(9, 5))
        points = ax.scatter(locations[:, 0], locations[:, 1], c=summary['median_raw_mad_uV'], s=12)
        fig.colorbar(points, ax=ax, label='Median sampled raw MAD (µV)')
        ax.set(xlabel='X (µm)', ylabel='Y (µm)', title='Recorded electrode geometry'); ax.set_aspect('equal')
        fig.tight_layout(); fig.savefig(output_dir / 'figures/geometry.png', dpi=150); plt.close(fig)
        fig, axes = plt.subplots(1, 2, figsize=(10, 4))
        for ax, col in zip(axes, ('max_ptp_uV', 'median_raw_mad_uV')):
            ax.hist(summary[col], bins=50); ax.set_xlabel(col); ax.set_ylabel('Channels')
        fig.tight_layout(); fig.savefig(output_dir / 'figures/qc_distributions.png', dpi=150); plt.close(fig)
        manifest['summary'] = dict(sampled_windows=len(windows), channels=len(ids),
                                  scaling_pass=all(x['pass_scaling'] for x in scaling),
                                  flat_channels_any_window=int((summary.flat_windows > 0).sum()),
                                  amplitude_outlier_channels=int((summary.amplitude_outlier_windows > 0).sum()),
                                  raw_mad_outlier_channels=int((summary.raw_mad_outlier_windows > 0).sum()),
                                  limited_sampling=True, channels_removed=0)
        if not manifest['summary']['scaling_pass'] or bad_values:
            raise ValueError('Signal/scaling validation failed')
        if any(seg['duration_sec'] < 180 for seg in segments):
            manifest['warnings'].append('Recording segment shorter than 180 s; downstream 3-min policy unresolved.')
        if any(summary.flat_windows > 0):
            manifest['warnings'].append('Flat sampled windows found; inspect channel QC before preprocessing.')
        manifest['warnings'].append('Raw MAD includes low-frequency activity; it is not final bandpass noise or unit SNR.')
        manifest['status'] = 'completed'
        logger.info('Inspection complete: %s', manifest['summary'])
        return manifest
    except Exception as error:
        manifest.update(status='failed', error_type=type(error).__name__, error=str(error))
        logger.exception('Inspection failed')
        raise
    finally:
        manifest.update(end_utc=datetime.now(timezone.utc).isoformat(), runtime_sec=time.perf_counter() - started)
        _json(output_dir / 'run_manifest.json', manifest)
        for handler in handlers:
            handler.close(); logger.removeHandler(handler)
