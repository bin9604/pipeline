"""Maxwell raw.h5 → mapped Recording → lazy filtered Recording + bounded QC.

No complete binary is written. Change experimental settings in config.yaml.
"""
from __future__ import annotations
import argparse
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import spikeinterface as si
import spikeinterface.extractors as se
import spikeinterface.preprocessing as sp

from utils import (load_config, run_record, file_identity, validate_metadata,
                   validate_scaling, validate_bandpass, write_json)


def run(config, output=None):
    input_file = Path(config['paths']['input'])
    output = Path(output or config['paths']['preprocessing']).resolve()
    with run_record(output, '01_preprocessing', [input_file], config) as record:
        # ============================================================
        # 1. Read Maxwell and keep explicit mapped channel IDs/order
        # ============================================================
        input_identity = file_identity(input_file)
        recording = se.read_maxwell(input_file, **config['reader'])
        # Maxwell/Neo can expose extra signal rows when channel_ids=None.
        recording = recording.select_channels(recording.get_channel_ids())
        metadata = validate_metadata(recording)
        fs, samples = metadata['sampling_frequency_hz'], metadata['num_samples']
        ids = recording.get_channel_ids()
        gains, offsets = recording.get_channel_gains(), recording.get_channel_offsets()
        print(f"fs={fs} Hz; duration={samples/fs:.6f} s; channels={len(ids)}; physical unit=uV")
        print('Preprocessing parameters:', config['preprocessing'])
        pd.DataFrame(dict(channel_id=ids, gain_to_uV=gains, offset_to_uV=offsets,
                          x_um=metadata['geometry_um'][:, 0], y_um=metadata['geometry_um'][:, 1]
                          )).to_csv(output / 'metadata/channel_map.csv', index=False)

        # ============================================================
        # 2. Bandpass: lazy float ADC counts, retained gains, zero offset
        # ============================================================
        parameters = config['preprocessing']
        validate_bandpass(fs, parameters['freq_min'], parameters['freq_max'])
        if np.dtype(parameters['dtype']).kind != 'f':
            raise ValueError('Filtered Recording must use floating point dtype')
        recording_filtered = sp.bandpass_filter(recording, **parameters)
        if not np.array_equal(recording_filtered.get_channel_gains(), gains):
            raise ValueError('Filtering changed channel gains')
        if not np.all(recording_filtered.get_channel_offsets() == 0):
            raise ValueError('Expected zero offset after bandpass')
        if (recording_filtered.get_num_samples() != samples or
            not np.array_equal(recording_filtered.get_channel_ids(), ids) or
            not np.array_equal(recording_filtered.get_channel_locations(), metadata['geometry_um'])):
            raise ValueError('Filtering changed samples or channel mapping/geometry')
        recording.dump_to_json(output / 'metadata/raw_recording.json')
        recording_filtered.dump_to_json(output / 'metadata/filtered_recording.json')
        reloaded = si.load(output / 'metadata/filtered_recording.json')
        write_json(output / 'metadata/filter_parameters.json', dict(
            requested=parameters,
            effective={k: v for k, v in recording_filtered._kwargs.items() if k != 'recording'},
            coefficients=recording_filtered._recording_segments[0].coeff,
            gain_to_uV=recording_filtered.get_channel_gains(),
            offset_to_uV=recording_filtered.get_channel_offsets(), lazy=True))

        # ============================================================
        # 3. Short raw/filtered QC windows; include both recording edges
        # ============================================================
        settings = config['signal_qc']
        if min(settings['windows'], settings['window_sec'], settings['plot_channels'],
               settings['plot_duration_sec']) <= 0 or settings['context_sec'] < 0:
            raise ValueError('Invalid signal QC window settings')
        length = min(samples, max(2, round(settings['window_sec'] * fs)))
        rng = np.random.default_rng(config['seed'])
        starts = sorted(set([0, samples-length] + rng.integers(
            0, samples-length+1, size=int(settings['windows'])).tolist()))
        rows, checks = [], []
        for window_index, start in enumerate(starts):
            end = start + length
            numerical = recording.get_traces(start_frame=start, end_frame=end)
            raw_uV = recording.get_traces(start_frame=start, end_frame=end, return_in_uV=True)
            raw_error = validate_scaling(numerical, raw_uV, gains, offsets)
            traces = recording_filtered.get_traces(start_frame=start, end_frame=end)
            filtered_uV = recording_filtered.get_traces(start_frame=start, end_frame=end, return_in_uV=True)
            filtered_error = validate_scaling(traces, filtered_uV, gains, np.zeros(len(ids)))
            if not np.array_equal(traces, reloaded.get_traces(start_frame=start, end_frame=end)):
                raise ValueError('Lazy Recording reload changed traces')
            pad = round(settings['context_sec'] * fs)
            left, right = max(0, start-pad), min(samples, end+pad)
            expanded = recording_filtered.get_traces(start_frame=left, end_frame=right, return_in_uV=True)
            context_error = float(np.max(np.abs(filtered_uV-expanded[start-left:end-left])))
            if context_error > settings['context_atol_uV']:
                raise ValueError('Filter context discrepancy exceeds configured uV tolerance')
            checks.append(dict(window=window_index, start_frame=start, end_frame=end,
                               raw_scaling_error_uV=raw_error, filtered_scaling_error_uV=filtered_error,
                               context_error_uV=context_error, reload_equal=True))
            for signal, values in [('raw', raw_uV), ('filtered', filtered_uV)]:
                median = np.median(values, axis=0)
                mad = np.median(np.abs(values-median), axis=0)/0.6744897501960817
                for c, channel in enumerate(ids):
                    rows.append(dict(window=window_index, channel_id=channel, signal=signal,
                                     median_uV=float(median[c]), mad_sigma_uV=float(mad[c]),
                                     ptp_uV=float(np.ptp(values[:, c])), flat=bool(np.ptp(values[:, c]) == 0)))
            n_channels = min(len(ids), int(settings['plot_channels']))
            n = min(length, max(2, round(settings['plot_duration_sec']*fs)))
            fig, axes = plt.subplots(n_channels, 2, figsize=(12, 2*n_channels), squeeze=False)
            for c in range(n_channels):
                for ax, values, title in zip(axes[c], (raw_uV, filtered_uV), ('Raw', 'Filtered')):
                    ax.plot((np.arange(n)+start)/fs, values[:n, c], linewidth=0.5)
                    ax.set(title=title, ylabel=f'{ids[c]} / uV', xlabel='Time (s)')
                    ax.ticklabel_format(axis='x', useOffset=False)
            fig.tight_layout()
            fig.savefig(output / f'figures/signal_{window_index:02d}.png', dpi=120)
            plt.close(fig)
        pd.DataFrame(rows).to_csv(output / 'metrics/signal_qc.csv', index=False)
        write_json(output / 'metadata/window_checks.json', checks)
        fig, ax = plt.subplots()
        ax.scatter(metadata['geometry_um'][:, 0], metadata['geometry_um'][:, 1], s=5)
        ax.set(xlabel='x (um)', ylabel='y (um)', title='All mapped electrodes')
        ax.set_aspect('equal')
        fig.tight_layout(); fig.savefig(output / 'figures/geometry.png'); plt.close(fig)

        # ============================================================
        # 4. Save provenance. Stage 02 loads this JSON and the lazy Recording
        # ============================================================
        metadata.update(input_identity=input_identity, filtered_numerical_unit='ADC counts',
                        filtered_offset_to_uV=recording_filtered.get_channel_offsets(), lazy=True)
        write_json(output / 'metadata/recording.json', metadata)
        record.update(limited_sampling=True, sampled_windows=len(checks), channels_removed=0)
        record['warnings'].append('Bounded QC is not full-recording validation; MAD is not unit SNR.')
        if any(row['flat'] for row in rows):
            record['warnings'].append('Flat sampled channels/windows require manual review; none removed.')
        if samples/fs < config['isi_analysis']['interval_sec']:
            record['warnings'].append('Recording is shorter than the downstream window; explicit short-recording policy applies.')
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=Path(__file__).with_name('config.yaml'))
    parser.add_argument('--output', type=Path, help='Fresh output folder; update paths.preprocessing for later stages')
    args = parser.parse_args()
    run(load_config(args.config), args.output)
