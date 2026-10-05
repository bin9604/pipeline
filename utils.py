"""Small file/provenance helpers and pure data checks; no pipeline orchestration."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
from pathlib import Path
import subprocess
import sys
import time
import warnings

import numpy as np
import yaml


def write_json(path, value):
    def convert(obj):
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        if isinstance(obj, np.generic):
            return obj.item()
        if isinstance(obj, Path):
            return str(obj)
        raise TypeError(f'Cannot serialize {type(obj).__name__}')
    Path(path).write_text(json.dumps(value, default=convert, ensure_ascii=False,
                                   indent=2, allow_nan=False), encoding='utf-8')


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def load_config(path):
    path = Path(path).resolve()
    config = yaml.safe_load(path.read_text(encoding='utf-8'))
    # Paths are relative to the config file, not the shell's working directory.
    for key, value in config['paths'].items():
        if value is not None:
            config['paths'][key] = str((path.parent / value).resolve())
    return config


def file_identity(path):
    path = Path(path).resolve()
    stat = path.stat()
    return dict(path=str(path), size_bytes=stat.st_size, mtime_ns=stat.st_mtime_ns)


def check_input_identity(identity):
    if file_identity(identity['path']) != identity:
        raise ValueError('Raw input changed or moved: rerun 01_preprocessing.py')


def kilosort_commit(path):
    if not path:
        return None
    try:
        result = subprocess.run(['git', '-C', str(path), 'rev-parse', 'HEAD'],
                                capture_output=True, text=True, timeout=10)
        return result.stdout.strip() if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


@contextmanager
def run_record(output, stage, inputs, config):
    """Reserve a fresh folder and write one JSON even when the stage fails."""
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    for name in ('metadata', 'metrics', 'figures'):
        (output / name).mkdir()
    versions = {}
    for name in ('spikeinterface', 'neo', 'h5py', 'numpy', 'scipy', 'pandas', 'matplotlib', 'PyYAML'):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    sources = [Path(__file__)] + list(Path(__file__).parent.glob('0[1-4]_*.py'))
    record = dict(stage=stage, status='running', inputs=[str(Path(p).resolve()) for p in inputs],
                  output=str(output), parameters=config, python=sys.version, executable=sys.executable,
                  versions=versions, kilosort2_commit=kilosort_commit(config['paths'].get('kilosort2')),
                  start_utc=datetime.now(timezone.utc).isoformat(), warnings=[], sorting_started=False,
                  code_sha256={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sources})
    start = time.perf_counter()
    write_json(output / 'run.json', record)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter('always')
        try:
            yield record
            record['status'] = 'completed'
        except Exception as error:
            record.update(status='failed', error_type=type(error).__name__, error=str(error))
            raise
        finally:
            record['warnings'].extend(str(w.message) for w in caught)
            record.update(end_utc=datetime.now(timezone.utc).isoformat(), runtime_sec=time.perf_counter()-start)
            write_json(output / 'run.json', record)


def validate_metadata(recording):
    """Validate one recording without reading its complete signal."""
    fs = float(recording.get_sampling_frequency())
    ids = recording.get_channel_ids()
    gains, offsets = recording.get_channel_gains(), recording.get_channel_offsets()
    locations = recording.get_channel_locations()
    if not np.isfinite(fs) or fs <= 0 or recording.get_num_segments() != 1:
        raise ValueError('Require positive sampling frequency and exactly one segment')
    if not len(ids) or len(set(ids.tolist())) != len(ids):
        raise ValueError('Empty or duplicate channel IDs')
    if recording.get_num_samples() < 2:
        raise ValueError('Recording must contain at least two samples')
    if locations.ndim != 2 or locations.shape[0] != len(ids) or locations.shape[1] < 2 or not np.isfinite(locations).all():
        raise ValueError('Missing or invalid electrode geometry')
    for values in (gains, offsets):
        if values is None or np.asarray(values).shape != (len(ids),) or not np.isfinite(values).all():
            raise ValueError('Missing or invalid gain/offset conversion to uV')
    if np.any(gains <= 0):
        raise ValueError('Maxwell gain_to_uV must be positive')
    return dict(sampling_frequency_hz=fs, num_samples=recording.get_num_samples(),
                duration_sec=recording.get_num_samples()/fs, num_channels=len(ids), channel_ids=ids,
                geometry_um=locations, gain_to_uV=gains, offset_to_uV=offsets,
                physical_unit='uV', numerical_unit='ADC counts', dtype=str(recording.get_dtype()))


def validate_scaling(numerical, physical, gains, offsets, rtol=1e-5, atol=1e-4):
    if numerical.ndim != 2 or numerical.shape != physical.shape or numerical.shape[1] != len(gains):
        raise ValueError('Trace shape disagrees with channel mapping')
    if not np.isfinite(numerical).all() or not np.isfinite(physical).all():
        raise ValueError('NaN/Inf signal sample')
    expected = numerical.astype(np.float32) * gains + offsets
    if not np.allclose(physical, expected, rtol=rtol, atol=atol):
        raise ValueError('Physical signal disagrees with numerical * gain_to_uV + offset_to_uV')
    return float(np.max(np.abs(physical-expected)))


def validate_bandpass(fs, low, high):
    if not np.isfinite([fs, low, high]).all() or not (0 < low < high < fs/2):
        raise ValueError(f'Invalid bandpass {low}–{high} Hz for fs={fs} Hz')


def binary_quantization(numerical, gains, max_error_uV):
    """SI 0.105.0 writes numerical samples, cast to int16, not automatic uV."""
    if max_error_uV <= 0 or not np.isfinite(max_error_uV):
        raise ValueError('max_quantization_error_uV must be finite and positive')
    if not np.isfinite(numerical).all() or numerical.min() < -32768 or numerical.max() > 32767:
        raise ValueError('Nonfinite sample or int16 overflow; no clipping/rescaling is permitted')
    encoded = numerical.astype('int16')
    error = np.abs((encoded.astype(np.float64)-numerical) * gains)
    if error.max() > max_error_uV:
        raise ValueError('int16 physical quantization exceeds explicit engineering tolerance')
    return encoded, float(error.max())
