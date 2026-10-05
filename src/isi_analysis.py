"""Downstream ISI fitting; never participates in sorting unit QC."""
from __future__ import annotations
from pathlib import Path
import warnings
import numpy as np
import pandas as pd
from scipy.optimize import curve_fit, OptimizeWarning
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from spikeinterface.core import BaseSorting


def fit_isi(spike_frames: np.ndarray, fs: float, available_sec: float, params: dict) -> tuple[dict, dict | None]:
    """Analyze one half-open interval with explicit short-recording conventions."""
    start, requested = params['interval_start_sec'], params['interval_sec']
    if start < 0 or requested <= 0: raise ValueError('Invalid ISI analysis interval')
    available = max(0, available_sec-start)
    duration = min(requested, available)
    times = spike_frames / fs
    selected = times[(times >= start) & (times < start+duration)]
    result = dict(total_spikes=int(len(selected)), interval_start_sec=start, interval_used_sec=duration,
                  complete_interval=bool(available >= requested), median_ISI_ms=np.nan,
                  n_ISI_8_100ms=0, a=np.nan, **{'lambda': np.nan}, R2=np.nan,
                  exponential_classification=False, status='pending')
    if available < requested and params['short_recording_policy'] == 'skip':
        result['status'] = 'short_recording_skipped'; return result, None
    if len(selected) < params['minimum_spikes']:
        result['status'] = 'insufficient_spikes'; return result, None
    all_isi = np.diff(selected)*1000
    retained = all_isi[(all_isi >= params['isi_min_ms']) & (all_isi <= params['isi_max_ms'])]
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


def run_isi_analysis(sorting: BaseSorting, mapping: list[dict], output: Path, config: dict) -> pd.DataFrame:
    """Fit curated units separately for every original recording, never across seams."""
    params = config['isi_analysis']
    if not params['conventions_acknowledged']:
        raise ValueError('Acknowledge ISI median/normalization/window conventions before analysis')
    rows, plots = [], 0
    for origin in mapping:
        for unit in sorting.unit_ids:
            frames = sorting.get_unit_spike_train(unit, segment_index=0)
            local = frames[(frames >= origin['start_frame']) & (frames < origin['end_frame'])]-origin['start_frame']
            row, histogram = fit_isi(local, origin['fs'], origin['num_samples']/origin['fs'], params)
            row.update(unit_id=unit, input_file=origin['input_file'], median_scope=params['median_scope'],
                       histogram_normalization=params['histogram_normalization'])
            rows.append(row)
            if histogram is not None and plots < config['visualization']['units_per_group']*3:
                fig, ax = plt.subplots(figsize=(7, 4))
                ax.stairs(histogram['y'], histogram['edges'], label='Normalized histogram')
                if histogram['fit'] is not None: ax.plot(histogram['x'], histogram['fit'], label='Exponential fit')
                ax.set(xlabel='ISI (ms)', ylabel=params['histogram_normalization'], title=f'Unit {unit}: {row["status"]}, R²={row["R2"]:.3f}')
                ax.legend(); fig.tight_layout(); fig.savefig(output / f'figures/isi_{plots:03d}.png', dpi=130); plt.close(fig)
                plots += 1
    columns = ['unit_id','input_file','total_spikes','median_ISI_ms','n_ISI_8_100ms','a','lambda','R2','exponential_classification','status']
    table = pd.DataFrame(rows) if rows else pd.DataFrame(columns=columns)
    table.to_csv(output / 'metrics/isi_analysis.csv', index=False)
    return table
