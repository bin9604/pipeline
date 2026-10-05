"""Small signal, Nyquist, and actual-recording boundary checks for Stage 3."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import spikeinterface as si
import yaml
from src.preprocessing import preprocess_recording


def main() -> None:
    """Verify frequency discrimination and explicit Nyquist failure."""
    with Path('config/preprocessing_config.yaml').open(encoding='utf-8') as handle:
        params = yaml.safe_load(handle)['preprocessing']
    fs = 20000
    t = np.arange(fs) / fs
    signals = np.column_stack([np.sin(2*np.pi*f*t) for f in (100, 1000, 8000)]).astype('float32')
    recording = si.NumpyRecording(signals, sampling_frequency=fs)
    filtered = preprocess_recording(recording, params)
    result = filtered.get_traces(start_frame=2000, end_frame=18000)
    rms_ratio = np.sqrt(np.mean(result**2, axis=0)) / np.sqrt(np.mean(signals[2000:18000]**2, axis=0))
    assert rms_ratio[0] < 0.01 and rms_ratio[1] > 0.95 and rms_ratio[2] < 0.01, rms_ratio
    try:
        preprocess_recording(si.NumpyRecording(signals, sampling_frequency=12000), params)
    except ValueError:
        pass
    else:
        raise AssertionError('6000 Hz at Nyquist must fail explicitly')
    saved = si.load('results/Development_2953_5month/preprocessing_run_001/metadata/filtered_recording.json')
    for a, b in ((0, 1000), (saved.get_num_samples()-1000, saved.get_num_samples())):
        traces = saved.get_traces(start_frame=a, end_frame=b, return_in_uV=True)
        assert traces.shape == (1000, 1020) and np.isfinite(traces).all()
    print('PASS: 100/1000/8000 Hz RMS ratios:', rms_ratio)
    print('PASS: Nyquist rejection and actual first/last-window finite shape checks')


if __name__ == '__main__':
    main()
