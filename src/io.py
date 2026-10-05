"""Maxwell reading and strict concatenation compatibility checks."""
from __future__ import annotations
from datetime import datetime
from pathlib import Path
import numpy as np
import spikeinterface as si
import spikeinterface.extractors as se
from spikeinterface.core import BaseRecording
from src.utils import write_json


def read_recording(input_file: Path, config: dict) -> BaseRecording:
    """Load mapped channels lazily; require valid microvolt conversion and geometry."""
    if not input_file.is_file(): raise FileNotFoundError(input_file)
    raw = se.read_maxwell(input_file, **config['reader'])
    gains, offsets = raw.get_channel_gains(), raw.get_channel_offsets()
    if gains is None or offsets is None or not np.isfinite(gains).all() or not np.isfinite(offsets).all():
        raise ValueError('Missing/invalid conversion to uV')
    if np.any(gains == 0) or not np.isfinite(raw.get_channel_locations()).all():
        raise ValueError('Invalid conversion or geometry')
    return raw.select_channels(raw.channel_ids)


def save_recording_metadata(recording: BaseRecording, path: Path) -> None:
    """Persist per-segment metadata, geometry and conversion properties."""
    write_json(path, dict(fs=recording.sampling_frequency, dtype=str(recording.get_dtype()),
                         channel_ids=recording.channel_ids, locations=recording.get_channel_locations(),
                         properties={k: recording.get_property(k) for k in recording.get_property_keys()},
                         segments=[dict(samples=recording.get_num_samples(s),
                                        duration_sec=recording.get_num_samples(s)/recording.sampling_frequency)
                                   for s in range(recording.get_num_segments())]))


def validate_concatenation(files: list[Path], recordings: list[BaseRecording], config: dict) -> list[dict]:
    """Require explicit chip/config/time evidence plus exact channel compatibility."""
    evidence = config['concatenation']
    if not evidence['confirmed'] or evidence['max_start_gap_sec'] is None:
        raise ValueError('Concatenation requires explicit confirmation and max_start_gap_sec')
    entries = evidence['recordings']
    if len(entries) != len(files) or len(files) < 2: raise ValueError('Missing concatenation evidence')
    if len(set(p.resolve() for p in files)) != len(files): raise ValueError('Duplicate concatenation input')
    if float(evidence['max_start_gap_sec']) <= 0: raise ValueError('Invalid allowed time gap')
    times = []
    for path, entry in zip(files, entries):
        if Path(entry['input_file']).resolve() != path.resolve(): raise ValueError('Evidence/input order mismatch')
        if not entry.get('chip_id') or not entry.get('configuration_id'): raise ValueError('Missing chip/config identity')
        stamp = datetime.fromisoformat(entry['recorded_at'])
        if stamp.tzinfo is None: raise ValueError('recorded_at requires a timezone')
        times.append(stamp)
    if len({e['chip_id'] for e in entries}) != 1 or len({e['configuration_id'] for e in entries}) != 1:
        raise ValueError('Different chip or electrode configuration')
    if times != sorted(times) or (max(times)-min(times)).total_seconds() > evidence['max_start_gap_sec']:
        raise ValueError('Recording dates not chronological or exceed declared short-period limit')
    first = recordings[0]
    for r in recordings:
        if r.get_num_segments() != 1: raise ValueError('Concatenation currently requires single-segment inputs')
        if r.sampling_frequency != first.sampling_frequency: raise ValueError('Sampling frequency mismatch')
        if r.get_dtype() != first.get_dtype(): raise ValueError('Raw dtype mismatch')
        for a, b in ((r.channel_ids, first.channel_ids), (r.get_channel_locations(), first.get_channel_locations()),
                     (r.get_channel_gains(), first.get_channel_gains()), (r.get_channel_offsets(), first.get_channel_offsets()),
                     (r.get_property('electrode'), first.get_property('electrode'))):
            if a is None or b is None or not np.array_equal(a, b): raise ValueError('Channel mapping/geometry/scaling mismatch')
    mapping, offset = [], 0
    for path, r, entry in zip(files, recordings, entries):
        samples = r.get_num_samples()
        mapping.append(dict(input_file=str(path.resolve()), start_frame=offset, end_frame=offset+samples,
                            num_samples=samples, fs=r.sampling_frequency, **{k: entry[k] for k in ('chip_id','configuration_id','recorded_at')}))
        offset += samples
    return mapping
