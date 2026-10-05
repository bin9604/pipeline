"""Existing raw Sorting → SortingAnalyzer → metrics → candidate curation + review.

Edit config.yaml curation thresholds; this script never runs Kilosort2.
Automatic candidate acceptance does not establish a biological neuron.
"""
from __future__ import annotations
import argparse
import inspect
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import spikeinterface as si
import spikeinterface.metrics as metrics

from utils import (load_config, run_record, read_json, write_json, check_input_identity,
                   validate_metadata)


def curate_metrics(quality_metrics, thresholds):
    """All three inclusive thresholds must pass; every invalid metric rejects."""
    if not quality_metrics.index.is_unique:
        raise ValueError('Duplicate unit IDs in metrics')
    limits = np.array([thresholds['isi_violation_max'], thresholds['firing_rate_min_hz'], thresholds['snr_min']])
    if not np.isfinite(limits).all() or np.any(limits < 0):
        raise ValueError('Curation thresholds must be finite and nonnegative')
    decisions = quality_metrics.copy()
    rules = [
        ('isi_violations_ratio', thresholds['isi_violation_max'], 'maximum', 'high_isi_violation'),
        ('firing_rate', thresholds['firing_rate_min_hz'], 'minimum', 'low_firing_rate'),
        ('snr', thresholds['snr_min'], 'minimum', 'low_snr'),
    ]
    accepted, all_reasons = [], []
    for _, row in quality_metrics.iterrows():
        reasons = []
        for name, limit, direction, reason in rules:
            value = float(row[name])
            if not np.isfinite(value):
                reasons.append(f'invalid_{name}')
            elif (direction == 'minimum' and value < limit) or (direction == 'maximum' and value > limit):
                reasons.append(reason)
        accepted.append(not reasons)
        all_reasons.append(';'.join(reasons))
    decisions['accepted'] = pd.Series(accepted, index=decisions.index, dtype=bool)
    decisions['candidate_status'] = np.where(decisions['accepted'], 'candidate accepted', 'candidate rejected')
    decisions['rejection_reason'] = all_reasons
    return decisions


def run(config, output=None):
    preprocessing = Path(config['paths']['preprocessing'])
    sorting_folder = Path(config['paths']['sorting'])
    output = Path(output or config['paths']['qc']).resolve()
    with run_record(output, '03_qc_curation', [sorting_folder / 'sorting_raw', preprocessing], config) as record:
        # ============================================================
        # 1. Load existing raw Sorting and the exact matching filtered Recording
        # ============================================================
        if si.__version__ != '0.105.0':
            raise ValueError('Re-audit metric definitions before changing SpikeInterface 0.105.0')
        if not config['metrics']['definitions_acknowledged']:
            raise ValueError('Review METHODS.md and acknowledge SI metric definitions in config.yaml')
        previous = read_json(sorting_folder / 'run.json')
        source = read_json(sorting_folder / 'metadata/sorting_source.json')
        metadata = read_json(preprocessing / 'metadata/recording.json')
        if previous['status'] != 'completed' or not previous['sorting_started']:
            raise ValueError('Require a completed sorting run, not preflight output')
        if (source['preprocessing'] != str(preprocessing.resolve()) or
            source['input_identity'] != metadata['input_identity']):
            raise ValueError('Raw Sorting and Recording originate from different preprocessing runs')
        check_input_identity(metadata['input_identity'])
        sorting_raw = si.load(sorting_folder / 'sorting_raw')
        recording_filtered = si.load(preprocessing / 'metadata/filtered_recording.json')
        actual = validate_metadata(recording_filtered)
        if (sorting_raw.get_num_segments() != 1 or sorting_raw.get_sampling_frequency() != actual['sampling_frequency_hz'] or
            actual['num_samples'] != source['num_samples'] or source['num_samples'] != metadata['num_samples'] or
            not np.array_equal(actual['channel_ids'], metadata['channel_ids']) or
            not np.array_equal(actual['geometry_um'], metadata['geometry_um']) or
            not np.array_equal(actual['gain_to_uV'], metadata['gain_to_uV']) or
            not np.all(actual['offset_to_uV'] == 0)):
            raise ValueError('Recording metadata/Sorting mismatch')
        for unit in sorting_raw.unit_ids:
            frames = sorting_raw.get_unit_spike_train(unit)
            if np.any(frames < 0) or np.any(frames >= actual['num_samples']) or np.any(np.diff(frames) < 0):
                raise ValueError('Spike frames outside recording or not chronological')
        record['kilosort2_commit'] = source.get('kilosort2_commit')

        # ============================================================
        # 2. SortingAnalyzer → random spikes → waveforms → templates → noise
        # ============================================================
        settings, seed = config['analyzer'], config['seed']
        print('Waveform/QC parameters:', settings)
        if min(settings['ms_before'], settings['ms_after'], settings['max_spikes_per_unit'],
               settings['sparsity_radius_um'], settings['noise_chunks_per_segment'], settings['noise_chunk_size']) <= 0:
            raise ValueError('Analyzer parameters must be positive')
        analyzer = None
        metric_names = ['num_spikes', 'firing_rate', 'isi_violation', 'snr'] + config['metrics']['diagnostic_metrics']
        metric_parameters = metrics.get_default_quality_metrics_params()
        metric_parameters['isi_violation'] = dict(isi_threshold_ms=config['metrics']['isi_threshold_ms'],
                                                 min_isi_ms=config['metrics']['min_isi_ms'])
        metric_parameters['snr'] = dict(method=config['metrics']['snr_method'])
        if not (0 <= config['metrics']['min_isi_ms'] < config['metrics']['isi_threshold_ms']):
            raise ValueError('Require 0 <= min_isi_ms < isi_threshold_ms')
        write_json(output / 'metadata/metric_parameters.json', {k: metric_parameters[k] for k in metric_names})
        for name in ('compute_snrs', 'compute_isi_violations'):
            (output / f'metadata/{name}.txt').write_text(inspect.getsource(getattr(metrics, name)), encoding='utf-8')
        (output / 'metadata/isi_formula.txt').write_text(inspect.getsource(
            metrics.compute_isi_violations.__globals__['isi_violations']), encoding='utf-8')
        if len(sorting_raw.unit_ids):
            analyzer = si.create_sorting_analyzer(sorting_raw, recording_filtered, format='binary_folder',
                folder=output / 'analyzer', sparse=True, return_in_uV=True, seed=seed, peak_sign='both',
                sparsity_kwargs=dict(method='radius', radius_um=settings['sparsity_radius_um']), job_kwargs=config['jobs'])
            analyzer.compute('random_spikes', max_spikes_per_unit=settings['max_spikes_per_unit'], seed=seed)
            analyzer.compute('waveforms', ms_before=settings['ms_before'], ms_after=settings['ms_after'],
                             dtype='float32', **config['jobs'])
            analyzer.compute('templates', operators=['average'])
            analyzer.compute('noise_levels', method='mad', random_slices_kwargs=dict(
                seed=seed, num_chunks_per_segment=settings['noise_chunks_per_segment'],
                chunk_size=min(settings['noise_chunk_size'], recording_filtered.get_num_samples())), **config['jobs'])
            analyzer.compute('spike_amplitudes', **config['jobs'])

            # ============================================================
            # 3. Actual SI quality metrics, inspect full table before curation
            # ============================================================
            analyzer.compute('quality_metrics', metric_names=metric_names, metric_params=metric_parameters, seed=seed)
            quality_metrics = analyzer.get_extension('quality_metrics').get_data()
            write_json(output / 'metadata/analyzer_parameters.json', {
                name: analyzer.get_extension(name).params for name in
                ('random_spikes', 'waveforms', 'templates', 'noise_levels', 'spike_amplitudes', 'quality_metrics')})
        else:
            quality_metrics = pd.DataFrame(columns=['num_spikes', 'firing_rate', 'isi_violations_ratio', 'snr'],
                                           index=pd.Index(sorting_raw.unit_ids))
            record['warnings'].append('Zero raw units: no analyzer or unit figures; empty curation remains valid.')
        quality_metrics = quality_metrics.reindex(sorting_raw.unit_ids)
        quality_metrics.index.name = 'unit_id'
        quality_metrics.to_csv(output / 'metrics/quality_metrics.csv')
        print(quality_metrics.to_string())

        # ============================================================
        # 4. Inclusive, visible thresholds; retain every rejection reason
        # ============================================================
        SNR_MIN = config['curation']['snr_min']
        ISI_VIOLATION_MAX = config['curation']['isi_violation_max']
        FIRING_RATE_MIN = config['curation']['firing_rate_min_hz']
        print(f'Candidate rules: SNR >= {SNR_MIN}; ISI ratio <= {ISI_VIOLATION_MAX}; FR >= {FIRING_RATE_MIN} Hz')
        decisions = curate_metrics(quality_metrics, config['curation'])
        accepted_unit_ids = sorting_raw.unit_ids[decisions.accepted.to_numpy(dtype=bool)]
        rejected_unit_ids = sorting_raw.unit_ids[~decisions.accepted.to_numpy(dtype=bool)]
        decisions.to_csv(output / 'metrics/curation_decisions.csv')
        decisions.loc[accepted_unit_ids].to_csv(output / 'metrics/accepted_units.csv')
        decisions.loc[rejected_unit_ids].to_csv(output / 'metrics/rejected_units.csv')

        # ============================================================
        # 5. Human review figures; automatic decisions are only candidates
        # ============================================================
        figure_settings = config['visualization']
        if min(figure_settings[k] for k in ('units_per_group', 'sampled_waveforms', 'raster_duration_sec',
            'acg_window_ms', 'acg_bin_ms', 'max_acg_pairs', 'amplitude_histogram_bins',
            'diagnostic_isi_max_ms', 'diagnostic_isi_bins', 'firing_rate_bin_sec')) <= 0:
            raise ValueError('QC figure settings must be positive')
        if analyzer is not None:
            plot_unit_qc(analyzer, decisions, output, config)

        # ============================================================
        # 6. Save curated Sorting separately; never write to sorting_raw
        # ============================================================
        sorting_curated = sorting_raw.select_units(accepted_unit_ids)
        sorting_curated.save(folder=output / 'sorting_curated')
        write_json(output / 'metadata/analysis_source.json', dict(
            sampling_frequency_hz=metadata['sampling_frequency_hz'], num_samples=metadata['num_samples'],
            duration_sec=metadata['duration_sec'], input_identity=metadata['input_identity'],
            raw_sorting=str((sorting_folder / 'sorting_raw').resolve()),
            candidate_unit_ids=accepted_unit_ids, kilosort2_commit=source.get('kilosort2_commit')))
        record.update(raw_units=len(sorting_raw.unit_ids), curated_units=len(accepted_unit_ids))
        record['warnings'].append('Candidate acceptance does not confirm a biological neuron; review QC figures/notebook.')
    return output


def plot_unit_qc(analyzer, decisions: pd.DataFrame, output: Path, config: dict) -> None:
    """Sample accepted/borderline/rejected units and save eight-panel QC."""
    rng, settings = np.random.default_rng(config['seed']), config['visualization']
    limits = config['curation']
    distances = np.column_stack([
        np.abs(decisions.isi_violations_ratio.to_numpy()-limits['isi_violation_max'])/max(limits['isi_violation_max'], np.finfo(float).eps),
        np.abs(decisions.firing_rate.to_numpy()-limits['firing_rate_min_hz'])/max(limits['firing_rate_min_hz'], np.finfo(float).eps),
        np.abs(decisions.snr.to_numpy()-limits['snr_min'])/max(limits['snr_min'], np.finfo(float).eps)])
    borderline = np.any(distances <= settings['borderline_relative_distance'], axis=1)
    groups = dict(accepted=decisions.index[decisions.accepted], rejected=decisions.index[~decisions.accepted],
                  borderline=decisions.index[borderline])
    fs, sorting = analyzer.sampling_frequency, analyzer.sorting
    amplitudes = analyzer.get_extension('spike_amplitudes').get_data(copy=False)
    indices = sorting.get_spike_vector_to_indices()
    selection = []
    for group, candidates in groups.items():
        if not len(candidates): continue
        for unit in rng.choice(candidates.to_numpy(), min(settings['units_per_group'], len(candidates)), replace=False):
            selection.append(dict(group=group, unit_id=unit))
            template = analyzer.get_extension('templates').get_unit_template(unit, operator='average')
            main = int(analyzer.get_main_channels(outputs='index')[sorting.id_to_index(unit)])
            waves = analyzer.get_extension('waveforms').get_waveforms_one_unit(unit)
            sparse_channels = analyzer.sparsity.unit_id_to_channel_indices[unit]
            local_main = np.flatnonzero(sparse_channels == main)
            times = sorting.get_unit_spike_train(unit)/fs
            amps = np.asarray(amplitudes[indices[0][unit]])
            before = round(analyzer.get_extension('waveforms').params['ms_before']*fs/1000)
            t = (np.arange(template.shape[0])-before)/fs*1000
            fig, axes = plt.subplots(2, 4, figsize=(16, 8))
            ax = axes.flat
            ax[0].plot(t, template[:, main]); ax[0].set(title='Mean template', xlabel='ms', ylabel='µV')
            if len(waves) and len(local_main):
                sampled = rng.choice(len(waves), min(settings['sampled_waveforms'], len(waves)), replace=False)
                ax[1].plot(t, waves[sampled, :, local_main[0]].T, alpha=0.2)
            ax[1].set(title='Sampled waveforms', xlabel='ms', ylabel='µV')
            ax[2].hist(amps, bins=settings['amplitude_histogram_bins']); ax[2].set(title='Spike amplitudes', xlabel='µV')
            # Bounded pair-count ACG; stop and label if the diagnostic cap is hit.
            lags, cap_hit = [], False
            half = settings['acg_window_ms']/1000
            pairs = 0
            for i, stamp in enumerate(times):
                right = np.searchsorted(times, stamp+half, side='right')
                count = right-i-1
                if pairs+count > settings['max_acg_pairs']: cap_hit=True; break
                lags.extend((times[i+1:right]-stamp)*1000); pairs += count
            lags = np.asarray(lags)
            edges = np.arange(-settings['acg_window_ms'], settings['acg_window_ms']+settings['acg_bin_ms'], settings['acg_bin_ms'])
            ax[3].hist(np.concatenate((-lags, lags)), bins=edges)
            ax[3].set(title='ACG (truncated)' if cap_hit else 'ACG', xlabel='Lag (ms)')
            isi = np.diff(times)*1000
            ax[4].hist(isi[isi<=settings['diagnostic_isi_max_ms']], bins=np.linspace(0,settings['diagnostic_isi_max_ms'],settings['diagnostic_isi_bins']+1))
            ax[4].axvline(config['metrics']['isi_threshold_ms'], color='r'); ax[4].set(title='ISI diagnostic', xlabel='ms')
            duration = analyzer.recording.get_total_duration()
            rate_bin = settings['firing_rate_bin_sec']
            rate_edges = np.arange(0, duration+rate_bin, rate_bin)
            counts, _ = np.histogram(times, bins=rate_edges)
            ax[5].plot(rate_edges[:-1], counts/np.minimum(rate_bin, duration-rate_edges[:-1]))
            if len(amps)==len(times):
                twin = ax[5].twinx(); twin.scatter(times, amps, s=1, alpha=0.15); twin.set_ylabel('Amplitude µV')
            ax[5].set(title='Rate / amplitude drift', xlabel='s', ylabel='Hz')
            ax[6].eventplot(times[times<settings['raster_duration_sec']]); ax[6].set(title='Spike raster', xlabel='s')
            geometry = analyzer.recording.get_channel_locations()
            footprint = np.ptp(template, axis=0)
            ax[7].scatter(geometry[:,0], geometry[:,1], c=footprint, s=10)
            ax[7].set(title='Spatial footprint (PTP)', xlabel='x µm', ylabel='y µm'); ax[7].set_aspect('equal')
            row = decisions.loc[unit]
            fig.suptitle(f'Candidate {group} unit {unit}: FR={row.firing_rate:.3g}, SNR={row.snr:.3g}, ISI ratio={row.isi_violations_ratio:.3g}')
            fig.tight_layout(); fig.savefig(output / f'figures/unit_{group}_{len(selection):03d}.png', dpi=120); plt.close(fig)
    pd.DataFrame(selection, columns=['group','unit_id']).to_csv(output / 'metrics/qc_figure_units.csv', index=False)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=Path(__file__).with_name('config.yaml'))
    parser.add_argument('--output', type=Path, help='Fresh QC folder; update paths.qc for downstream analysis')
    args = parser.parse_args()
    run(load_config(args.config), args.output)
