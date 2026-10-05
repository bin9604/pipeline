# Stage 3：lazy 300–6000 Hz 预处理

已在 spike_sort 环境对实际 recording 完成 Stage 3，结果位于 `results/Development_2953_5month/preprocessing_run_001`。未运行 Kilosort2，也未增加 reference、notch、whitening 或其他显式预处理。

## 实现

`src/preprocessing.py` 提供 `preprocess_recording(recording, parameters)`，返回 SpikeInterface bandpass-filtered Recording。保留所有 1020 个映射通道及原始顺序、geometry、samples、segments 和采样率。先以真实 channel IDs 建立 ChannelSliceRecording，防止 Stage 2 发现的 Maxwell 1028 signal rows/1020 mapped channels 问题再次出现在 sorter 的全通道读取中。

滤波由 `spikeinterface.preprocessing.bandpass_filter()` 执行，读取时计算，不加载全时长到 RAM。输出 float32，避免整数滤波结果舍入；转换到 µV 的 gain 保留，滤波后的 offset 为 0。没有额外减均值步骤。

## 参数来源

|参数|使用值|来源|
|---|---|---|
|freq_min / freq_max|300 / 6000 Hz|用户提供的论文 Methods|
|filter_order / ftype|5 / butter|实际安装 SI 0.105.0 的软件默认；不是论文指定|
|filter_mode / direction|sos / forward-backward|软件默认；不是论文指定|
|margin_ms|auto → 16.666666666666668 ms|实际 SI 实现解析结果，已保存|
|add_reflect_padding|false|软件默认|
|dtype|float32|工程设置，防止 uint16 量化/舍入|

实际安装源码是本次参数核验依据；配置已将相关软件默认显式写出。五阶是滤波器设计参数，双向滤波会改变合成频率响应，不能说论文报告了五阶双向 Butterworth。作者的阶数、类型和边界处理仍为“需要根据论文引用文献/补充材料进一步确认”。

Nyquist 为 10 kHz，允许 6 kHz upper cutoff；函数对不合法采样率和 cutoff 报 ValueError，不能静默改变论文频段。

## 实际检查结果

- 使用 Stage 2 同一组 5 个 0.5 s 窗口，全部 1020 通道；5,100 个 raw 与 5,100 个 filtered 统计行。
- 滤波输出 shape 与 raw 一致，抽样中全部为有限值；通道 ID、geometry、sample 数保持一致。
- JSON 中的 lazy Recording 能重新加载，各检查窗口与原对象输出逐值一致。
- 扩大读取上下文再裁剪与直接短窗读取的最大差异为 0.0059142 µV，低于工程验收容差 0.05 µV。该容差不是论文参数，也不等于证明任意 chunk 边界完全一致。
- 已额外读取实际 recording 起始/末尾各 1000 frames，shape=(1000,1020)，无非有限值。边界存在滤波瞬态可能，不能将有限值检查当成生理事件可信性证明。
- 抽样通道 MAD sigma（每通道先取跨窗中位数，再取跨通道中位数）：raw 约 9.3321 µV，filtered 约 4.2985 µV。
- 通道 678 的跨窗 MAD sigma 中位数从约 242.63 降为 25.93 µV；滤波后仍存在明显大幅波形，不能凭形态判断为真实 unit 或坏道。保留该通道，在后续 sorting/QC 继续检查。
- 已视觉检查 raw/filtered 对照图。raw 有 DC 基线，filtered 近零；两列纵轴独立缩放，应按纵轴读数比较幅度。

MAD sigma = median(|x-median(x)|)/0.6744897501960817，只是抽样诊断，尚不是 SI 的最终 noise_levels extension，也不是 unit SNR。工程测试的 100/1000/8000 Hz 正弦信号 RMS 保留比例约 0.0000123/1.0/0.0002418，验证频段外衰减与频段内保留；12 kHz 采样率的 6000 Hz cutoff 如预期报错。

## 运行方法

```powershell
& 'C:\Users\Bin\miniconda3\envs\spike_sort\python.exe' scripts/run_preprocessing.py --input Development_2953_5month.raw.h5 --inspection results/Development_2953_5month/inspection_run_003 --output results/Development_2953_5month/preprocessing_run_002 --config config/preprocessing_config.yaml

& 'C:\Users\Bin\miniconda3\envs\spike_sort\python.exe' scripts/validate_preprocessing.py
```

首次实际运行目录是 preprocessing_run_001；示例重跑使用新目录，既有目录拒绝覆盖。validation 脚本针对本次 _001 结果，同时测试 synthetic signal 和 Nyquist 错误。

## 输出与后续使用

- `run_manifest.json`：配置、依赖版本、输入文件身份、起止时间、状态、运行时长和限制。
- `metadata/filtered_recording.json`：可重新加载的 lazy Recording 定义；不是已写出的完整 filtered signal。
- `metadata/filter_parameters.json`：实际 margin、filter 参数、SOS coefficients、dtype 和 conversion。
- `metadata/chunk_reload_validation.json`：逐窗上下文差异和 reload 一致性。
- `metadata/sampling_windows.csv`：抽样 frame 清单。
- `metrics/preprocessing_qc_windows.csv`、`preprocessing_qc_summary.csv`：raw/filtered 诊断。
- `figures/comparison_s0_w*.png`、`noise_diagnostics.png`：对照图和 MAD sigma 分布。
- `logs/preprocessing.log`：运行日志。

```python
import spikeinterface as si
filtered_recording = si.load(
    'results/Development_2953_5month/preprocessing_run_001/metadata/filtered_recording.json'
)
```

加载定义仍需要原始 HDF5、Maxwell 解压插件以及兼容依赖。JSON 包含当前绝对文件路径；搬到服务器须重新用新 input_file 构建，或显式调整路径并记录 provenance。本阶段没有生成全时长 binary；后续 Kilosort2 如需 binary，交由 SI 在独立 sorter 输出目录生成并保留。

本阶段未对完整 recording 做全时长 QC；179.83 s 不足完整 180 s 的 downstream 策略仍未确定。下一阶段是 Stage 4：检查 Kilosort2 可发现性、MATLAB/CUDA/GPU 和参数，不应直接开始 sorting。
