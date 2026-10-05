# Human organoid HD-MEA SpikeInterface + Kilosort2 pipeline

完整模块和 CLI 已建立，目标环境为 SpikeInterface 0.105.0。**目前没有运行真实 spike sorting**。本机 Stage 4 尚未就绪；不能把下面的构建验证当成 Kilosort2/GPU 验收。

## 已验证

- `results/Development_2953_5month/pipeline_dry_run_002`：最终代码的真实 Maxwell reader、lazy bandpass、短窗信号 QC 和参数导出；sorting_started=false。
- `results/synthetic_pipeline_smoke_002`：已知合成 spike trains → SortingAnalyzer → quality metrics → curation → QC PNG → downstream ISI；synthetic=true、sorting_started=false。此 smoke 使用5秒/至少5 spikes，仅测试软件链路，绝不用于论文分析。
- `tests/test_pipeline.py`：真实 SI API 接通、阈值等号、多拒绝原因、NaN/Inf拒绝、零单位输出、短 recording 策略、concatenation验证和边界归属。测试 mock 禁止调用 sorter。
- `scripts/validate_preprocessing.py`：频率响应/Nyquist 和真实文件首尾读取验证。

## 布局

```text
config/sorting_config.yaml        主配置（明确论文值与未确认约定）
src/io.py                        reader、metadata、concatenation验证
src/preprocessing.py             lazy 300–6000 Hz带通
src/sorting.py                   Kilosort2环境门槛、参数审计、int16范围检查
src/postprocessing.py            sparse/disk-backed analyzer与quality metrics
src/quality_control.py           抽样信号QC
src/curation.py                  三个boolean masks、接受/拒绝理由
src/visualization.py             采样unit的八面板review图
src/isi_analysis.py              curated单位的独立ISI拟合
src/pipeline.py                  single/batch/concatenation与source映射
src/utils.py                     logging、配置、manifest、代码hash
scripts/                         CLI、环境检查、synthetic smoke、Slurm模板
notebooks/01_signal_qc.ipynb      读取信号QC结果
notebooks/02_sorting_qc.ipynb     读取单元QC结果（初始指向synthetic demo）
notebooks/03_isi_analysis.ipynb   读取ISI结果（初始指向synthetic demo）
tests/                           无sorter测试
```

原 exploratory notebooks 和 kilosort.py 保留。raw HDF5 无需搬入 data/，路径由 CLI 传入。工作目录使用项目根目录。

## 现在可以安全运行的命令

```powershell
& 'C:\Users\Bin\miniconda3\envs\spike_sort\python.exe' scripts/run_pipeline.py --input Development_2953_5month.raw.h5 --output results/Development_2953_5month/pipeline_dry_run_new --config config/sorting_config.yaml --dry-run
& 'C:\Users\Bin\miniconda3\envs\spike_sort\python.exe' -m unittest discover -s tests -v
& 'C:\Users\Bin\miniconda3\envs\spike_sort\python.exe' scripts/smoke_postprocessing.py --output results/synthetic_pipeline_smoke_new
```

dry-run 读取少量真实信号、保存 lazy定义和参数，不调用 Kilosort2、不生成全录制binary、不验证GPU。每次输出目录必须新建；失败目录也保留，不覆盖。pipeline notebook 仅阅读结果，不自动启动 sorter。

## 将来运行真实 sorting 前

先阅读 [METHODS.md](METHODS.md) 和 [STAGE4_RESULTS.md](STAGE4_RESULTS.md)。配置需要显式接受当前 SI quality metric 约定：`metrics.definitions_acknowledged: true`；这不表示其参数已从论文中确认。ISI 还需要 `isi_analysis.conventions_acknowledged: true`，明确 normalization、median_scope、窗口位置和 short_recording_policy。默认179.83秒记录会被严格180秒策略 skip，不补数据。

在具备 NVIDIA GPU、MATLAB/toolbox/已编译 KS2 MEX 的计算节点设置 KILOSORT2_PATH（或 config.sorter.path），记录真实KS2 commit并完成runtime验证。requirements-pipeline.txt 是当前环境验证过的核心依赖清单，没有安装依赖或改写原 requirements.txt。

以下是未来命令，本次没有执行：

```bash
python scripts/check_kilosort2_environment.py --output results/ks2_preflight_new --kilosort2-path /validated/Kilosort2
python scripts/run_pipeline.py --input /data/Development_2953_5month.raw.h5 --output /scratch/results/run_new --config config/sorting_config.yaml
```

严格限定阶段时：

```bash
python scripts/run_sorting.py --input /data/recording.raw.h5 --output /scratch/results/sorting_new --config config/sorting_config.yaml
python scripts/run_quality_control.py --source /scratch/results/sorting_new --output /scratch/results/qc_new --config config/sorting_config.yaml
python scripts/run_isi_analysis.py --source /scratch/results/qc_new --output /scratch/results/isi_new --config config/sorting_config.yaml
```

quality-control 会在新目录保存独立 raw/curated版本；ISI只读取已有curated sorting。完整入口默认执行QC和curation，但未确认ISI约定时会明确延期并记录warning。

## 批处理和concatenation

多个 `--input` 默认分别处理；单个失败记录其错误并继续其他文件，batch_manifest标记失败，最终退出非零。此逻辑已实现，本次未运行真实批处理。

```bash
python scripts/run_pipeline.py --input /data/a.raw.h5 /data/b.raw.h5 --output /scratch/batch_new --config config/sorting_config.yaml
```

只有 `--concatenate` 才联合 sorting，且 config.concatenation 需有逐文件 chip/config/带时区录制时间和可接受start时间跨度。实际映射/geometry/scaling必须相同；先各自滤波，再拼接。

```yaml
concatenation:
  confirmed: true
  max_start_gap_sec: 600  # 用户明确的短时间标准，非论文参数
  recordings:
    - input_file: /data/a.raw.h5
      chip_id: actual_chip_id
      configuration_id: actual_configuration_id
      recorded_at: '2026-10-05T10:00:00+08:00'
    - input_file: /data/b.raw.h5
      chip_id: actual_chip_id
      configuration_id: actual_configuration_id
      recorded_at: '2026-10-05T10:05:00+08:00'
```

```bash
python scripts/run_pipeline.py --input /data/a.raw.h5 /data/b.raw.h5 --output /scratch/joint_new --config config/sorting_config.yaml --concatenate
```

上述身份/时间只是格式示例，不能当作当前数据的metadata。metrics/spike_source_mapping_raw.csv 和 curated.csv 保存每个spike的文件与局部时间；ISI分别在原文件窗口执行。concatenation seam 附近需要检查，不会默默删除 spikes。

## 输出与资源

完整真实run输出：metadata（recording/config/lazy定义/time mapping/实际参数/源码），kilosort2（SI参数、MATLAB日志、chanMap、ops、recording.dat和temp_wh.dat），sorting_raw、sorting_curated、analyzer（disk-backed sparse extensions），metrics（quality/accepted/rejected/ISI/source mapping），figures和logs，以及run_manifest.json。如果没有units或延期ISI，相应输出会明确缺省/为空。

每run记录输入size/mtime、代码SHA256、实际版本、配置、阶段状态、warnings、完整异常日志和执行时刻。不读取整份HDF5做hash，未声称输入有内容级checksum；复制/归档时可另做校验。

当前int16 recording.dat理论6.83 GiB，连同temp_wh.dat约13.7 GiB，再加原始HDF5、analyzer、结果、MATLAB工作区。写binary之前额外逐秒扫描所有filtered samples检查int16范围，增加一次流式IO。n_jobs默认1；增加前需估算并发内存。所有 plots 使用Agg，不依赖GUI。

Slurm入口 `scripts/slurm_pipeline.sh` 需用户按集群设置module、partition/account和资源；32G/4CPU/8h是初始工程示例，未经过真实KS2测量。提交时用环境变量PIPELINE_ROOT、INPUT_FILE、OUTPUT_DIR、KILOSORT2_PATH、CONFIG_FILE；不在login node执行sorting。MATLAB命令和库需要在batch环境PATH可用。
