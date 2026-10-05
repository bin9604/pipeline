"""Reproducible review figures; never manually edit unit acceptance."""
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from spikeinterface.core import SortingAnalyzer


def plot_unit_qc(analyzer: SortingAnalyzer, decisions: pd.DataFrame, output: Path, config: dict) -> None:
    """Sample accepted/borderline/rejected units and save eight-panel QC."""
    rng, settings = np.random.default_rng(config['seed']), config['visualization']
    limits = config['curation']
    distances = np.column_stack([
        np.abs(decisions.isi_violations_ratio.to_numpy()-limits['isi_violation_max'])/limits['isi_violation_max'],
        np.abs(decisions.firing_rate.to_numpy()-limits['firing_rate_min_hz'])/limits['firing_rate_min_hz'],
        np.abs(decisions.snr.to_numpy()-limits['snr_min'])/limits['snr_min']])
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
            fig.suptitle(f'{group} unit {unit}: FR={row.firing_rate:.3g}, SNR={row.snr:.3g}, ISI ratio={row.isi_violations_ratio:.3g}')
            fig.tight_layout(); fig.savefig(output / f'figures/unit_{group}_{len(selection):03d}.png', dpi=120); plt.close(fig)
    pd.DataFrame(selection, columns=['group','unit_id']).to_csv(output / 'metrics/qc_figure_units.csv', index=False)
