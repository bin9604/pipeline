"""Sample-based pre-sorting signal checks without channel removal."""
from pathlib import Path
import numpy as np
import pandas as pd
from spikeinterface.core import BaseRecording


def signal_qc(recording: BaseRecording, output: Path, seed: int, parameters: dict) -> pd.DataFrame:
    """Check five bounded all-channel windows and preserve sampling coordinates."""
    rng = np.random.default_rng(seed)
    rows = []
    for s in range(recording.get_num_segments()):
        total = recording.get_num_samples(s)
        length = min(total, int(recording.sampling_frequency*parameters['window_sec']))
        if length < 2: raise ValueError('Recording too short for QC')
        for start in rng.integers(0, total-length+1, size=parameters['windows_per_segment']):
            traces = recording.get_traces(segment_index=s, start_frame=int(start), end_frame=int(start+length), return_in_uV=True)
            if not np.isfinite(traces).all(): raise ValueError('Nonfinite samples in recording QC')
            rows.append(dict(segment=s, start_frame=int(start), end_frame=int(start+length),
                             flat_channels=int(np.sum(np.ptp(traces, axis=0)==0)),
                             max_abs_uV=float(np.max(np.abs(traces)))))
    table = pd.DataFrame(rows)
    table.to_csv(output / 'metrics/signal_qc.csv', index=False)
    return table
