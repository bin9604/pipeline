# 科研脚本重构记录

## 重构前审阅与映射

本次先审阅 reader、preprocessing、sorting、postprocessing、curation、visualization、ISI、pipeline、配置、测试及 Stage 1–4 验证记录，再实施重构。旧版本已由用户 push 到 Git；不在当前项目另建 legacy 副本。

|旧文件 / 职责|处理|新位置|
|---|---|---|
|src/io.py：Maxwell reader、元数据、拼接校验|MERGE；拼接 REMOVE FROM ACTIVE FLOW|01_preprocessing.py|
|src/recording_inspection.py、src/preprocessing.py、src/quality_control.py：信号校验与滤波|MERGE|01_preprocessing.py；纯数值校验在 utils.py|
|src/sorting.py、scripts/check_kilosort2_environment.py：参数与环境检查|MERGE|02_sorting.py|
|src/postprocessing.py、src/curation.py、src/visualization.py|MERGE|03_qc_curation.py|
|src/isi_analysis.py|MERGE；保留已验证的 ISI 拟合实现|04_analysis.py|
|src/utils.py：schema、manifest、stage bookkeeping|MERGE；去掉复杂 schema 和 stage framework|utils.py，仅配置、JSON、运行记录、基础校验|
|src/pipeline.py、scripts/run_*.py、多层 CLI、batch / concatenation / mapping / seam|REMOVE FROM ACTIVE FLOW|Git 历史|
|config/*.yaml|MERGE|config.yaml，稳定设置与实验参数明确分区|
|旧 notebooks|MERGE|三个 review/analysis notebooks；不会启动 sorter|
|scripts/slurm_pipeline.sh|MERGE|slurm/run_sorting.sh，仅调用 02_sorting.py|
|tests/test_pipeline.py|MERGE|tests/test_scientific.py，保留科学边界与数据安全测试|
|requirements-pipeline.txt、METHODS.md、STAGE*_*.md、PIPELINE_BUILD_RESULTS.md|KEEP|依赖与科学/历史证据；历史文件中的旧路径属于历史版本|
|docs/ARCHITECTURE.md|REMOVE FROM ACTIVE FLOW|本记录与 README；旧内容在 Git 历史|

必须保留的科学检查：唯一且顺序一致的映射通道；采样率、时长、几何、gain/offset；raw→µV 数值等式；有限值与 Nyquist；float lazy 滤波、reload 与边界/上下文检查；binary 数值域与物理缩放及量化；阈值等号、非有限指标、全部拒绝原因；零单元；raw/curated 独立；短记录、半开窗口、不跨边界计算 ISI；禁止测试意外启动 sorter。

## 当前验证状态

历史证据：STAGE3_RESULTS.md 记录真实 Maxwell reader 与 lazy preprocessing；PIPELINE_BUILD_RESULTS.md 记录旧 synthetic smoke；STAGE4_RESULTS.md 记录环境未就绪。它们不能自动作为新脚本验收证据。

2026-10-05 本次实际验证（SI 0.105.0 / Python 3.11.16）：

|层级|结果 / 证据|
|---|---|
|code reviewed|四个脚本与本机 SI reader/filter/analyzer/metric/KS2 wrapper API 核对；不再引用 src 或旧脚本|
|synthetic tested|`python -m unittest discover -s tests -v`：**12 tests passed**；日志 results/refactor_tests_final.log；实际 SortingAnalyzer、metrics、accepted/rejected 图、独立 curated analysis、三个 notebooks 的 code cells 均执行|
|binary writer tested（synthetic）|真实 SI writer 短 probe 与 int16 ADC counts 一致；全扫描、物理量化检查、正式 binary 抽样校验路径通过 mock sorter fixture；没有调用真实 KS2|
|real-data preprocessing tested|01 对真实 Development_2953_5month.raw.h5 成功；results/preprocessing_001；20 kHz、1020 channels、3,596,600 frames、179.83 s；5随机窗口+首尾=7个0.5s bounded windows；保留全部通道|
|real-data scaling / reload|7个窗口 numerical→uV 与过滤后的 gain/offset 校验通过，lazy reload逐值一致；最大context discrepancy=0.0121215582 uV，低于0.05 uV工程容差；视觉检查首/末窗口原始与filtered图|
|environment preflight|results/sorting_001/run.json：completed（检测完成）、readiness=not_ready、sorting_started=false、binary_validated=false；KS2、MATLAB/runtime、可工作的NVIDIA驱动/GPU未发现|
|GPU runtime tested|**未完成**|
|real Kilosort2 tested|**未完成**；本次未运行MATLAB、CUDA MEX或真实GPU sorting，也未生成真实全量binary|

测试环境出现 SI binary cache writer 的 ResourceWarning（未关闭文件提示），没有被隐藏；测试在丢弃对象/收集循环引用后成功清理Windows临时文件。诊断指标在短synthetic recording中出现NaN符合预期，保留诊断而不额外拒绝unit。

## 删除的抽象层与保留功能

删除 src 模块层、run_* CLI 转发层、pipeline orchestrator、参数resolver/schema、stage bookkeeping、batch/concatenation/timezone/source-mapping/seam框架。原因是当前任务只需一份recording，核心科学操作必须在对应脚本从上往下可见。保留一个小utils文件用于JSON/配置、版本/时间/失败记录和纯数据校验；它不创建或排序SpikeInterface对象。

阈值边界、NaN/Inf、多重拒绝、零单元、raw不可覆盖、gain/offset/geometry/trace shape、Nyquist、binary物理误差、short skip/use_available、窗口终点/ISI上下限与禁止意外启动sorter均保留测试。修复旧继承实现中浮点秒数相加可能误纳入窗口终点的问题，改用decimal秒数推导整数frame边界；8–100 ms上下限也采用整数frame包含比较。

## 科学参数来源和首次 GPU 验收

论文数值沿用历史METHODS.md：Kilosort2、300–6000 Hz、ISI ratio≤0.3 / FR≥0.05 Hz / SNR≥5、180 s内至少30spikes、8–100 ms、median/15、R²>0.9。本次没有重新取得论文；SI ratio/SNR定义是否对应论文仍待核验。

filter类型/阶数/双向/边界、KS2内部detection/CAR/highpass/whitening/minFR、waveform/noise/sparsity、seed、normalization/median/short-recording policy、1 uV量化容差和资源样例均为软件默认或人为约定，不能称论文确认。参数表、四阶段输入输出与新目录见README；全部可调参数在config.yaml。

第一次取得GPU节点：修改服务器config与Slurm所有USER MUST EDIT → 在服务器运行01 → compute node验证NVIDIA/MATLAB许可证/toolbox/gpuDevice/选定KS2 revision CUDA MEX → 02只做preflight并查看参数/commit → 设置runtime_confirmed=true → login node提交sbatch slurm/run_sorting.sh → 查看squeue/sacct/ks2-JOB_ID.log与run.json → 成功后运行03、人工review、04。任何runtime发现检查或mock通过都不能标为真实KS2通过。
