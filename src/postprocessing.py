"""Disk-backed sparse SortingAnalyzer and required quality metrics."""
from __future__ import annotations
from pathlib import Path
import inspect
import spikeinterface as si
import spikeinterface.metrics as metrics
import pandas as pd
from spikeinterface.core import BaseRecording, BaseSorting, SortingAnalyzer
from src.utils import write_json


def analyze_sorting(sorting: BaseSorting, recording: BaseRecording, output: Path, config: dict) -> tuple[SortingAnalyzer, pd.DataFrame]:
    """Compute bounded waveforms and only extensions needed for metrics/QC."""
    if not config['metrics']['definitions_acknowledged']:
        raise ValueError('Acknowledge explicit SI metric conventions; they are not confirmed paper definitions')
    params, seed = config['analyzer'], config['seed']
    analyzer = si.create_sorting_analyzer(sorting, recording, format='binary_folder',
        folder=output / 'analyzer', sparse=True, return_in_uV=True, seed=seed,
        peak_sign='both', sparsity_kwargs=dict(method='radius', radius_um=params['sparsity_radius_um']),
        job_kwargs=config['jobs'])
    analyzer.compute('random_spikes', max_spikes_per_unit=params['max_spikes_per_unit'], seed=seed)
    analyzer.compute('waveforms', ms_before=params['ms_before'], ms_after=params['ms_after'], dtype='float32', **config['jobs'])
    analyzer.compute('templates', operators=['average'])
    noise_chunk = min(params['noise_chunk_size'],
                      min(recording.get_num_samples(s) for s in range(recording.get_num_segments())))
    analyzer.compute('noise_levels', method='mad', random_slices_kwargs=dict(
        seed=seed, num_chunks_per_segment=params['noise_chunks_per_segment'], chunk_size=noise_chunk), **config['jobs'])
    write_json(output / 'metadata/analyzer_extension_parameters.json', {
        name: analyzer.get_extension(name).params for name in
        ('random_spikes','waveforms','templates','noise_levels')})
    analyzer.compute('spike_amplitudes', **config['jobs'])
    # Spatial footprints come from channel geometry and sparse templates; avoid
    # an additional localization extension just for illustrative QC.
    names = ['num_spikes', 'firing_rate', 'isi_violation', 'snr'] + config['metrics']['diagnostic_metrics']
    for metric in config['metrics']['diagnostic_metrics']:
        if metric not in ('presence_ratio', 'amplitude_median', 'amplitude_cv', 'rp_violation', 'sliding_rp_violation'):
            raise ValueError(f'Unsupported diagnostic metric: {metric}')
    qm_params = metrics.get_default_quality_metrics_params()
    qm_params['isi_violation'] = dict(isi_threshold_ms=config['metrics']['isi_threshold_ms'], min_isi_ms=config['metrics']['min_isi_ms'])
    qm_params['snr'] = dict(method=config['metrics']['snr_method'])
    write_json(output / 'metadata/quality_metric_parameters.json', {k: qm_params[k] for k in names})
    for name in ('compute_snrs', 'compute_isi_violations'):
        (output / f'metadata/{name}_source.txt').write_text(inspect.getsource(getattr(metrics, name)), encoding='utf-8')
    isi_helper = metrics.compute_isi_violations.__globals__['isi_violations']
    (output / 'metadata/isi_formula_source.txt').write_text(inspect.getsource(isi_helper), encoding='utf-8')
    analyzer.compute('quality_metrics', metric_names=names, metric_params=qm_params, seed=seed)
    table = analyzer.get_extension('quality_metrics').get_data()
    table.index.name = 'unit_id'
    table.to_csv(output / 'metrics/quality_metrics.csv')
    return analyzer, table
