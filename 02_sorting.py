"""Lazy filtered Recording → explicit KS2 parameters → run_sorter → raw Sorting.

Default: read-only environment/parameter preflight, no binary export or sorting.
Use --run on a verified NVIDIA compute node to export binary and start Kilosort2.
"""
from __future__ import annotations
import argparse
import copy
import inspect
import os
from pathlib import Path
import shutil
import subprocess

import numpy as np
import spikeinterface as si
import spikeinterface.sorters as ss
from spikeinterface.core import write_binary_recording

from utils import (load_config, run_record, read_json, write_json, check_input_identity,
                   validate_metadata, validate_scaling, binary_quantization, kilosort_commit)


def run(config, output=None, start_sorting=False):
    preprocessing = Path(config['paths']['preprocessing'])
    output = Path(output or config['paths']['sorting']).resolve()
    with run_record(output, '02_sorting', [preprocessing], config) as record:
        # ============================================================
        # 1. Central, explicit Kilosort2 parameters (no fallback sorter)
        # ============================================================
        defaults = ss.get_default_sorter_params('kilosort2')
        overrides = {**config['sorter']['overrides'], **config['jobs']}
        if set(overrides)-set(defaults):
            raise ValueError(f'Unknown Kilosort2 parameters: {set(overrides)-set(defaults)}')
        parameters = {**defaults, **overrides}
        if parameters['keep_good_only'] or parameters['skip_kilosort_preprocessing']:
            raise ValueError('Keep all raw units and retain KS2 internal preprocessing in this baseline')
        if parameters['delete_recording_dat'] or parameters['delete_tmp_files']:
            raise ValueError('Preserve binary and MATLAB temporary files for audit')
        print('Final parameters passed to run_sorter:', parameters, flush=True)
        write_json(output / 'metadata/sorter_defaults.json', defaults)
        write_json(output / 'metadata/sorter_parameters.json', parameters)
        write_json(output / 'metadata/parameter_sources.json', {
            key: ('engineering: retain audit files' if key == 'delete_tmp_files' else
                  'engineering: binary write jobs' if key in config['jobs'] else
                  'explicit experimental override (not paper-confirmed)' if key in overrides and value != defaults[key] else
                  'SpikeInterface/Kilosort2 software default (not paper-confirmed)')
            for key, value in parameters.items()})
        cls = ss.sorter_dict['kilosort2']
        # SI 0.105.0 resolves NT and wave_length; the MATLAB template resolves scaleproc.
        if si.__version__ != '0.105.0':
            raise ValueError('Re-audit KS2 binary/metric implementation before changing SI 0.105.0')
        resolved = cls._check_params(None, output, copy.deepcopy(parameters))
        write_json(output / 'metadata/wrapper_resolved_parameters.json', resolved)
        write_json(output / 'metadata/matlab_parameter_notes.json', dict(
            scaleproc=200 if resolved['scaleproc'] is None else resolved['scaleproc'],
            source='SI 0.105.0 kilosort2_master.m; external filter != internal KS2 highpass/CAR/whitening',
            matlab_rng_seed='not exposed; no deterministic GPU result claimed'))
        (output / 'metadata/kilosort2_wrapper.txt').write_text(inspect.getsource(cls), encoding='utf-8')
        from spikeinterface.sorters.external.kilosortbase import KilosortBase
        (output / 'metadata/kilosort_base.txt').write_text(inspect.getsource(KilosortBase), encoding='utf-8')
        (output / 'metadata/binary_writer.txt').write_text(inspect.getsource(write_binary_recording), encoding='utf-8')
        template = Path(inspect.getfile(cls)).with_name('kilosort2_master.m')
        shutil.copyfile(template, output / 'metadata/kilosort2_master.m.txt')

        # ============================================================
        # 2. Discover KS2 source, MATLAB/runtime, NVIDIA; discovery != validation
        # ============================================================
        kilosort_path = config['paths']['kilosort2'] or os.environ.get('KILOSORT2_PATH')
        blockers = []
        if kilosort_path:
            if not Path(kilosort_path).is_dir():
                blockers.append('Kilosort2 path is not a directory')
            else:
                cls.set_kilosort2_path(kilosort_path)
        installed = cls.is_installed()
        if not installed:
            blockers.append('Kilosort2 not detected by SI')
        matlab = shutil.which('matlab')
        compiled = cls.check_compiled()
        if not matlab and not compiled:
            blockers.append('MATLAB/compiled runtime not detected')
        nvidia = shutil.which('nvidia-smi')
        if not nvidia and os.name == 'nt':
            candidate = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/nvidia-smi.exe'
            if candidate.is_file():
                nvidia = str(candidate)
        gpu = {}
        if nvidia:
            try:
                result = subprocess.run([nvidia, '--query-gpu=name,driver_version,memory.total',
                                         '--format=csv,noheader'], capture_output=True, text=True, timeout=20)
                gpu = dict(returncode=result.returncode, stdout=result.stdout, stderr=result.stderr)
            except (OSError, subprocess.TimeoutExpired) as error:
                gpu = dict(error=str(error))
        if gpu.get('returncode') != 0 or not gpu.get('stdout', '').strip():
            blockers.append('Working NVIDIA GPU/driver not detected')
        actual_path = getattr(cls, 'kilosort2_path', None)
        revision = kilosort_commit(actual_path)
        environment = dict(kilosort2_installed=installed, kilosort2_path=actual_path,
                           kilosort2_commit=revision, kilosort2_version=cls.get_sorter_version() if installed else None,
                           matlab=matlab, compiled_runtime=bool(compiled), gpu=gpu,
                           runtime_confirmed_by_user=bool(config['sorter']['runtime_confirmed']),
                           blockers=blockers, readiness='not_ready' if blockers else 'requires_runtime_validation')
        record['kilosort2_commit'] = revision
        write_json(output / 'metadata/environment.json', environment)
        print('Environment:', environment, flush=True)

        # ============================================================
        # 3. Load the existing lazy Recording; do not rerun preprocessing
        # ============================================================
        previous = read_json(preprocessing / 'run.json')
        metadata = read_json(preprocessing / 'metadata/recording.json')
        if previous['status'] != 'completed':
            raise ValueError('Preprocessing did not complete')
        check_input_identity(metadata['input_identity'])
        recording_filtered = si.load(preprocessing / 'metadata/filtered_recording.json')
        actual = validate_metadata(recording_filtered)
        gains = recording_filtered.get_channel_gains()
        offsets = recording_filtered.get_channel_offsets()
        if (actual['num_samples'] != metadata['num_samples'] or
            actual['sampling_frequency_hz'] != metadata['sampling_frequency_hz'] or
            not np.array_equal(actual['channel_ids'], metadata['channel_ids']) or
            not np.array_equal(actual['geometry_um'], metadata['geometry_um']) or
            not np.array_equal(gains, metadata['gain_to_uV']) or not np.all(offsets == 0) or
            np.dtype(recording_filtered.get_dtype()).kind != 'f'):
            raise ValueError('Filtered Recording mapping/scaling disagrees with preprocessing evidence')
        write_json(output / 'metadata/recording.json', metadata)
        record['warnings'].extend([
            'Binary stores int16 ADC-count values, not uV. Casting truncates fractional counts.',
            'Internal KS2 highpass/CAR/whitening/minFR are software defaults, not paper-confirmed.',
            'Discovery does not verify MATLAB license, toolbox, gpuDevice or CUDA MEX.',
            'KS2 revision/batch-boundary patch choice and GPU reproducibility need explicit review.'])
        if not start_sorting:
            record.update(readiness=environment['readiness'], binary_validated=False)
            return output
        if blockers:
            raise RuntimeError('; '.join(blockers))
        if not config['sorter']['runtime_confirmed']:
            raise RuntimeError('Verify MATLAB license/toolbox/gpuDevice/KS2 CUDA MEX, then set sorter.runtime_confirmed=true')
        if not actual_path or not revision:
            raise RuntimeError('Require an explicit versioned KS2 checkout and recorded git commit')

        # ============================================================
        # 4. Full streamed finite/range AND physical quantization validation
        # ============================================================
        samples, fs = recording_filtered.get_num_samples(), recording_filtered.get_sampling_frequency()
        chunk = max(1, round(fs))
        max_error, max_abs = 0.0, 0.0
        numerical_energy = np.zeros(len(gains))
        encoded_energy = np.zeros(len(gains))
        for start in range(0, samples, chunk):
            end = min(samples, start+chunk)
            traces = recording_filtered.get_traces(start_frame=start, end_frame=end)
            physical = recording_filtered.get_traces(start_frame=start, end_frame=end, return_in_uV=True)
            validate_scaling(traces, physical, gains, offsets)
            encoded, error = binary_quantization(traces, gains, config['sorter']['max_quantization_error_uV'])
            max_error = max(max_error, error)
            max_abs = max(max_abs, float(np.abs(traces).max()))
            numerical_energy += np.square(traces.astype('float64')).sum(axis=0)
            encoded_energy += np.square(encoded.astype('float64')).sum(axis=0)
        if np.any((numerical_energy > 0) & (encoded_energy == 0)):
            raise ValueError('int16 quantization erased an entire nonzero channel; review scaling')
        # Verify the real SI writer with a short probe BEFORE starting KS2.
        probe_frames = min(samples, chunk)
        probe_file = output / 'metadata/binary_probe.dat'
        write_binary_recording(recording_filtered.frame_slice(0, probe_frames), [probe_file], dtype='int16', **config['jobs'])
        probe = np.fromfile(probe_file, dtype='int16').reshape(probe_frames, len(gains))
        expected = recording_filtered.get_traces(start_frame=0, end_frame=probe_frames).astype('int16')
        if not np.array_equal(probe, expected):
            raise ValueError('SI binary writer differs from audited numeric int16 conversion')
        write_json(output / 'metadata/binary_scaling.json', dict(
            scanned_all_samples=True, input_unit='ADC counts', binary_unit='ADC counts',
            physical_conversion='binary_int16 * gain_to_uV; filtered offset=0',
            gain_to_uV=gains, max_quantization_error_uV=max_error, max_abs_numerical=max_abs,
            probe_writer_matches=True, expected_binary_bytes=samples*len(gains)*2,
            energy_retention_per_channel=np.divide(encoded_energy, numerical_energy,
                out=np.ones_like(encoded_energy), where=numerical_energy > 0)))

        # ============================================================
        # 5. The only expensive sorter call; preserve raw Sorting independently
        # ============================================================
        print('Starting run_sorter with parameters:', parameters, flush=True)
        record.update(sorting_started=True, binary_validated=True)
        write_json(output / 'run.json', record)
        sorting_raw = ss.run_sorter('kilosort2', recording_filtered, folder=output / 'kilosort2',
                                   remove_existing_folder=False, delete_output_folder=False,
                                   verbose=True, raise_error=True, **parameters)
        if sorting_raw.get_num_segments() != 1 or sorting_raw.get_sampling_frequency() != fs:
            raise ValueError('Raw Sorting segment count/sampling rate disagrees with Recording')
        # Check the actual full binary after SI setup/run, not just the probe.
        binary = output / 'kilosort2/sorter_output/recording.dat'
        if binary.stat().st_size != samples*len(gains)*2:
            raise ValueError('Unexpected Kilosort2 binary size')
        binary_map = np.memmap(binary, dtype='int16', mode='r', shape=(samples, len(gains)))
        for start in sorted(set([0, samples//2, max(0, samples-probe_frames)])):
            end = min(samples, start+probe_frames)
            if not np.array_equal(binary_map[start:end], recording_filtered.get_traces(
                    start_frame=start, end_frame=end).astype('int16')):
                raise ValueError('Actual KS2 binary sample disagrees with filtered ADC-count scale')
        del binary_map
        sorting_raw.save(folder=output / 'sorting_raw')
        write_json(output / 'metadata/sorting_source.json', dict(
            preprocessing=str(preprocessing.resolve()), input_identity=metadata['input_identity'],
            sampling_frequency_hz=fs, num_samples=samples, actual_binary_sample_check=True,
            kilosort2_commit=revision))
        record.update(raw_units=len(sorting_raw.unit_ids), readiness='sorting_completed')
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=Path(__file__).with_name('config.yaml'))
    parser.add_argument('--output', type=Path, help='Fresh folder; update paths.sorting for QC')
    parser.add_argument('--run', action='store_true', help='Export full binary and start KS2 on a verified GPU node')
    args = parser.parse_args()
    result = run(load_config(args.config), args.output, args.run)
    if not args.run and read_json(result / 'metadata/environment.json')['blockers']:
        raise SystemExit(2)
