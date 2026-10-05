"""Meaningful pipeline checks using known synthetic spikes, never a sorter."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
import pandas as pd
import spikeinterface as si
from src.utils import load_config
from src.curation import curate_metrics
from src.isi_analysis import fit_isi
from src.io import validate_concatenation
from src.pipeline import finish_sorting, export_spike_mapping, run_single_recording
from src.preprocessing import preprocess_recording


class PipelineTests(unittest.TestCase):
    """Exercise boundaries and real SI postprocessing API without KS2."""
    def setUp(self):
        self.config = load_config(Path('config/sorting_config.yaml'))

    def test_curation_inclusive_missing_and_multiple_reasons(self):
        table = pd.DataFrame(dict(isi_violations_ratio=[.3,.31,np.nan],
                                  firing_rate=[.05,.04,.1], snr=[5.,4.,np.inf]), index=[8,20,90])
        actual = curate_metrics(table, self.config['curation'])
        self.assertEqual(actual.accepted.tolist(), [True,False,False])
        self.assertEqual(actual.loc[20,'rejection_reason'], 'high_isi_violation;low_firing_rate;low_snr')
        self.assertIn('invalid_snr', actual.loc[90,'rejection_reason'])

    def test_isi_short_interval_and_strict_r2(self):
        params = self.config['isi_analysis']
        row, _ = fit_isi(np.arange(100)*100, 20000, 179.83, params)
        self.assertEqual(row['status'], 'short_recording_skipped')
        params['short_recording_policy'] = 'use_available'
        frames = np.arange(40)*400
        row, data = fit_isi(frames, 20000, 2, params)
        self.assertEqual(row['total_spikes'],40)
        self.assertEqual(row['n_ISI_8_100ms'],39)
        self.assertFalse(row['complete_interval'])
        if row['status']=='fit_completed':
            self.assertEqual(row['exponential_classification'], row['R2']>.9)

    def test_source_boundary_mapping(self):
        sorting = si.NumpySorting.from_unit_dict({7:np.array([0,9,10,19])}, 1000)
        mapping = [dict(input_file='a',start_frame=0,end_frame=10,num_samples=10,fs=1000),
                   dict(input_file='b',start_frame=10,end_frame=20,num_samples=10,fs=1000)]
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'spikes.csv'; export_spike_mapping(sorting,mapping,p)
            rows=pd.read_csv(p)
            self.assertEqual(rows.local_frame.tolist(),[0,9,0,9])
            self.assertEqual(rows.input_file.tolist(),['a','a','b','b'])

    def test_concatenation_rejects_missing_identity(self):
        with self.assertRaises(ValueError): validate_concatenation([Path('a'),Path('b')], [], self.config)

    def test_dry_run_never_calls_sorter_and_refuses_overwrite(self):
        recording,_=si.generate_ground_truth_recording(durations=[1],sampling_frequency=20000,
            num_channels=4,num_units=1,seed=12)
        with tempfile.TemporaryDirectory() as tmp:
            sentinel=Path(tmp)/'synthetic_input'; sentinel.write_text('test only')
            output=Path(tmp)/'dry_run'
            recording=recording.save(folder=Path(tmp)/'recording', progress_bar=False)
            with patch('src.pipeline.read_recording',return_value=recording), patch(
                    'src.pipeline.run_kilosort2',side_effect=AssertionError('No sorter during dry run')):
                run_single_recording(sentinel,self.config,output,dry_run=True)
                with self.assertRaises(FileExistsError): run_single_recording(sentinel,self.config,output,dry_run=True)
            import json
            manifest=json.loads((output/'run_manifest.json').read_text())
            self.assertFalse(manifest['sorting_started'])
            del recording
            import gc
            gc.collect()

    def test_concatenation_preserves_boundaries_and_rejects_geometry(self):
        recording, _ = si.generate_ground_truth_recording(durations=[1], sampling_frequency=20000,
            num_channels=4,num_units=1,seed=12)
        recording.set_property('electrode',np.arange(4))
        self.config['concatenation'] = dict(confirmed=True,max_start_gap_sec=60,recordings=[
            dict(input_file='a',chip_id='chip',configuration_id='config',recorded_at='2026-10-05T10:00:00+08:00'),
            dict(input_file='b',chip_id='chip',configuration_id='config',recorded_at='2026-10-05T10:00:30+08:00')])
        mapping=validate_concatenation([Path('a'),Path('b')],[recording,recording],self.config)
        self.assertEqual(mapping[1]['start_frame'],20000)
        first=preprocess_recording(recording,self.config['preprocessing'])
        combined=si.concatenate_recordings([first,first])
        actual=combined.get_traces(start_frame=19990,end_frame=20010)
        expected=np.concatenate([first.get_traces(start_frame=19990,end_frame=20000),first.get_traces(start_frame=0,end_frame=10)])
        np.testing.assert_array_equal(actual,expected)
        changed=recording.select_channels(recording.channel_ids[::-1])
        with self.assertRaises(ValueError): validate_concatenation([Path('a'),Path('b')],[recording,changed],self.config)

    def test_zero_units_remains_exportable(self):
        sorting=si.NumpySorting.from_unit_dict({},20000)
        with tempfile.TemporaryDirectory() as tmp:
            table=pd.DataFrame(columns=['isi_violations_ratio','firing_rate','snr'])
            output=Path(tmp); (output/'metrics').mkdir()
            from src.curation import save_curation
            curated,_=save_curation(sorting,table,output,self.config)
            self.assertEqual(len(curated.unit_ids),0)
            self.assertTrue((output/'metrics/accepted_units.csv').exists())

    def test_actual_analyzer_curation_figures_and_isi(self):
        config = self.config
        config['metrics']['definitions_acknowledged']=True
        config['isi_analysis']['conventions_acknowledged']=True
        config['isi_analysis']['interval_sec']=5
        config['isi_analysis']['minimum_spikes']=5
        config['analyzer']['noise_chunk_size']=1000
        config['analyzer']['max_spikes_per_unit']=30
        config['visualization']['units_per_group']=1
        config['curation']['snr_min']=20  # Test both accepted and rejected paths.
        recording, sorting = si.generate_ground_truth_recording(durations=[5], sampling_frequency=20000,
            num_channels=8, num_units=3, seed=42)
        mapping=[dict(input_file='synthetic',start_frame=0,end_frame=100000,num_samples=100000,fs=20000)]
        with tempfile.TemporaryDirectory() as tmp:
            output=Path(tmp)
            for folder in ('metadata','metrics','figures','logs'): (output/folder).mkdir()
            manifest={'warnings':[],'stages_completed':[]}
            with patch('spikeinterface.sorters.run_sorter', side_effect=AssertionError('Sorter must never run')):
                finish_sorting(sorting,recording,mapping,output,config,manifest)
            metrics=pd.read_csv(output/'metrics/quality_metrics.csv')
            self.assertEqual(len(metrics),3)
            self.assertTrue(np.isfinite(metrics.snr).all())
            np.testing.assert_allclose(metrics.firing_rate,metrics.num_spikes/5)
            analyzer=si.load(output/'analyzer')
            noise=analyzer.get_extension('noise_levels').get_data()
            for index,unit in enumerate(sorting.unit_ids):
                main=analyzer.get_main_channels(outputs='index')[index]
                template=analyzer.get_extension('templates').get_unit_template(unit)
                expected=np.max(np.abs(template[:,main]))/noise[main]
                self.assertAlmostEqual(metrics.loc[index,'snr'],expected,places=5)
            self.assertTrue((output/'sorting_raw').exists())
            self.assertTrue((output/'sorting_curated').exists())
            self.assertTrue((output/'metrics/isi_analysis.csv').exists())
            self.assertGreater(len(list((output/'figures').glob('unit_*.png'))),0)
            self.assertGreater(len(list((output/'figures').glob('unit_rejected_*.png'))),0)


if __name__ == '__main__': unittest.main()
