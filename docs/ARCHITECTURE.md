# Pipeline 调用关系与数据流向

本文梳理 human organoid HD-MEA SpikeInterface + Kilosort2 pipeline 的模块调用关系与数据流向。目标环境为 SpikeInterface 0.105.0，主配置为 `config/sorting_config.yaml`。

## 1. 分层结构

```text
scripts/           CLI 入口（argparse，只做参数解析 + 调 src）
  ├─ run_pipeline.py          主入口：single / batch / concatenate
  ├─ run_sorting.py           只跑到 sorting（stop_after_sorting）
  ├─ run_quality_control.py   从已有 run 重跑下游 QC
  ├─ run_isi_analysis.py      从已有 run 重跑 ISI
  ├─ inspect_recording.py     Stage1 原始数据检查
  ├─ run_preprocessing.py     Stage2 滤波验证
  ├─ validate_preprocessing.py 频率判别 / Nyquist 单测
  ├─ check_kilosort2_environment.py KS2 环境预检
  └─ smoke_postprocessing.py  合成数据 smoke

src/              领域逻辑（无 CLI，可测试）
  ├─ pipeline.py       编排层（_run / finish_sorting / 各 run_* 函数）
  ├─ io.py / preprocessing.py / quality_control.py / sorting.py
  ├─ postprocessing.py / curation.py / visualization.py / isi_analysis.py
  ├─ recording_inspection.py
  └─ utils.py          write_json / load_config / run_context / source_fingerprint
```

`config/sorting_config.yaml` 是唯一主配置；`preprocessing_config.yaml`、`inspection_config.yaml` 只被 Stage1/Stage2 的独立脚本使用。

## 2. 模块职责与依赖（src/ 内部）

| 模块 | 职责 | 主要函数 | 依赖 |
|---|---|---|---|
| `utils.py` | 配置加载/校验、run 目录 + manifest 生命周期、JSON/哈希 | `load_config`, `run_context`, `write_json`, `source_fingerprint` | 无 |
| `io.py` | Maxwell 读取、元数据、拼接前一致性校验 | `read_recording`, `save_recording_metadata`, `validate_concatenation` | utils |
| `preprocessing.py` | lazy 300–6000 Hz 带通 | `preprocess_recording` | 无 |
| `quality_control.py` | 信号级抽样 QC | `signal_qc` | 无 |
| `sorting.py` | KS2 参数审计 + 环境门槛 + 调用 sorter + int16 范围扫描 | `sorter_parameters`, `check_sorter_environment`, `run_kilosort2` | utils |
| `postprocessing.py` | SortingAnalyzer + quality metrics | `analyze_sorting` | utils |
| `curation.py` | 三指标 boolean mask 接受/拒绝 | `curate_metrics`, `save_curation` | 无 |
| `visualization.py` | 采样 unit 八面板图 | `plot_unit_qc` | 无 |
| `isi_analysis.py` | 下游指数拟合 | `fit_isi`, `run_isi_analysis` | 无 |
| `pipeline.py` | **编排层**，串起以上所有 | `_run`, `finish_sorting`, `export_spike_mapping`, `run_single_recording`, `run_concatenated_recordings` | 全部 |
| `recording_inspection.py` | Stage1 独立检查 | `inspect_recording` | 无 |

## 3. 主入口调用链（`run_pipeline.py` → 完整 run）

```text
run_pipeline.py main()
  ├─ load_config(config)
  ├─ --concatenate ──────────► run_concatenated_recordings()
  ├─ 单个 --input ───────────► run_single_recording()
  └─ 多个 --input(非拼接) ────► 循环 run_single_recording() + batch_manifest.json
                                    └── _run(files, config, output, concatenated, dry_run, stop_after_sorting)
```

`_run()` 内部（核心主流程）：

```text
run_context(output, inputs, config, stage)   ← 建目录 / 写 manifest / 记日志 / provenance
  │
  ├─ read_recording()          × N         → BaseRecording(raw, 含 gain/offset 注解)
  ├─ validate_concatenation()  (仅拼接)     → mapping[]
  │                                         (非拼接则自造单段 mapping)
  ├─ write time_mapping.json
  ├─ save_recording_metadata() × N          → metadata/recording_{i}.json
  ├─ preprocess_recording()    × N         → 懒加载 float32 带通 Recording
  ├─ si.concatenate_recordings() (仅拼接)
  ├─ dump_to_json()                         → metadata/filtered_recording.json
  ├─ sorter_parameters()                    → metadata/sorter_defaults.json + requested.json
  ├─ signal_qc()                            → metrics/signal_qc.csv
  │
  ├─ [dry_run] 到此返回（sorting_started=false）
  ├─ [definitions_acknowledged=false] raise，不启动 sorter
  │
  ├─ run_kilosort2()                        → BaseSorting
  │    ├─ check_sorter_environment()
  │    ├─ sorter_parameters()
  │    ├─ 全录制流式 int16 范围扫描
  │    └─ ss.run_sorter('kilosort2')       → kilosort2/ 目录(MATLAB 输出)
  │
  ├─ [stop_after_sorting] 存 sorting_raw 后返回
  │
  └─ finish_sorting()  ← 下游全部阶段
       ├─ sorting.save()                    → sorting_raw/
       ├─ export_spike_mapping()            → metrics/spike_source_mapping_raw.csv
       ├─ analyze_sorting()                 → analyzer/ + metrics/quality_metrics.csv
       ├─ save_curation()                   → metrics/{curation_decisions,accepted,rejected}_units.csv
       │                                     → sorting_curated/
       ├─ export_spike_mapping(curated)     → metrics/spike_source_mapping_curated.csv
       ├─ plot_unit_qc()                    → figures/unit_{group}_{n}.png
       └─ run_isi_analysis() (若已确认约定) → metrics/isi_analysis.csv + figures/isi_{n}.png

run_context 的 finally 写 run_manifest.json（status/completed、warnings、code_sha256…）
```

## 4. 数据流向（对象 → 产物）

```text
原始 Maxwell .h5
   │ read_maxwell (io.read_recording)
   ▼
raw Recording  ──(增益/offset 注解保留)──► 元数据：channel_ids/geometry/gain/segments
   │ preprocess_recording (lazy bandpass, float32)
   ▼
filtered Recording ─────────────────────► filtered_recording.json (lazy 定义, 不落全量 binary)
   │                                      signal_qc.csv (抽样窗)
   │ run_kilosort2 (SI 写 int16 recording.dat)
   ▼
BaseSorting ────────────────────────────► sorting_raw/
   │                                       spike_source_mapping_raw.csv
   │ analyze_sorting (binary_folder analyzer, sparse, uV)
   ▼
SortingAnalyzer + quality_metrics 表 ───► analyzer/ + quality_metrics.csv
   │ save_curation (三个布尔 mask 的 AND)
   ▼
curated Sorting + 决策表 ───────────────► sorting_curated/ + curation_decisions/accepted/rejected.csv
   │                                       spike_source_mapping_curated.csv
   │ plot_unit_qc
   ▼
八面板 PNG ─────────────────────────────► figures/unit_*.png
   │ run_isi_analysis (每原始文件独立拟合)
   ▼
ISI 拟合表 + 图 ────────────────────────► isi_analysis.csv + figures/isi_*.png
```

关键点：**filtered 阶段是懒加载**（`dump_to_json` 只存定义，不写全量 float binary）；真正落盘的 binary 是 Kilosort2 阶段由 SI 以 int16 ADC-count 写的 `recording.dat`/`temp_wh.dat`。

## 5. 独立 CLI 之间的阶段依赖

```text
Stage0 环境预检:  check_kilosort2_environment.py  → environment_report.json (独立)

Stage1 检查:      inspect_recording.py ──► inspection_run_NNN/
                                           ├─ metadata/hdf5_structure, recording_metadata,
                                           │          channel_map, sampling_windows
                                           ├─ metrics/channel_qc_windows/summary
                                           ├─ metadata/scaling_validation.json
                                           └─ figures/raw_*, geometry, qc_distributions
        │
        └─(读 sampling_windows.csv + manifest, 校验输入未变)
Stage2 滤波验证:   run_preprocessing.py ──► preprocessing_run_NNN/
                                           ├─ filtered_recording.json, filter_parameters.json
                                           ├─ preprocessing_qc_windows/summary, chunk_reload_validation.json
                                           └─ figures/comparison_*, noise_diagnostics

      validate_preprocessing.py  ──(独立频率/Nyquist 断言, 无输出目录)

主链路(不依赖 Stage1/2 产物):  run_pipeline.py ──► pipeline_run_NNN/ (见 §3/§4)

下游重跑(依赖已有 run 的产物):
  run_quality_control.py  读 source/{filtered_recording.json, sorting_raw, time_mapping.json} → 新目录重跑 finish_sorting
  run_isi_analysis.py     读 source/{sorting_curated, time_mapping.json}                      → 新目录重跑 run_isi_analysis

  smoke_postprocessing.py 合成 ground-truth → finish_sorting（无 sorter, 无真实 input）
```

## 6. 完整 run 输出目录结构

```text
{output}/
├─ run_manifest.json                 ← 单一事实来源（status/warnings/code_sha256/versions…）
├─ logs/pipeline.log
├─ metadata/
│   ├─ config_resolved.json
│   ├─ time_mapping.json
│   ├─ recording_{i}.json
│   ├─ filtered_recording.json
│   ├─ sorter_defaults.json
│   ├─ sorter_requested_parameters.json
│   ├─ kilosort2_wrapper.txt        (run_kilosort2 写入)
│   ├─ binary_writer.txt            (run_kilosort2 写入)
│   ├─ analyzer_extension_parameters.json
│   ├─ quality_metric_parameters.json
│   ├─ compute_snrs_source.txt
│   ├─ compute_isi_violations_source.txt
│   └─ isi_formula_source.txt
├─ metrics/
│   ├─ signal_qc.csv
│   ├─ spike_source_mapping_raw.csv
│   ├─ quality_metrics.csv
│   ├─ curation_decisions.csv
│   ├─ accepted_units.csv
│   ├─ rejected_units.csv
│   ├─ spike_source_mapping_curated.csv
│   ├─ qc_figure_units.csv
│   └─ isi_analysis.csv            (若 conventions_acknowledged)
├─ figures/
│   ├─ unit_{group}_{n}.png
│   └─ isi_{n}.png                 (若 conventions_acknowledged)
├─ kilosort2/                       ← sorter 输出（MATLAB 工作区/ops/chanMap/recording.dat…）
├─ sorting_raw/
├─ sorting_curated/
└─ analyzer/                        ← binary_folder sparse analyzer（waveforms/templates/noise_levels/…）
```

## 7. 关键编排约束

- **Stage1/Stage2 是"独立验证链"**：主 `run_pipeline.py` 不消费它们的产物——主链路自己读文件、自己滤波、自己 QC。Stage1/2 的作用是提前核验 reader/filter/边界，与正式 run 解耦。
- **`finish_sorting` 是下游"可复用单元"**：既被主链路调用，也被 `run_quality_control.py`、`smoke_postprocessing.py` 独立调用（输入是现成的 sorting + recording + mapping）。
- **两把科研门槛**：`metrics.definitions_acknowledged`（拦在 `run_kilosort2` 前）、`isi_analysis.conventions_acknowledged`（拦在 `run_isi_analysis` 前，为 false 时主链路明确延期并记 warning）。
- **provenance**：`run_context` 对每个输入记 size/mtime、对 `src/` + `scripts/` 记 SHA256、记包版本、完整异常与 warnings，`run_manifest.json` 是每个 run 的单一事实来源；失败目录也保留不覆盖。
