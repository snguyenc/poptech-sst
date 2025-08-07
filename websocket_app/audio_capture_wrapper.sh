#!/bin/bash
# Wrapper script for Audio Capture Native App
# Ensures correct Python environment is used

# Activate conda environment and run the Python script
source /Users/sonnc/miniconda3/etc/profile.d/conda.sh
conda activate captureaudio
exec /Users/sonnc/miniconda3/envs/captureaudio/bin/python3 /Users/sonnc/Data/Project/poptech/app/ai/audio_capture_extension/native_app/audio_capture_native.py "$@"
