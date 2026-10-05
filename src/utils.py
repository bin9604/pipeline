"""Configuration and reproducibility helpers."""
from __future__ import annotations
import copy
import hashlib
import importlib.metadata
import json
import logging
import platform
import sys
import time
import warnings
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator
import numpy as np
import yaml


def write_json(path: Path, value: object) -> None:
    """Write numpy-compatible JSON with nonfinite values represented as null."""
    def clean(obj):
        if isinstance(obj, dict): return {str(k): clean(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)): return [clean(v) for v in obj]
        if isinstance(obj, np.ndarray): return clean(obj.tolist())
        if isinstance(obj, np.generic): return clean(obj.item())
        if isinstance(obj, float) and not np.isfinite(obj): return None
        if isinstance(obj, Path): return str(obj)
        return obj
    path.write_text(json.dumps(clean(value), ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def load_config(path: Path) -> dict:
    """Load explicit parameters without applying hidden scientific defaults."""
    with path.open(encoding='utf-8') as handle: config = yaml.safe_load(handle)
    if not isinstance(config, dict): raise ValueError('Configuration must be a mapping')
    if config['sorter']['name'] != 'kilosort2': raise ValueError('Only Kilosort2 is supported')
    if config['metrics']['isi_threshold_ms'] <= config['metrics']['min_isi_ms']:
        raise ValueError('isi_threshold_ms must exceed min_isi_ms')
    if config['isi_analysis']['short_recording_policy'] not in ('skip', 'use_available'):
        raise ValueError('Invalid short_recording_policy')
    if config['isi_analysis']['median_scope'] not in ('all', 'retained'):
        raise ValueError('Invalid median_scope')
    if config['isi_analysis']['histogram_normalization'] not in ('probability', 'density'):
        raise ValueError('Invalid histogram_normalization')
    if config['isi_analysis']['bin_width_method'] != 'median_divided_by_15':
        raise ValueError('Unsupported bin_width_method')
    a = config['isi_analysis']
    if not (0 < a['isi_min_ms'] < a['isi_max_ms']) or a['minimum_spikes'] < 2:
        raise ValueError('Invalid ISI limits/minimum spike count')
    if config['analyzer']['max_spikes_per_unit'] <= 0:
        raise ValueError('max_spikes_per_unit must be positive')
    if config['signal_qc']['windows_per_segment'] < 1 or config['signal_qc']['window_sec'] <= 0:
        raise ValueError('Invalid signal QC sampling')
    return copy.deepcopy(config)


@contextmanager
def run_context(output: Path, inputs: list[Path], config: dict, stage: str) -> Iterator[dict]:
    """Reserve a fresh run directory and always write success/failure provenance."""
    for path in inputs:
        if not path.is_file(): raise FileNotFoundError(path)
    output.mkdir(parents=True, exist_ok=False)
    for folder in ('metadata', 'metrics', 'figures', 'logs'): (output / folder).mkdir()
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    handler = logging.FileHandler(output / 'logs/pipeline.log', encoding='utf-8')
    logger.addHandler(handler)
    started = time.perf_counter()
    versions = {}
    for name in ('spikeinterface', 'numpy', 'scipy', 'pandas', 'neo', 'h5py', 'PyYAML'):
        try: versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError: versions[name] = None
    manifest = dict(status='running', stage=stage, start_utc=datetime.now(timezone.utc).isoformat(),
                    python=sys.version, executable=sys.executable, os=platform.platform(), versions=versions,
                    inputs=[dict(path=str(p.resolve()), size_bytes=p.stat().st_size, mtime_ns=p.stat().st_mtime_ns) for p in inputs],
                    config=config, warnings=[], stages_completed=[])
    manifest['code_sha256'] = source_fingerprint(list(Path(__file__).parent.glob('*.py')) +
                                               list((Path(__file__).parent.parent/'scripts').glob('*.py')))
    write_json(output / 'metadata/config_resolved.json', config)
    write_json(output / 'run_manifest.json', manifest)
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter('always')
        try:
            yield manifest
            manifest['status'] = 'completed'
        except Exception as error:
            manifest.update(status='failed', error_type=type(error).__name__, error=str(error))
            logging.exception('Pipeline failed')
            raise
        finally:
            manifest['warnings'].extend(f'{w.category.__name__}: {w.message}' for w in captured)
            manifest.update(end_utc=datetime.now(timezone.utc).isoformat(), runtime_sec=time.perf_counter()-started)
            write_json(output / 'run_manifest.json', manifest)
            handler.close(); logger.removeHandler(handler)


def source_fingerprint(paths: list[Path]) -> dict:
    """Hash small code/config files rather than streaming the entire raw recording."""
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths if p.is_file()}
