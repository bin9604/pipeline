# Stage 2 实施与验收

已使用 `C:\Users\Bin\miniconda3\envs\spike_sort\python.exe` 完成实际文件检查。最终结果目录为 `results/Development_2953_5month/inspection_run_003`。原 notebook 未改动；未滤波、未 sorting、未删除通道。

## 新增代码与配置

- `src/recording_inspection.py`：参数化 reader、HDF5 结构导出、metadata/scaling 核验、串行随机窗口 QC、无 GUI PNG 与 manifest。
- `scripts/inspect_recording.py`：命令行入口。
- `config/inspection_config.yaml`：seed=42、5 个 0.5 s 窗口，每窗全 1020 通道统计，随机 8 通道和最多 8 个标记通道绘图；图展示每窗前 100 ms。均为工程检查设置，不是论文参数。

## 验证结果

|项目|实际结果|
|---|---|
|Python / SI / Neo|3.11.16 / 0.105.0 / 0.14.5|
|采样率 / Nyquist|20,000 / 10,000 Hz，支持后续 6,000 Hz upper cutoff|
|记录|1020 channels，1 segment，3,596,600 samples，179.83 s|
|原始 dtype|uint16|
|physical_unit / gain / offset|uV / 6.29425 µV per count / 0；完整逐通道值见 channel_map.csv|
|单位转换|5/5 窗口通过；最大绝对误差约 0.0002344 µV，符合配置浮点容差|
|输出统计|5,100 个 window/channel 行；抽样中无 NaN/Inf、无平坦通道|
|相对异常|channel ID 678 在全部 5 窗口中标记为 amplitude/raw MAD outlier|

678 的最大抽样 PTP 约 2272.22 µV，跨窗 raw MAD 中位数约 163.65 µV。规则是超过当窗跨通道中位数的 10 倍，属于诊断启发式；不能仅凭此判定坏通道。图中额外包含该通道，应在进入预处理前检查它。未删除任何通道。

原始 HDF5 的 sig shape=(1028,3596600)，mapping 有 1020 行。首次运行省略 channel_ids 时实际返回 1028 列，与 1020 个 scaling 值冲突。已按 traceback 修正为显式请求 recording.get_channel_ids()，并校验返回 shape；不猜测其余行含义。`inspection_run_001` 保留失败 manifest/traceback；`inspection_run_002` 是修复后的检查；`inspection_run_003` 加入标记通道绘图与无 offset 的时间标签，是最终版本。

## 运行方法

```powershell
& 'C:\Users\Bin\miniconda3\envs\spike_sort\python.exe' scripts/inspect_recording.py --input Development_2953_5month.raw.h5 --output results/Development_2953_5month/inspection_run_004 --config config/inspection_config.yaml
```

每次用新的 output 目录；已存在目录会报 FileExistsError，不能覆盖既有结果。Linux 使用相应 Python 路径，CLI 参数相同。需要的 PyYAML、h5py、Neo 已记录实际版本；requirements.txt 未自动改写。

## 检查输出

- `run_manifest.json`：状态 completed、实际版本/配置/时刻/运行时长与 warnings。
- `metadata/recording_metadata.json`：逐 segment、属性、annotations、geometry、真实 ID 类型。
- `metadata/hdf5_structure.json`：不读取 signal datasets 的 HDF5 树与属性。
- `metadata/channel_map.csv`：electrode、位置与逐通道 conversion。
- `metadata/sampling_windows.csv`：frame 边界和实际绘图 IDs，可重现抽样。
- `metadata/scaling_validation.json`：各窗转换误差。
- `metrics/channel_qc_windows.csv` 与 `channel_qc_summary.csv`：逐窗和逐通道统计。
- `figures/raw_s0_w*.png`、`geometry.png`、`qc_distributions.png`：原始缩放信号、几何、分布。
- `logs/inspection.log`：运行日志。

已检查 CSV 行数、非有限值、metadata 与 scaling；抽样波形和 geometry 图已做视觉检查。raw 波形有约 3.2 mV DC 基线，这是 reader 的原始缩放数据，本阶段未减均值；不等于 spike 幅度。raw MAD 含低频信号，不能作为最终带通噪声或 unit SNR。

仅抽样 2.5 s，不证明整个 recording 无异常；dtype 极端值比例也不能替代硬件 ADC 饱和判据。记录不足 180 s 的 downstream 策略仍待确认。下一阶段应建立 SI lazy 300–6000 Hz 带通，并对照原始/滤波短窗检查，尚未执行 Stage 3。
