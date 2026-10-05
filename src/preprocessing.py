"""Lazy paper-cutoff bandpass preprocessing with explicit channel mapping."""
from __future__ import annotations

import numpy as np
from spikeinterface.core import BaseRecording
import spikeinterface.preprocessing as sp


def preprocess_recording(recording: BaseRecording, parameters: dict) -> BaseRecording:
    """Return a lazy float Recording; never change cutoffs to fit the input.

    Select all mapped IDs explicitly to protect Maxwell recordings with extra
    signal rows. This retains every recorded channel and its original order.
    Float output avoids rounding filtered data to integer ADC counts.
    """
    fs = float(recording.get_sampling_frequency())
    low, high = float(parameters['freq_min']), float(parameters['freq_max'])
    if not np.isfinite(fs) or not (0 < low < high < fs / 2):
        raise ValueError(f'Invalid bandpass {low}–{high} Hz for sampling rate {fs} Hz')
    if (low, high) != (300.0, 6000.0):
        raise ValueError('This paper-reproduction stage requires 300–6000 Hz')
    if np.dtype(parameters['dtype']).kind != 'f':
        raise ValueError('Filtered output must use floating point dtype')
    mapped = recording.select_channels(recording.get_channel_ids())
    return sp.bandpass_filter(mapped, **parameters)
