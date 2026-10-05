"""Kilosort2 invocation and audit of parameters; no fallback sorter."""
from __future__ import annotations
import inspect
import os
import shutil
import subprocess
from pathlib import Path
import numpy as np
import spikeinterface.sorters as ss
from spikeinterface.core import BaseRecording, BaseSorting
from src.utils import write_json


def sorter_parameters(config: dict) -> tuple[dict, dict]:
    """Return pristine defaults and explicit overrides including binary-write jobs."""
    defaults = ss.get_default_sorter_params('kilosort2')
    overrides = dict(config['sorter']['overrides'])
    overrides.update(config['jobs'])
    unknown = set(overrides) - set(defaults)
    if unknown: raise ValueError(f'Unknown Kilosort2 parameters: {unknown}')
    if overrides.get('skip_kilosort_preprocessing', False):
        raise ValueError('Skipping Kilosort2 preprocessing is not supported by this baseline')
    if overrides.get('keep_good_only', False): raise ValueError('Baseline must retain all sorter units')
    return defaults, {**defaults, **overrides}


def check_sorter_environment(config: dict) -> dict:
    """Fail early on missing KS2/MATLAB/NVIDIA; deeper MEX/license checks are external."""
    cls = ss.sorter_dict['kilosort2']
    if config['sorter']['path']: cls.set_kilosort2_path(config['sorter']['path'])
    if not cls.is_installed(): raise RuntimeError('Kilosort2 not installed; see STAGE4_RESULTS.md')
    if not shutil.which('matlab') and not cls.check_compiled(): raise RuntimeError('MATLAB not on PATH')
    nvidia = shutil.which('nvidia-smi')
    if not nvidia and os.name == 'nt':
        p = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/nvidia-smi.exe'
        if p.is_file(): nvidia = str(p)
    if not nvidia: raise RuntimeError('NVIDIA driver/GPU not discoverable')
    result = subprocess.run([nvidia, '--query-gpu=name,driver_version,memory.total', '--format=csv,noheader'],
                            capture_output=True, text=True, timeout=20, check=True)
    return dict(gpu=result.stdout, sorter_version=cls.get_sorter_version(),
                kilosort2_path=getattr(cls, 'kilosort2_path', None))


def run_kilosort2(recording: BaseRecording, output: Path, config: dict, manifest: dict) -> BaseSorting:
    """Run on the filtered Recording, letting SI create bounded int16 binary chunks."""
    manifest['sorter_environment'] = check_sorter_environment(config)
    defaults, parameters = sorter_parameters(config)
    write_json(output / 'metadata/sorter_defaults.json', defaults)
    write_json(output / 'metadata/sorter_requested_parameters.json', parameters)
    cls = ss.sorter_dict['kilosort2']
    (output / 'metadata/kilosort2_wrapper.txt').write_text(inspect.getsource(cls), encoding='utf-8')
    # Explicit streamed check before SI's int16 writer (additional IO, bounded RAM).
    max_abs = 0.0
    for segment in range(recording.get_num_segments()):
        samples = recording.get_num_samples(segment)
        chunk = max(1, int(recording.sampling_frequency))
        for start in range(0, samples, chunk):
            traces = recording.get_traces(segment_index=segment, start_frame=start,
                                           end_frame=min(samples, start+chunk))
            if not np.isfinite(traces).all() or traces.min() < -32768 or traces.max() > 32767:
                raise ValueError('Filtered numerical traces cannot be safely written as int16')
            max_abs = max(max_abs, float(np.max(np.abs(traces))))
    manifest['binary_range_validation'] = dict(max_abs_numerical_value=max_abs, scanned_all_samples=True)
    from spikeinterface.core import write_binary_recording
    (output / 'metadata/binary_writer.txt').write_text(inspect.getsource(write_binary_recording), encoding='utf-8')
    manifest['warnings'].append('SI writes int16 numerical traces (ADC-count scale here), not automatic uV export; dtype conversion quantizes the float filtered traces.')
    manifest['sorting_started'] = True
    write_json(output / 'run_manifest.json', manifest)
    return ss.run_sorter('kilosort2', recording, folder=output / 'kilosort2',
                         remove_existing_folder=False, delete_output_folder=False,
                         verbose=True, raise_error=True, **parameters)
