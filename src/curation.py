"""Strict three-metric unit selection with explicit missing-value rejection."""
from pathlib import Path
import numpy as np
import pandas as pd
from spikeinterface.core import BaseSorting


def curate_metrics(metrics: pd.DataFrame, thresholds: dict) -> pd.DataFrame:
    """Use inclusive paper thresholds; missing/infinite metrics fail explicitly."""
    result = metrics.copy()
    rules = [('isi_violations_ratio', 'ISI_pass', thresholds['isi_violation_max'], False, 'high_isi_violation'),
             ('firing_rate', 'FR_pass', thresholds['firing_rate_min_hz'], True, 'low_firing_rate'),
             ('snr', 'SNR_pass', thresholds['snr_min'], True, 'low_snr')]
    reasons = [[] for _ in range(len(result))]
    for name, mask, limit, minimum, reason in rules:
        values = result[name].to_numpy(dtype=float)
        finite = np.isfinite(values)
        result[mask] = finite & ((values >= limit) if minimum else (values <= limit))
        for i, passed in enumerate(result[mask]):
            if not passed: reasons[i].append(reason if finite[i] else f'invalid_{name}')
    result['accepted'] = result[['ISI_pass', 'FR_pass', 'SNR_pass']].all(axis=1)
    result['rejection_reason'] = [';'.join(reason) for reason in reasons]
    return result


def save_curation(sorting: BaseSorting, metrics: pd.DataFrame, output: Path, config: dict) -> tuple[BaseSorting, pd.DataFrame]:
    """Preserve raw sorting and persist curated sorting plus all rejection reasons."""
    table = curate_metrics(metrics, config['curation'])
    table.index.name = 'unit_id'
    table.to_csv(output / 'metrics/curation_decisions.csv')
    table[table.accepted].to_csv(output / 'metrics/accepted_units.csv')
    table[~table.accepted].to_csv(output / 'metrics/rejected_units.csv')
    accepted = sorting.unit_ids[table.reindex(sorting.unit_ids)['accepted'].to_numpy(dtype=bool)]
    curated = sorting.select_units(accepted)
    curated.save(folder=output / 'sorting_curated')
    return curated, table
