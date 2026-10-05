#!/usr/bin/env bash
# USER MUST EDIT: partition and account below; placeholders prevent accidental use.
#SBATCH --partition=USER_MUST_EDIT
#SBATCH --account=USER_MUST_EDIT
#SBATCH --gres=gpu:1
# Initial resource EXAMPLES ONLY, not verified cluster recommendations.
#SBATCH --cpus-per-task=4
#SBATCH --mem=32G
#SBATCH --time=08:00:00
#SBATCH --job-name=organoid_ks2
#SBATCH --output=ks2-%j.log
set -euo pipefail

# USER MUST EDIT: module names and conda initialization/environment.
module load USER_MUST_EDIT_MATLAB_MODULE USER_MUST_EDIT_CUDA_MODULE
source /USER_MUST_EDIT/miniconda3/etc/profile.d/conda.sh
conda activate USER_MUST_EDIT_ENVIRONMENT

# USER MUST EDIT: project root, explicit KS2 revision checkout, input/output config.
PROJECT_ROOT=/USER_MUST_EDIT/organoid_hdmea
export KILOSORT2_PATH=/USER_MUST_EDIT/Kilosort2_checkout
CONFIG=/USER_MUST_EDIT/organoid_hdmea/config.yaml
OUTPUT=/USER_MUST_EDIT/results/sorting_001
# USER MUST EDIT in CONFIG: paths.input/preprocessing/sorting/qc/analysis,
# paths.kilosort2 (or null to use the environment variable), runtime_confirmed.
cd "$PROJECT_ROOT"
nvidia-smi
# Run 01_preprocessing.py once on the server before submitting this job.
# Confirm MATLAB license, Parallel Computing Toolbox, gpuDevice and compiled
# KS2 CUDA MEX manually on a compute node; discovery alone is insufficient.
python -u 02_sorting.py --config "$CONFIG" --output "$OUTPUT" --run
