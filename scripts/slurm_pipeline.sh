#!/bin/bash
#SBATCH --job-name=organoid-ks2
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=4
#SBATCH --mem=32G
#SBATCH --time=08:00:00
#SBATCH --output=slurm-%j.log
# Resource requests are initial engineering estimates, not measured requirements.
set -euo pipefail
: "${PIPELINE_ROOT:?Set PIPELINE_ROOT to the project checkout}"
: "${INPUT_FILE:?Set INPUT_FILE to raw HDF5}"
: "${OUTPUT_DIR:?Set a fresh output path on compute-node-accessible storage}"
: "${KILOSORT2_PATH:?Set the validated KS2 checkout}"
# Activate your prepared Python/MATLAB/CUDA environment before sbatch, or add
# site-specific module loads here. This script deliberately guesses no modules.
cd "$PIPELINE_ROOT"
python scripts/check_kilosort2_environment.py --output "$OUTPUT_DIR-preflight" --kilosort2-path "$KILOSORT2_PATH"
python scripts/run_pipeline.py --input "$INPUT_FILE" --output "$OUTPUT_DIR" --config "${CONFIG_FILE:-config/sorting_config.yaml}"
