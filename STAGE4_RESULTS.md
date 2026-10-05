# Stage 4：Kilosort2 环境验证

2026-10-05 已完成本机 preflight，结论为 **not_ready：当前不能进入 Stage 5**。这表示环境验收不通过，不是 sorting 报错。本阶段没有调用 run_sorter、写入 recording.dat、安装软件或更换算法。

## 实测环境

|检查|结果|
|---|---|
|Python / SpikeInterface|spike_sort，3.11.16 / 0.105.0|
|available_sorters|包含 kilosort2，表示 SI 有对应 wrapper|
|installed_sorters|仅 simple；Kilosort2 is_installed=False|
|KILOSORT2_PATH|未设置，无法验证源码版本/commit|
|MATLAB / ks2_compiled|PATH 中均未找到；标准 C:/Program Files/MATLAB 未发现安装目录|
|nvidia-smi / nvcc|PATH 中未找到；System32 下未找到 nvidia-smi.exe|
|显卡|经授权 CIM 查询：Intel Arc B390、两个虚拟显示设备；未列出 NVIDIA|
|MATLAB license / Parallel Computing Toolbox / gpuDevice / CUDA MEX|无法执行验证，不应标成通过|

软件不在 PATH 不证明所有自定义目录都没有安装。脚本的未提升权限 CIM 查询被拒绝；经授权的独立查询成功，结果保存为 `gpu_inventory_verified.json`。AdapterRAM 字段未当作可信现代 GPU 显存容量。

实际 SI KilosortBase 声明 `gpu_capability='nvidia-required'`，Kilosort2 wrapper 写入 `ops.GPU=1` 并注明没有 CPU 版本；Intel GPU 不能满足本 wrapper 的 GPU 要求。不能通过安装 Python kilosort 包或替换 Kilosort4 来满足当前 Kilosort2 复现目标。

## 新增与运行

新增 `scripts/check_kilosort2_environment.py`，只读探测，拒绝覆盖输出目录。完整默认参数和说明已导出，不修改任何科学参数。

```powershell
& 'C:\Users\Bin\miniconda3\envs\spike_sort\python.exe' scripts/check_kilosort2_environment.py --output results/Development_2953_5month/environment_run_002
```

源码安装后可添加 `--kilosort2-path <真实的KS2源码目录>`，仅影响当前 Python 进程及其子进程；不修改永久环境变量。本次实测结果位于 environment_run_001。not_ready 返回非零退出状态，不能把检查脚本运行结束误解为环境就绪。

输出包含 environment_report.json、preflight.log、sorter_defaults.json、sorter_parameter_descriptions.json、本机安装 wrapper/base 源码副本及 kilosort2_master.m.txt。源码 is_installed 检查只查找 master/main MATLAB 文件或 compiled launcher，不能证明完整 MEX、工具箱、许可证与 GPU 链路可运行。

## 关键默认参数与科学边界

|参数|SI 0.105.0 默认|含义|
|---|---|---|
|detect_threshold|6|内部检测阈值|
|projection_threshold|[10,4]|模板投影阈值|
|car|true|Kilosort2 内部 reference；Stage 3 没有额外 reference|
|freq_min|150 Hz|内部 highpass；不能误写成外部 Methods 的 300–6000 Hz|
|whiteningRange / nPCs|32 / 3|内部 whitening/PCA 参数，不是额外探索聚类|
|minFR / minfr_goodchannels|0.1 / 0.1 Hz|内部 cluster/channel 行为，不等于后处理 FR ≥0.05 Hz|
|keep_good_only|false|保留 sorter 的全部输出单元以便独立 curation|
|skip_kilosort_preprocessing|false|保留内部预处理，不因为外部带通就直接跳过|
|NT / ntbuff|None / 64|wrapper 将 NT 默认解析为 65600|
|scaleproc|None|wrapper 将其解析为 200|
|delete_tmp_files|['matlab_files']|默认删除生成的 MATLAB 文件，正式运行拟覆盖为 false 以保留审计材料|
|delete_recording_dat|false|默认保留输入 binary|

全参数见 sorter_defaults.json。本阶段无参数覆盖。Stage 5 如保留全部临时文件，必须显式记录 `delete_tmp_files=False` 是 provenance 工程覆盖。其余设置仍是软件默认，不宣称来自论文。

外部 filtered Recording + 内部高通/CAR/whitening 是当前 wrapper 默认路径；不能为避免重复滤波而擅自设 skip_kilosort_preprocessing=True，源码中这还会跳过 whitening 并要求显式 scaleproc。论文没有提供内部参数，仍 **需要根据论文引用文献/补充材料进一步确认**。内部 minFR 可能使某些最终满足 0.05 Hz 的 unit 在更早阶段被淘汰，必须披露，不能声称两个门槛等价。

wrapper 没有公开 seed 参数；已检查的 master 脚本也没有显式 rng 设置。KS2 算法源码尚未安装，因此随机过程与 GPU 重现性仍未验证，不能随意向 run_sorter 传 seed 或声称已固定 MATLAB 随机性。

## Binary 与资源

本机 SI 源码显示，该 lazy float Recording 会分块写成 `sorter_output/recording.dat`（int16），然后生成 chanMap.mat、ops.mat，内部预处理使用 temp_wh.dat。binary 写入是数值 dtype 转换，不应假定自动写入 µV；下一阶段须核验写入路径的缩放/量化与幅度范围。

当前 int16 binary 理论大小为 3,596,600×1020×2 = **7,337,064,000 bytes，约 6.83 GiB**。recording.dat 加一个类似体量的 temp_wh.dat 已约 13.7 GiB，此外还有原始文件、结果、MATLAB/GPU 工作区与 padding，不能只预留原始 HDF5 大小。显存/RAM 需求应在实际目标硬件验证，不以现代 Kilosort4 要求套用 KS2。

## 进入 Stage 5 前的具体条件

1. 使用具备 NVIDIA GPU 的计算节点，并确认 nvidia-smi 成功；在计算节点而非 HPC login node 执行。
2. 提供明确 Kilosort2 checkout 的目录与 git commit，确认算法版本是 KS2，编译/验证对应 CUDA MEX。
3. 确认 MATLAB 可由 SI wrapper 的 `matlab` 命令调用；验证许可证、Parallel Computing Toolbox、gpuDevice 和具体 KS2 所需函数/MEX。脚本如显示 requires_matlab_runtime_validation，也仍不代表全面就绪。
4. 搬迁后用服务器 input_file 重新构建 Stage 3 Recording，确认 Maxwell Linux 解压插件；不直接依赖 Windows 绝对路径 JSON。
5. 保存目标节点的软件版本、GPU/驱动、KS2 commit、参数及临时磁盘情况，通过验证后再开始单 recording sorting。

官方旧 URL https://github.com/MouseLand/Kilosort2 当前重定向到 Kilosort 主仓库（首页是 Kilosort4），不能按 wrapper 旧安装提示直接克隆默认分支并认作 KS2。主仓库还披露 KS2/2.5/3 的 batch-boundary spike detection 缺陷与 patch1 修复；在选定 KS2 revision 和 NT/ntbuff 前需明确采用历史版还是修复版，不静默套用本 wrapper 参数。参考：https://github.com/MouseLand/Kilosort 。本次没有安装或自行选择作者未报告的 revision。
