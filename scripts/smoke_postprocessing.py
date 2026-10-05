"""Persist synthetic downstream smoke-test artifacts; never invoke a sorter."""
import argparse
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import spikeinterface as si
from src.utils import load_config, run_context, write_json
from src.pipeline import finish_sorting


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    config=load_config(Path('config/sorting_config.yaml'))
    config['metrics']['definitions_acknowledged']=True
    config['isi_analysis'].update(conventions_acknowledged=True,interval_sec=5,minimum_spikes=5)
    config['analyzer'].update(noise_chunk_size=1000,max_spikes_per_unit=30)
    config['visualization']['units_per_group']=1
    with run_context(args.output,[],config,'synthetic_postprocessing_smoke_test') as manifest:
        manifest.update(sorting_started=False, synthetic=True)
        recording,sorting=si.generate_ground_truth_recording(durations=[5],sampling_frequency=20000,
            num_channels=8,num_units=3,seed=42)
        mapping=[dict(input_file='synthetic',start_frame=0,end_frame=100000,num_samples=100000,fs=20000)]
        write_json(args.output/'metadata/time_mapping.json',mapping)
        finish_sorting(sorting,recording,mapping,args.output,config,manifest)


if __name__=='__main__': main()
