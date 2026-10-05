"""Read-only Kilosort2 preflight; never invokes sorting or installs software."""
from __future__ import annotations

import argparse
import inspect
import json
import logging
import os
import platform
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import spikeinterface as si
import spikeinterface.sorters as ss


def run(output: Path, kilosort2_path: Path | None = None) -> dict:
    """Save discoverability, unmodified defaults, source evidence and probes."""
    output.mkdir(parents=True, exist_ok=False)
    logging.basicConfig(level=logging.INFO, handlers=[logging.StreamHandler(),
                        logging.FileHandler(output / 'preflight.log', encoding='utf-8')])
    started = time.perf_counter()
    started_utc = datetime.now(timezone.utc).isoformat()
    cls = ss.sorter_dict['kilosort2']
    if kilosort2_path is not None:
        if not kilosort2_path.is_dir():
            raise FileNotFoundError(kilosort2_path)
        cls.set_kilosort2_path(kilosort2_path)
    defaults = ss.get_default_sorter_params('kilosort2')
    def save(name: str, value: object) -> None:
        (output / name).write_text(json.dumps(value, indent=2, ensure_ascii=False, default=str), encoding='utf-8')
    save('sorter_defaults.json', defaults)
    save('sorter_parameter_descriptions.json', ss.get_sorter_params_description('kilosort2'))
    from spikeinterface.sorters.external.kilosortbase import KilosortBase
    for name, obj in (('kilosort2_wrapper.py.txt', cls), ('kilosort_base.py.txt', KilosortBase)):
        (output / name).write_text(inspect.getsource(obj), encoding='utf-8')
    source = Path(inspect.getfile(cls))
    master = source.parent / 'kilosort2_master.m'
    if master.is_file():
        shutil.copyfile(master, output / 'kilosort2_master.m.txt')
    probes = {}
    for name in ('matlab', 'nvidia-smi', 'nvcc', 'ks2_compiled'):
        executable = shutil.which(name)
        if name == 'nvidia-smi' and executable is None and os.name == 'nt':
            candidate = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/nvidia-smi.exe'
            executable = str(candidate) if candidate.is_file() else None
        probes[name] = dict(executable=executable)
        if executable and name in ('nvidia-smi', 'nvcc'):
            command = ([executable, '--query-gpu=name,driver_version,memory.total', '--format=csv,noheader']
                       if name == 'nvidia-smi' else [executable, '--version'])
            try:
                result = subprocess.run(command, capture_output=True, text=True, timeout=20)
                probes[name].update(returncode=result.returncode, stdout=result.stdout, stderr=result.stderr)
            except (OSError, subprocess.TimeoutExpired) as error:
                probes[name]['error'] = str(error)
    gpu_inventory = None
    if os.name == 'nt':
        try:
            result = subprocess.run(['powershell', '-NoProfile', '-Command',
                'Get-CimInstance Win32_VideoController -ErrorAction Stop | Select-Object Name,DriverVersion,AdapterRAM | ConvertTo-Json'],
                capture_output=True, text=True, timeout=20)
            gpu_inventory = dict(returncode=result.returncode, stdout=result.stdout, stderr=result.stderr)
        except (OSError, subprocess.TimeoutExpired) as error:
            gpu_inventory = dict(error=str(error))
    source_path = getattr(cls, 'kilosort2_path', None)
    revision = None
    if source_path:
        try:
            result = subprocess.run(['git', '-C', source_path, 'rev-parse', 'HEAD'], capture_output=True, text=True, timeout=10)
            revision = result.stdout.strip() if result.returncode == 0 else None
        except (OSError, subprocess.TimeoutExpired):
            pass
    installed = cls.is_installed()
    blockers = []
    if not installed:
        blockers.append('Kilosort2 source/compiled installation not detected by SpikeInterface')
    if not probes['matlab']['executable'] and not probes['ks2_compiled']['executable']:
        blockers.append('Neither MATLAB on PATH nor ks2_compiled launcher detected')
    if probes['nvidia-smi'].get('returncode') != 0:
        blockers.append('Working NVIDIA driver/GPU not verified')
    report = dict(status='completed', readiness='not_ready' if blockers else 'requires_matlab_runtime_validation',
                  sorting_started=False, start_utc=started_utc,
                  python=sys.version, executable=sys.executable, spikeinterface=si.__version__,
                  os=platform.platform(), available_sorters=ss.available_sorters(),
                  installed_sorters=ss.installed_sorters(), kilosort2_installed=installed,
                  kilosort2_path=source_path, kilosort2_git_commit=revision,
                  wrapper_source=str(source), gpu_capability=cls.gpu_capability,
                  probes=probes, windows_gpu_inventory=gpu_inventory, blockers=blockers,
                  scientific_parameter_overrides={},
                  limitations=['MATLAB license, Parallel Computing Toolbox, gpuDevice and CUDA MEX are not validated.',
                               'PATH absence does not prove software absent at all custom locations.',
                               'No source checkout means Kilosort2 version and random-seed behavior remain unverified.'],
                  end_utc=datetime.now(timezone.utc).isoformat(), runtime_sec=time.perf_counter()-started)
    save('environment_report.json', report)
    logging.info('Kilosort2 readiness: %s; blockers: %s', report['readiness'], blockers)
    return report


def main() -> None:
    """Write one preflight run to a fresh directory."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--kilosort2-path', type=Path)
    args = parser.parse_args()
    report = run(args.output, args.kilosort2_path)
    if report['readiness'] == 'not_ready':
        raise SystemExit(2)


if __name__ == '__main__':
    main()
