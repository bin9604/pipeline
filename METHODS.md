# 科学参数与实现定义

本项目实现论文提供的阶段顺序；缺少论文引用文献/补充材料和真实 KS2 环境，因此不宣称已经完整复现论文。

|设置|论文已给出|当前实现|
|---|---|---|
|用户显式滤波|300–6000 Hz|SI lazy bandpass，float32；五阶 Butterworth/SOS/双向是明确软件设置|
|算法|Kilosort2|严格指定 kilosort2，无 Kilosort4 fallback|
|自动筛选|ISI ≤0.3、FR ≥0.05 Hz、SNR ≥5|逐 unit 检查三个有限值与阈值；记录所有原因；等号通过|
|downstream 选择|3 min 内至少30 spikes|独立于 sorting QC，curated units 才参与|
|ISI 范围|8–100 ms|包含两端；当前仅一个 recording，不跨窗口边界|
|bin width|median ISI/15|median 作用域需配置声明|
|拟合|a exp(-λx)，R²>0.9|非线性最小二乘，a/λ 非负工程约束；严格大于门槛|

## 本机 SI 0.105.0 实现

核验对象是实际安装的 `spikeinterface.metrics`，不是旧命名 `qualitymetrics` 或 `quality_metrics`。extension 参数使用 `metric_params`，不是旧示例中的 `qm_params`。

令 N 为 unit 的总 spike 数、T 为分析时长（秒）、τ 为 isi_threshold_ms/1000、c 为 min_isi_ms/1000。实际 helper 统计相邻 ISI<τ 的次数 V，计算：

`isi_violations_ratio = [V / (2 N (τ-c))] / (N/T) = V T / [2 N² (τ-c)]`。

实现中的 `min_isi_ms` 修改分母，没有在这个 helper 中把 ISI<c 的计数删掉。ratio 不是 V/N、不是 bounded percentage，可能大于1；N=0 返回 NaN。当前 explicit software defaults 是 τ=1.5 ms、c=0；**尚未从论文中确定**。与论文的 0.3 是否使用相同定义，**需要根据论文引用文献/补充材料进一步确认**。

`firing_rate=N/T`（Hz）。QC 的 T 是这一份 recording 的完整有效时长。04 的 firing_rate 使用配置分析窗口的实际有效时长，不能与全 recording QC 的 FR 混淆。

当前 `compute_snrs` 在 analyzer 的 main channel 取模板 extremum 的绝对幅度，再除该通道 `noise_levels`：`SNR=abs(A_template)/σ_noise`。analyzer 使用 both peak sign；noise_levels 使用随机窗口逐窗 MAD/0.6744897501960817，然后跨窗口平均。random windows 和 waveform sample 有固定 seed。

需要特别区分文档与本机源码：早期查阅的官方当前文档称 0.104 后改用 median；本机 0.105.0 的 `compute_snrs → get_template_amplitude_on_main_channel → get_template_amplitudes` 调用链没有传 operator，后者默认 operator='average'。本项目计算 average 模板，`snr` 按**本机实际 average 调用链**执行，没有冒称 median。运行时保存 metric/helper 源码；如果更换软件版本，必须重新核验。这修正了 Stage 1 对文档的初步描述。

`metrics.definitions_acknowledged=false` 是科研解释门槛：只有明确接受当前 SI 指标约定后才能运行 QC，不是要求宣称已经找到论文参数。确认或调整设置后显式置 true，并保存配置；待补充资料找到时另建新 run，不能改写旧结果。

## Waveform、诊断与人工 review

ms_before=1/ms_after=2、300 sampled spikes/unit、100 µm sparse radius 都是 QC 工程设置，尚未从论文中确定；不改变已完成的 sorting，能影响模板、SNR 和接受决定。binary_folder analyzer 的 waveform 数量有上限，按单元读取绘图，不一次把全部波形加载 RAM。

presence_ratio、amplitude_median 和 amplitude_cv 只作诊断，短 recording 或小 spike 数产生 NaN 时保留，不因此拒绝 unit。可配置增加 rp/sliding_rp；未默认计算额外定位或完整 correlogram extension，spatial footprint 来自模板/geometry，ACG 只为采样 review units 计算并有显式 pair cap。PTP 图和随时间幅度图用于观察，不能证明所有重复单位/漂移已解决。人工查看不改变 automatic acceptance；需要人工修改时另建版本。

## Downstream ISI 未报告约定

窗口默认从0开始，归一化默认 probability，median 默认取8–100 ms retained ISI；也支持 median_scope=all、normalization=density。这些均是待确认的工程约定。最后一个 histogram bin 仍使用相同 width，可能延伸超过100 ms；只计入已截取到8–100 ms的数据，不静默改变 width。包括零计数 bins 参与拟合；λ 的单位是 ms⁻¹。R²=1−SSE/SST，常数 histogram 不拟合；fit failure/缺少 spikes/保留 ISI不足均输出 status。

默认 short_recording_policy=skip：179.83 s 文件输出 short_recording_skipped；use_available 必须显式选择，报告实际 interval_used_sec、complete_interval=false，不能称完整3 min。默认 conventions_acknowledged=false 会使 04_analysis.py 报错并记录失败，要求先明确约定。不会补零、补 spikes 或把30 spikes门槛用于 sorting。

## 单 recording 与 sorter 内部行为

当前流程只处理一个 segment、一份 raw.h5；不实现 batch、拼接、timezone、source mapping 或 seam framework。分析窗口使用整数 frames 判定半开边界，ISI 包含两端也按 frame 间隔比较。

Kilosort2 内部默认 CAR、高通150Hz、whitening、minFR=0.1 是算法软件设置；与用户显式300–6000Hz和事后0.05Hz门槛分别保存。skip_kilosort_preprocessing 不允许在该 baseline 随意开启。保留全部 sorter units，不沿用其 good-only 筛选。科学参数覆盖、默认参数、SI产生的effective params均保留；正式运行不自动清理临时文件。

SI 对当前浮点 filtered Recording 按数值写 int16 recording.dat；保持 ADC-count scale 的 gain annotation，绝不假定写入自动为 µV。正式调用前逐秒检查所有 filtered samples 的有限性和 int16 范围，额外一次全录制流式读取，失败就报错、不缩放或剪切。同时核验 numerical→µV 的 gain/zero offset、int16 physical quantization 误差、全通道量化归零、实际 SI writer 短 probe 和正式 binary 首/中/尾抽样一致性。max_quantization_error_uV=1.0 是工程容差，非论文参数。量化影响必须与外部 float waveform QC 区分。

KS2 revision、MATLAB/toolbox/CUDA-MEX 仍未核验；wrapper 不暴露 seed，不能承诺 GPU sorting 位级重现。本机没有通过 Stage 4，构建完成不代表真实 sorting 验收完成。
