"""Existing curated Sorting only ? counts, firing rate, ISI and optional paper fit.

No Recording is loaded and no preprocessing/sorting is run. All source duration
information is read from the QC output, so this stage works without raw HDF5.
"""
from __future__ import annotations
import argparse
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from pathlib import Path
import warnings

import numpy as np
import pandas as pd
from scipy.optimize import curve_fit, OptimizeWarning
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import spikeinterface as si

from utils import load_config, run_record, read_json, write_json


def validate_analysis_parameters(params):
    numeric = [params[k] for k in ('interval_start_sec', 'interval_sec', 'isi_min_ms',
                                   'isi_max_ms', 'r2_min', 'refractory_ms')]
    if not np.isfinite(numeric).all() or params['interval_start_sec'] < 0 or params['interval_sec'] <= 0:
        raise ValueError('Invalid analysis window')
    if params['short_recording_policy'] not in ('skip', 'use_available'):
        raise ValueError('Invalid short recording policy')
    if params['median_scope'] not in ('all', 'retained'):
        raise ValueError('Invalid median scope')
    if params['histogram_normalization'] not in ('probability', 'density'):
        raise ValueError('Invalid normalization')
    if params['bin_width_method'] != 'median_divided_by_15':
        raise ValueError('Unsupported bin width rule')
    if not 0 < params['isi_min_ms'] < params['isi_max_ms'] or params['minimum_spikes'] < 2:
        raise ValueError('Invalid ISI bounds/minimum spikes')
    if params['max_histogram_bins'] < 1 or params['refractory_ms'] <= 0:
        raise ValueError('Invalid histogram cap/refractory threshold')


def select_window_frames(spike_frames, fs, available_sec, params):
    """Compare integer frames; decimal seconds avoid 0.2+0.4 boundary drift."""
    start = Decimal(str(params['interval_start_sec']))
    end = start + Decimal(str(params['interval_sec']))
    rate = Decimal(str(fs))
    first = int((start*rate).to_integral_value(rounding=ROUND_CEILING))
    last = min(round(available_sec*fs), int((end*rate).to_integral_value(rounding=ROUND_CEILING)))
    return spike_frames[(spike_frames >= first) & (spike_frames < last)]


def fit_isi(spike_frames, fs, available_sec, params) -> tuple[dict, dict | None]:
    """Analyze one half-open interval with explicit short-recording conventions."""
    validate_analysis_parameters(params)
    if not np.isfinite(fs) or fs <= 0 or not np.isfinite(available_sec) or available_sec < 0:
        raise ValueError('Invalid sampling rate/duration')
    spike_frames = np.asarray(spike_frames)
    if spike_frames.ndim != 1 or np.any(spike_frames < 0) or np.any(np.diff(spike_frames) < 0):
        raise ValueError('Invalid or unordered spike frames')
    start, requested = params['interval_start_sec'], params['interval_sec']
    if start < 0 or requested <= 0: raise ValueError('Invalid ISI analysis interval')
    available = max(0, available_sec-start)
    duration = min(requested, available)
    selected_frames = select_window_frames(spike_frames, fs, available_sec, params)
    selected = selected_frames/fs
    result = dict(total_spikes=int(len(selected)), interval_start_sec=start, interval_used_sec=duration,
                  complete_interval=bool(available >= requested), median_ISI_ms=np.nan,
                  n_ISI_8_100ms=0, a=np.nan, **{'lambda': np.nan}, R2=np.nan,
                  exponential_classification=False, status='pending')
    if available < requested and params['short_recording_policy'] == 'skip':
        result['status'] = 'short_recording_skipped'; return result, None
    if len(selected) < params['minimum_spikes']:
        result['status'] = 'insufficient_spikes'; return result, None
    interval_frames = np.diff(selected_frames)
    all_isi = interval_frames/fs*1000
    # Inclusive bounds in integer frames avoid loss of exact 8/100 ms intervals.
    frames_per_ms = Decimal(str(fs))/1000
    lower = int((Decimal(str(params['isi_min_ms']))*frames_per_ms).to_integral_value(rounding=ROUND_CEILING))
    upper = int((Decimal(str(params['isi_max_ms']))*frames_per_ms).to_integral_value(rounding=ROUND_FLOOR))
    retained = all_isi[(interval_frames >= lower) & (interval_frames <= upper)]
    result['n_ISI_8_100ms'] = len(retained)
    median_values = all_isi if params['median_scope'] == 'all' else retained
    if len(median_values) == 0 or len(retained) < 3:
        result['status'] = 'insufficient_retained_isi'; return result, None
    median = float(np.median(median_values)); width = median/15
    result.update(median_ISI_ms=median, bin_width_ms=width)
    if width <= 0:
        result['status'] = 'invalid_bin_width'; return result, None
    bins = int(np.ceil((params['isi_max_ms']-params['isi_min_ms'])/width))
    if bins > params['max_histogram_bins']:
        result['status'] = 'excessive_histogram_bins'; return result, None
    edges = params['isi_min_ms'] + np.arange(bins+1)*width
    counts, edges = np.histogram(retained, bins=edges)
    # Retain equal-width bins, including the final partial analysis-domain bin.
    y = counts / counts.sum()
    if params['histogram_normalization'] == 'density': y = y / width
    x = (edges[1:]+edges[:-1])/2
    data = dict(x=x, y=y, edges=edges, fit=None)
    if len(x) < 3 or np.sum((y-y.mean())**2) <= 0:
        result['status'] = 'degenerate_histogram'; return result, data
    def exponential(t, a, rate): return a*np.exp(-rate*t)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', OptimizeWarning)
            coefficients, _ = curve_fit(exponential, x, y, p0=(max(y.max(), 1e-6), 1/median),
                                        bounds=([0, 0], [np.inf, np.inf]), maxfev=20000)
        prediction = exponential(x, *coefficients)
        r2 = float(1-np.sum((y-prediction)**2)/np.sum((y-y.mean())**2))
        result.update(a=float(coefficients[0]), **{'lambda': float(coefficients[1])}, R2=r2,
                      exponential_classification=bool(r2 > params['r2_min']), status='fit_completed')
        data['fit'] = prediction
    except (RuntimeError, ValueError, OptimizeWarning) as error:
        result.update(status='fit_failed', fit_error=str(error))
    return result, data


def run(config, output=None):
    qc_folder = Path(config['paths']['qc'])
    output = Path(output or config['paths']['analysis']).resolve()
    with run_record(output, '04_analysis', [qc_folder / 'sorting_curated'], config) as record:
        # ============================================================
        # 1. Only curated Sorting; duration comes from QC source metadata
        # ============================================================
        previous = read_json(qc_folder / 'run.json')
        source = read_json(qc_folder / 'metadata/analysis_source.json')
        if previous['status'] != 'completed':
            raise ValueError('QC did not complete')
        sorting_curated = si.load(qc_folder / 'sorting_curated')
        fs, duration = source['sampling_frequency_hz'], source['duration_sec']
        if (sorting_curated.get_num_segments() != 1 or sorting_curated.get_sampling_frequency() != fs or
            not np.array_equal(sorting_curated.unit_ids, source['candidate_unit_ids']) or
            not np.isclose(duration, source['num_samples']/fs)):
            raise ValueError('Curated Sorting/source metadata mismatch')
        record['kilosort2_commit'] = source.get('kilosort2_commit')

        # ============================================================
        # 2. Explicit methodological conventions; never infer consensus
        # ============================================================
        parameters = config['isi_analysis']
        validate_analysis_parameters(parameters)
        if not parameters['conventions_acknowledged']:
            raise ValueError('Acknowledge analysis window, normalization, median and short-recording conventions')
        print('Analysis conventions:', parameters)
        write_json(output / 'metadata/analysis_conventions.json', dict(
            **parameters, window_boundary='half-open [start, end)',
            recording_boundary='one segment, no cross-recording ISI',
            refractory_definition='fraction of adjacent within-window ISI strictly < refractory_ms',
            short_skip='report observed spike count and window; skip rate/ISI statistics and fit',
            probability_definition='retained histogram counts / total retained ISI',
            density_definition='probability / bin width in ms',
            biological_interpretation='automatic curated units remain candidates'))

        # ============================================================
        # 3. Counts, rate and adjacent ISI inside the selected window
        # ============================================================
        rows, plotted = [], 0
        for unit_index, unit in enumerate(sorting_curated.unit_ids):
            frames = sorting_curated.get_unit_spike_train(unit)
            if np.any(frames < 0) or np.any(frames >= source['num_samples']) or np.any(np.diff(frames) < 0):
                raise ValueError('Curated spike outside recording or not chronological')
            row, histogram = fit_isi(frames, fs, duration, parameters)
            start, used = row['interval_start_sec'], row['interval_used_sec']
            selected = select_window_frames(frames, fs, duration, parameters)
            isi_ms = np.diff(selected)/fs*1000
            row.update(unit_id=unit, firing_rate_hz=np.nan, adjacent_isi_count=0,
                       all_isi_median_ms=np.nan, refractory_isi_count=0,
                       refractory_fraction=np.nan, median_scope=parameters['median_scope'],
                       histogram_normalization=parameters['histogram_normalization'])
            if row['status'] != 'short_recording_skipped' and used > 0:
                row.update(firing_rate_hz=len(selected)/used, adjacent_isi_count=len(isi_ms),
                           all_isi_median_ms=float(np.median(isi_ms)) if len(isi_ms) else np.nan,
                           refractory_isi_count=int(np.count_nonzero(isi_ms < parameters['refractory_ms'])),
                           refractory_fraction=float(np.mean(isi_ms < parameters['refractory_ms'])) if len(isi_ms) else np.nan)
                np.savez_compressed(output / f'metrics/isi_unit_{unit_index:05d}.npz',
                                    unit_id=unit, selected_spike_frames=selected, isi_ms=isi_ms)
            rows.append(row)

            # ============================================================
            # 4. Retained ISI histogram + optional inherited exponential fit
            # ============================================================
            if histogram is not None and plotted < config['visualization']['units_per_group']*3:
                fig, ax = plt.subplots(figsize=(7, 4))
                ax.stairs(histogram['y'], histogram['edges'], label='Retained ISI')
                if histogram['fit'] is not None:
                    ax.plot(histogram['x'], histogram['fit'], label='Exploratory exponential fit')
                ax.set(xlabel='ISI (ms)', ylabel=parameters['histogram_normalization'],
                       title=f'Candidate unit {unit}: {row["status"]}')
                ax.legend(); fig.tight_layout()
                fig.savefig(output / f'figures/isi_{unit_index:05d}.png', dpi=130); plt.close(fig)
                plotted += 1
        columns = ['unit_id', 'total_spikes', 'firing_rate_hz', 'interval_start_sec', 'interval_used_sec',
                   'complete_interval', 'status', 'adjacent_isi_count', 'all_isi_median_ms',
                   'refractory_isi_count', 'refractory_fraction', 'median_ISI_ms', 'n_ISI_8_100ms',
                   'a', 'lambda', 'R2', 'exponential_classification', 'median_scope', 'histogram_normalization']
        table = pd.DataFrame(rows) if rows else pd.DataFrame(columns=columns)
        table.to_csv(output / 'metrics/analysis.csv', index=False)
        record.update(curated_units=len(sorting_curated.unit_ids), analyzed_units=len(rows))
        record['warnings'].append('Normalization/median/short-recording choices are conventions, not confirmed paper definitions.')
        # Extension point: add a separately documented biological analysis here.
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=Path(__file__).with_name('config.yaml'))
    parser.add_argument('--output', type=Path, help='Fresh analysis output folder')
    args = parser.parse_args()
    run(load_config(args.config), args.output)
