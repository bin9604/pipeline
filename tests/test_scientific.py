"""Scientific/data-safety regression tests; every sorter call is mocked or forbidden."""
import copy
from contextlib import contextmanager
import gc
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd
import spikeinterface as si
import spikeinterface.sorters as ss

from utils import (load_config, read_json, write_json, validate_metadata, validate_scaling,
                   validate_bandpass, binary_quantization, check_input_identity)

ROOT = Path(__file__).resolve().parents[1]


def load_stage(filename):
    spec = importlib.util.spec_from_file_location(filename.removesuffix('.py'), ROOT / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


preprocessing = load_stage('01_preprocessing.py')
sorting_stage = load_stage('02_sorting.py')
qc = load_stage('03_qc_curation.py')
analysis = load_stage('04_analysis.py')


class ScientificTests(unittest.TestCase):
    def setUp(self):
        self.config = load_config(ROOT / 'config.yaml')
        # Global tripwire: no test may accidentally launch real Kilosort2.
        self.tripwire = patch('spikeinterface.sorters.run_sorter', side_effect=AssertionError('Real sorter forbidden'))
        self.sorter_mock = self.tripwire.start()
        self.addCleanup(self.tripwire.stop)
        cls = ss.sorter_dict['kilosort2']
        self.addCleanup(setattr, cls, 'kilosort2_path', getattr(cls, 'kilosort2_path', None))
        environment_patch = patch.dict('os.environ')
        environment_patch.start()
        self.addCleanup(environment_patch.stop)

    @contextmanager
    def workspace(self):
        temporary = tempfile.TemporaryDirectory()
        try:
            yield temporary.name
        finally:
            self.sorter_mock.reset_mock()
            gc.collect()
            temporary.cleanup()

    def test_inclusive_boundaries_invalids_and_all_reasons(self):
        table = pd.DataFrame(dict(isi_violations_ratio=[.3, .31, np.nan, np.inf],
                                  firing_rate=[.05, .04, .1, -np.inf], snr=[5, 4, np.inf, np.nan]),
                             index=[8, 20, 90, 91])
        decisions = qc.curate_metrics(table, self.config['curation'])
        self.assertEqual(decisions.accepted.tolist(), [True, False, False, False])
        self.assertEqual(decisions.loc[20, 'rejection_reason'], 'high_isi_violation;low_firing_rate;low_snr')
        self.assertEqual(decisions.loc[90, 'rejection_reason'], 'invalid_isi_violations_ratio;invalid_snr')
        self.assertIn('invalid_firing_rate', decisions.loc[91, 'rejection_reason'])
        self.assertEqual(decisions.loc[8, 'candidate_status'], 'candidate accepted')
        self.assertEqual(len(qc.curate_metrics(table.iloc[:0], self.config['curation'])), 0)

    def review_notebooks(self, config, names):
        """Execute review code against synthetic saved artifacts with sorter forbidden."""
        for name in names:
            notebook = json.loads((ROOT/'notebooks'/name).read_text(encoding='utf-8'))
            namespace = {'display': lambda *args: None}
            with patch('utils.load_config', return_value=config):
                for cell in notebook['cells']:
                    if cell['cell_type'] == 'code':
                        exec(compile(''.join(cell['source']), name, 'exec'), namespace)
            import matplotlib.pyplot as plt
            plt.close('all')
            # Drop references to SI disk-backed recordings/analyzers before cleanup.
            namespace.clear()
            gc.collect()

    def test_invalid_thresholds_and_duplicate_units(self):
        table = pd.DataFrame(dict(isi_violations_ratio=[.2], firing_rate=[.1], snr=[6]))
        for value in (np.nan, np.inf, -1):
            limits = {**self.config['curation'], 'snr_min': value}
            with self.assertRaises(ValueError):
                qc.curate_metrics(table, limits)
        with self.assertRaises(ValueError):
            qc.curate_metrics(pd.concat([table, table]), self.config['curation'])

    def test_scaling_shape_nonfinite_and_nontrivial_offset(self):
        numerical = np.array([[10, 100], [20, 200]], dtype='uint16')
        gains, offsets = np.array([.2, .3]), np.array([-5., -7.])
        physical = numerical.astype('float32')*gains+offsets
        self.assertLess(validate_scaling(numerical, physical, gains, offsets), 1e-6)
        for invalid in (physical*2, physical[:, :1], np.full_like(physical, np.nan), np.full_like(physical, np.inf)):
            with self.assertRaises(ValueError):
                validate_scaling(numerical, invalid, gains, offsets)

    def test_binary_physical_quantization_beyond_overflow(self):
        values = np.array([[-2.9, 3.8], [7.2, -4.1]], dtype='float32')
        encoded, error = binary_quantization(values, np.array([.2, .5]), 1.)
        np.testing.assert_array_equal(encoded, [[-2, 3], [7, -4]])
        self.assertGreater(error, .3)
        with self.assertRaises(ValueError):
            binary_quantization(values, np.array([10., 10.]), 1.)  # in range but physical quantization unsafe
        for value in (32768., -32769., np.nan, np.inf):
            with self.assertRaises(ValueError):
                binary_quantization(np.array([[value]]), np.ones(1), 1.)
        encoded, _ = binary_quantization(np.array([[-32768., 32767.]]), np.ones(2), 1.)
        self.assertEqual(encoded.tolist(), [[-32768, 32767]])

    def test_metadata_and_nyquist(self):
        recording, _ = si.generate_ground_truth_recording(durations=[.1], num_channels=4, num_units=1,
                                                          sampling_frequency=20000, seed=2)
        recording.set_channel_gains(np.ones(4)); recording.set_channel_offsets(np.zeros(4))
        self.assertEqual(validate_metadata(recording)['num_channels'], 4)
        recording.set_channel_gains([1, 1, 0, 1])
        with self.assertRaises(ValueError):
            validate_metadata(recording)
        for fs, low, high in [(12000, 300, 6000), (20000, -1, 6000), (np.nan, 300, 6000)]:
            with self.assertRaises(ValueError):
                validate_bandpass(fs, low, high)

    def test_short_window_half_open_bounds_and_normalization(self):
        params = copy.deepcopy(self.config['isi_analysis'])
        frames = np.arange(40)*400
        row, _ = analysis.fit_isi(frames, 20000, 179.83, params)
        self.assertEqual(row['status'], 'short_recording_skipped')
        params.update(short_recording_policy='use_available', interval_start_sec=.2, interval_sec=.4,
                      minimum_spikes=2, isi_min_ms=8., isi_max_ms=100.)
        row, histogram = analysis.fit_isi(np.array([2000, 4000, 6000, 8000, 10000, 12000]), 20000, 2, params)
        self.assertEqual(row['total_spikes'], 4)  # [0.2,0.6), excludes spike at 0.6
        if histogram is not None:
            self.assertAlmostEqual(histogram['y'].sum(), 1.)
        params['histogram_normalization'] = 'density'
        row, histogram = analysis.fit_isi(frames, 20000, 2, params)
        if histogram is not None:
            self.assertAlmostEqual(np.sum(histogram['y']*np.diff(histogram['edges'])), 1.)
        params['short_recording_policy'] = 'typo'
        with self.assertRaises(ValueError):
            analysis.fit_isi(frames, 20000, 2, params)

    def test_inclusive_isi_bounds_and_no_interval_across_window_edge(self):
        params = copy.deepcopy(self.config['isi_analysis'])
        params.update(interval_sec=1., minimum_spikes=2, interval_start_sec=.2)
        frames = np.array([1000, 4000, 4160, 6160, 6320, 8320, 24000])
        row, _ = analysis.fit_isi(frames, 20000, 2., params)
        self.assertEqual(row['total_spikes'], 5)
        self.assertEqual(row['n_ISI_8_100ms'], 4)

    def make_preprocessing(self, tmp, zero=False):
        config = copy.deepcopy(self.config)
        for name in ('preprocessing', 'sorting', 'qc', 'analysis'):
            config['paths'][name] = str(Path(tmp)/name)
        input_file = Path(tmp)/'synthetic_input'
        input_file.write_text('SYNTHETIC test; not Maxwell data', encoding='utf-8')
        config['paths']['input'] = str(input_file)
        config['signal_qc'].update(windows=1, window_sec=.05, plot_channels=1)
        recording, sorting = si.generate_ground_truth_recording(durations=[2], sampling_frequency=20000,
            num_channels=8, num_units=3, seed=42)
        recording.set_channel_gains(np.ones(8)); recording.set_channel_offsets(np.zeros(8))
        recording = recording.save(folder=Path(tmp)/'input_recording', progress_bar=False)
        with patch.object(preprocessing.se, 'read_maxwell', return_value=recording):
            preprocessing.run(config)
        if zero:
            sorting = si.NumpySorting.from_unit_dict({}, 20000)
        return config, sorting

    def make_mock_sorting(self, config, sorting):
        """Fixture for downstream tests; distinctly marked synthetic provenance."""
        output = Path(config['paths']['sorting']); (output/'metadata').mkdir(parents=True)
        metadata = read_json(Path(config['paths']['preprocessing'])/'metadata/recording.json')
        sorting.save(folder=output/'sorting_raw')
        write_json(output/'run.json', dict(status='completed', sorting_started=True, validation='synthetic fixture'))
        write_json(output/'metadata/sorting_source.json', dict(preprocessing=config['paths']['preprocessing'],
            input_identity=metadata['input_identity'], num_samples=metadata['num_samples'], kilosort2_commit=None))

    def test_lazy_reload_preflight_no_sorter_and_refuse_overwrite(self):
        with self.workspace() as tmp:
            config, _ = self.make_preprocessing(tmp)
            preprocessing_folder = Path(config['paths']['preprocessing'])
            metadata = read_json(preprocessing_folder/'metadata/recording.json')
            self.assertTrue(metadata['lazy'])
            self.assertFalse(list(preprocessing_folder.rglob('*.dat')))
            self.review_notebooks(config, ['01_signal_review.ipynb'])
            output = sorting_stage.run(config)
            self.assertFalse(read_json(output/'run.json')['sorting_started'])
            self.assertFalse(list(output.rglob('*.dat')))
            self.sorter_mock.assert_not_called()
            with self.assertRaises(FileExistsError):
                preprocessing.run(config)
            with self.assertRaises(FileExistsError):
                sorting_stage.run(config)
            Path(config['paths']['input']).write_text('changed source', encoding='utf-8')
            with self.assertRaises(ValueError):
                check_input_identity(metadata['input_identity'])

    def test_missing_runtime_never_starts_sorter(self):
        with self.workspace() as tmp:
            config, _ = self.make_preprocessing(tmp)
            with self.assertRaises(RuntimeError):
                sorting_stage.run(config, start_sorting=True)
            self.assertFalse(read_json(Path(config['paths']['sorting'])/'run.json')['sorting_started'])
            self.sorter_mock.assert_not_called()

    def test_actual_writer_and_mock_sorter_scaling_path(self):
        with self.workspace() as tmp:
            config, synthetic_sorting = self.make_preprocessing(tmp)
            config['paths']['kilosort2'] = tmp
            config['sorter']['runtime_confirmed'] = True
            config['sorter']['max_quantization_error_uV'] = 1.
            cls = ss.sorter_dict['kilosort2']
            def fake_sorter(name, recording, folder, **kwargs):
                self.assertEqual(name, 'kilosort2')
                self.assertFalse(kwargs['keep_good_only'])
                folder = Path(folder)/'sorter_output'; folder.mkdir(parents=True)
                # Only simulate the sorter export; actual short SI writer is tested separately by stage 02.
                recording.get_traces().astype('int16').tofile(folder/'recording.dat')
                return synthetic_sorting
            self.sorter_mock.side_effect = fake_sorter
            gpu_probe = type('Result', (), {'returncode': 0, 'stdout': 'MOCK GPU', 'stderr': ''})()
            with patch.object(cls, 'is_installed', return_value=True), patch.object(cls, 'check_compiled', return_value=False), \
                 patch.object(cls, 'get_sorter_version', return_value='MOCK KS2'), \
                 patch.object(sorting_stage.shutil, 'which', return_value='MOCK executable'), \
                 patch.object(sorting_stage.subprocess, 'run', return_value=gpu_probe), \
                 patch.object(sorting_stage, 'kilosort_commit', return_value='synthetic-commit'):
                output = sorting_stage.run(config, start_sorting=True)
            self.assertTrue(read_json(output/'metadata/binary_scaling.json')['probe_writer_matches'])
            self.assertTrue(read_json(output/'metadata/sorting_source.json')['actual_binary_sample_check'])
            self.assertTrue((output/'sorting_raw').exists())
            self.assertEqual(self.sorter_mock.call_count, 1)

    def test_synthetic_analyzer_curation_and_analysis_independent(self):
        with self.workspace() as tmp:
            config, synthetic_sorting = self.make_preprocessing(tmp)
            self.make_mock_sorting(config, synthetic_sorting)
            # Snapshot all raw Sorting files to prove QC does not modify them.
            raw_folder = Path(config['paths']['sorting'])/'sorting_raw'
            before = {str(p.relative_to(raw_folder)): p.read_bytes() for p in raw_folder.rglob('*') if p.is_file()}
            config['metrics']['definitions_acknowledged'] = True
            config['analyzer'].update(noise_chunk_size=1000, max_spikes_per_unit=30)
            config['visualization']['units_per_group'] = 1
            config['curation']['snr_min'] = 20
            output = qc.run(config)
            table = pd.read_csv(output/'metrics/quality_metrics.csv')
            self.assertEqual(len(table), 3)
            self.assertTrue(np.isfinite(table.snr).all())
            np.testing.assert_allclose(table.firing_rate, table.num_spikes/2)
            analyzer = si.load(output/'analyzer')
            noise = analyzer.get_extension('noise_levels').get_data()
            for i, unit in enumerate(analyzer.unit_ids):
                main = analyzer.get_main_channels(outputs='index')[i]
                template = analyzer.get_extension('templates').get_unit_template(unit)
                self.assertAlmostEqual(table.iloc[i].snr, np.abs(template[:, main]).max()/noise[main], places=5)
            self.assertTrue(list((output/'figures').glob('unit_*.png')))
            self.assertTrue(list((output/'figures').glob('unit_rejected_*.png')))
            del analyzer
            after = {str(p.relative_to(raw_folder)): p.read_bytes() for p in raw_folder.rglob('*') if p.is_file()}
            self.assertEqual(before, after)
            config['isi_analysis'].update(conventions_acknowledged=True, interval_sec=2., minimum_spikes=2)
            # Analysis must work even when Recording loading is forbidden.
            original_load = si.load
            def curated_only(path, **kwargs):
                self.assertEqual(Path(path), output/'sorting_curated')
                return original_load(path, **kwargs)
            with patch.object(analysis.si, 'load', side_effect=curated_only):
                analysis_output = analysis.run(config)
            result = pd.read_csv(analysis_output/'metrics/analysis.csv')
            self.assertGreater(len(result), 0)
            np.testing.assert_allclose(result.firing_rate_hz, result.total_spikes/2)
            self.review_notebooks(config, ['02_sorting_review.ipynb', '03_analysis.ipynb'])
            self.sorter_mock.assert_not_called()

    def test_zero_units_qc_analysis_and_rerun_thresholds(self):
        with self.workspace() as tmp:
            config, synthetic_sorting = self.make_preprocessing(tmp, zero=True)
            self.make_mock_sorting(config, synthetic_sorting)
            config['metrics']['definitions_acknowledged'] = True
            output = qc.run(config)
            self.assertEqual(len(si.load(output/'sorting_curated').unit_ids), 0)
            self.assertEqual(len(pd.read_csv(output/'metrics/curation_decisions.csv')), 0)
            self.assertFalse((output/'analyzer').exists())
            config['curation']['snr_min'] = 7.
            qc.run(config, Path(tmp)/'qc_new_threshold')
            config['isi_analysis']['conventions_acknowledged'] = True
            result = analysis.run(config)
            self.assertEqual(len(pd.read_csv(result/'metrics/analysis.csv')), 0)
            self.sorter_mock.assert_not_called()


if __name__ == '__main__':
    unittest.main()
