# Stage 1：humanbrainorga.ipynb 审查与 Stage 2 方案

审查日期：2026-10-05。下文 cell 编号从 0 开始，与 notebook JSON 顺序一致。

## 证据范围

已检查全部 12 个代码 cell、保存的文本输出、异常和 notebook 元数据。未重跑 notebook，未解压读取 HDF5 信号，未运行 Kilosort。当前终端找不到 python、python3 或 py；notebook 记录的 kernel 是 spike_sort、Python 3.11.16，但这不证明当前终端可以访问该环境。requirements.txt 声明 SpikeInterface 0.105.0，实际 kernel 安装版本仍需核验。原始文件存在，大小 1,835,231,688 bytes。

## 已完成的工作

保存的 reader 输出显示：1020 个记录通道、20,000 Hz、1 个 segment、3,596,600 samples、179.83 s、uint16。几何坐标为 (1020, 2)，已经绘制电极分布并构建 channel_id/electrode_id/x/y 映射。已经探索 8 个通道的原始短窗、300–6000 Hz Butterworth 滤波、MAD 噪声和阈值可视化。

这些是 notebook 历史输出，尚不是本次独立验证的原始文件元数据。尚未完成 Kilosort2、SortingAnalyzer、单元级 quality metrics、自动 curation 或 downstream ISI 分析；本 notebook 没有 waveform extraction、PCA、HDBSCAN 代码。

## 按 cell 分类

|Cell|分类|结论与处理|
|---|---|---|
|0|A：可迁移|保留 read_maxwell；参数化 input_file、使用 Path、记录真实依赖版本和 reader 选项。保存输出提示 Maxwell compression.dll 已安装，部署服务器时仍须核验对应平台插件。|
|1|A|保留通道数、采样率、segments、samples、dtype、geometry 检查，改为结构化导出。|
|2|B：探索/可视化|保留 geometry 图，Stage 2 可复用并保存 PNG；不要仅凭坐标图宣称已确认具体设备型号。|
|3|C：需修正|get_dtype 未调用，输出为 bound method；应使用 get_dtype()。属性键存在不代表数值有效。|
|4|C|保存了 NameError，单位与 gain/offset 尚未成功核验。需在明确加载 recording 后执行，检查逐通道数组与有限性，而非只看 unique。|
|5|A|channel map 可直接迁移；保留 reader 顺序和真实 ID 类型，检查映射长度、唯一性、坐标有限性。|
|6|A|保留 metadata；单 segment 当前可用，正式模块应逐 segment 记录 samples/duration。|
|7|B|短窗 trace 图可保留；注释写 0–1 s，实际为 0–0.1 s。硬编码字符串 ID 应改为读取实际 channel IDs 后选择。|
|8|C/B|一次读取 8 通道全时长，没有 return_in_uV=True；后续计算为原始数值单位。探索代码保留，正式预处理改为 SI lazy Recording，不迁移全时长 NumPy 滤波。|
|9|C|已保存 ValueError：time 长 2000，filtered_traces 长 3596600。局部 traces 与前一 cell 的全长 filtered_traces 混用；独立函数内从同一窗口构建时间轴和绘图数据。|
|10|B|短窗显式转换 µV、时间轴长度正确，可保留；独立 100 ms 窗口 sosfiltfilt 有边缘效应，比较正式滤波时需带上下文并裁剪。|
|11|B/C|MAD 可作为探索噪声估计；该 cell 又遗漏 µV 转换。阈值/SNR 代码只能作探索，需修正命名与无效数值处理。|

A 表示方法逻辑可迁移，不表示原 cell 无需封装即可用于生产。原 notebook 应保留，不删除探索代码；本次没有修改它或用户已有文件。

## 关键问题

1. **单位尚未验证。** uint16 本身不表示 µV。cell 8/9/11 获取未缩放数据，却在图或输出中写 µV。依据 SI 的转换关系 `uV = raw * gain_to_uV + offset_to_uV`，必须核验真实 conversion arrays；gain 不必是 1，offset 不必是 0。减均值不能代替单位转换。单通道线性增益下无量纲比值可能不变，但这不能使物理幅度标签成立。
2. **执行顺序不可复现。** execution_count 不按 cell 顺序递增，cell 4 和 9 有异常，变量 traces/filtered_traces/time 被反复覆盖。应在确认 kernel 后 Restart & Run All，并按实际 traceback 修正。
3. **阈值采样点不是 spike。** `x[x > threshold]` 等操作统计所有越阈 samples，不是局部峰、事件或单元 spike。没有事件合并、不应期、跨通道去重或聚类；不能据此得出 firing rate。
4. **探索 SNR 不可用于 unit curation。** 当前使用窗口内最大越阈幅度除 MAD 噪声；极值容易受伪迹影响，且没有越阈时返回 0 并不证明单元 SNR 为 0。还缺少 sigma=0、NaN/Inf 防护。只有 100 ms、8 个固定通道的结果不能证明整个 recording 无 spike。
5. **资源和滤波。** 8 通道全时长为 28,772,800 数值，单个 float64 数组约 230 MB，滤波及拷贝进一步增加内存；全记录原始 uint16 约 6.83 GiB。20 kHz 的 Nyquist 为 10 kHz，历史输出支持 6 kHz cutoff，但 Stage 2 仍需从实际 reader 重新核验。不要把探索性的减均值、四阶双向 Butterworth 当成论文指定设置。
6. **ID 不能推断。** 保存输出曾出现整数风格映射、另一输出则为字符串 IDs；1020 个通道的 ID 最大到 1023，说明不能用 range(1020) 代替实际 channel IDs。需核验真实类型与缺失 ID，不能自动补通道。
7. **179.83 s 不等于完整 180 s。** downstream 要求 3 min；不足 0.17 s 必须显式报告，不能补零或静默宣称完整 3 min。处理策略尚未从论文中确定。

## 方法定义的待确认项

正式筛选应使用单元级指标，不能使用 cell 11 的通道阈值比值。SI 官方文档特别说明 SNR 从 0.104.0 起改变了实现（median、正负峰），因此不可将旧版本公式直接套到 requirements 声明的 0.105.0。后续阶段须读取实际安装源码、明确输出列名、模板与噪声参数，再记录数学定义。

论文片段没有给出 SI 版本、ISI violation 的确切定义及 refractory/minimum ISI 参数，也未明确 SNR 的模板/噪声算法；0.3 不可直接解释为违反次数比例或 30% contamination。以上均为 **需要根据论文引用文献/补充材料进一步确认**。滤波阶数/类型、Kilosort2 版本与内部参数也尚未从论文中确定。

downstream 的 histogram normalization、median 取全部 ISI 还是截取后的 ISI、窗口起点与不足 180 s 的策略也需单独记录；30 spikes/3 min 不是 sorting QC 门槛。

## Stage 2 具体实施方案（尚未执行）

仅新增 recording inspection，暂不搭建完整 sorting 工程。拟新增 `src/recording_inspection.py`、`scripts/inspect_recording.py`、`config/inspection_config.yaml`。函数接口：`inspect_recording(input_file: Path, output_dir: Path, config: dict) -> dict`；入口使用 argparse、logging、pathlib，输出目录存在则拒绝覆盖。

实施顺序：

1. 确认 spike_sort kernel 的 Python 路径，记录 sys.executable、Python/SI/Neo/h5py/NumPy 版本和原始文件 stat。requirements 是声明而非安装证据。
2. 使用 read_maxwell 读取，遇到多 stream/recording 明确列出并要求指定，不能猜测。记录 Maxwell 插件状态与完整 traceback。仅记录实际 HDF5 元数据，不假设 notebook 展示过 HDF5 树。
3. 导出每个 segment 的 samples/duration、fs/dtype、实际 channel IDs/类型、所有 property 与 annotation、channel geometry/electrode 映射。逐通道保存 gain/offset/physical_unit，不以 gain=1/offset=0 为有效性条件。
4. 在同一小窗口分别读取 raw 与 return_in_uV=True，核对 SI scaling 与逐通道线性转换一致性（允许浮点容差）。缺失、非有限 conversion 或不支持缩放时明确报错，不输出假定 µV 的图。
5. 采用固定 seed 的随机时间窗，逐窗读取所有记录通道做轻量 QC，再固定 seed 随机选少量通道绘图。建议工程起始值：seed=42、每 segment 5 个 0.5 s 窗口、8 个绘图通道；全部为可调整工程设置，非论文参数。串行处理，每窗 20 kHz×0.5 s×1020 个值，float32 约 40.8 MB；转换和统计另有开销。记录所有抽样 frame 和 ID，长度不足则明确记录实际覆盖。
6. 每窗每通道计算 NaN/Inf count、min/max、median、std、PTP、MAD、相邻值变化情况；原始 ADC 极端值占比作为诊断。只有结合硬件有效 ADC 范围才能判断饱和，不能简单把 uint16 极值当硬件 clipping 阈值。精确常数窗标 flat；相对异常幅度/噪声阈值须在 config 标明工程启发式，仅标记、不删通道。raw MAD 含低频信号，不称为最终带通背景噪声。
7. 生成 geometry、raw traces、channel QC 分布图和抽样覆盖信息。全通道短窗统计也不能证明全时长无异常，报告明确 limited sampling。Stage 2 不做预处理，不运行 sorter。

拟运行命令（环境路径核验后）：

```powershell
& '<spike_sort 环境的 python.exe>' scripts/inspect_recording.py --input Development_2953_5month.raw.h5 --output results/Development_2953_5month/inspection_run_001 --config config/inspection_config.yaml
```

Linux 环境使用对应 Python，入口相同，绘图使用 Agg，避免 GUI。

预期输出：metadata/recording_metadata.json、metadata/channel_map.csv（含 scaling）、metadata/sampling_windows.csv、metrics/channel_qc_windows.csv、figures/*.png、logs/inspection.log、run_manifest.json（实际配置、版本、起止时间、状态、warnings/errors）。这里不承诺尚未读取过的 HDF5 元数据值。

验收：与历史值 20 kHz/1020 channels/3596600 samples/1 segment 对照；任何差异先调查；检查 ID/electrode/geometry/scaling 的一一对应；确认 raw-to-uV 小窗一致；检查输出图标签与时间轴；确认没有越界请求、全时长 get_traces 或自动删除通道；所有抽样可由 seed 与 frame 清单重现。验收并确认后进入 Stage 3。

## 官方参考

- SI API，read_maxwell 与 get_traces/scaling：https://spikeinterface.readthedocs.io/en/latest/api.html
- 未缩放与 µV 转换：https://spikeinterface.readthedocs.io/en/latest/tutorials/extractors/plot_2_working_with_unscaled_traces.html
- Quality metrics 与 0.104.0 SNR 变更：https://spikeinterface.readthedocs.io/en/stable/modules/metrics/quality_metrics.html

网页为当前官方文档，不替代实际环境 0.105.0 的源码核验；本阶段不声称论文 metric 定义已经解决。
