# Human organoid HD-MEA：四步科研分析

处理一份 Maxwell `.raw.h5`：lazy 预处理、Kilosort2、人工可检查的候选 QC/curation、curated spike-train analysis。主要需要 NVIDIA GPU 的步骤是 **02_sorting.py 的 Kilosort2**。

```text
Maxwell .raw.h5
    ↓ 01_preprocessing.py：read_maxwell → Recording → scaling → bandpass
filtered Recording（lazy，未写完整 binary）
    ↓ 02_sorting.py：参数 → runtime/binary 检查 → run_sorter('kilosort2')
raw Sorting（sorting_raw）
    ↓ 03_qc_curation.py：SortingAnalyzer → waveforms/templates → metrics
    ↓ 阈值 + 所有 rejection reasons + 人工 QC
curated Sorting（sorting_curated；candidate units）
    ↓ 04_analysis.py：只读 curated Sorting
spike count / firing rate / ISI / refractory statistics / exploratory ISI fit
```

平时只运行这四个脚本。当前目录移除 pipeline manager、batch、concatenation、多层 CLI，不保留 legacy 副本；旧工程化版本已由用户 push 到 Git。

## 目录与输入输出

```text
01_preprocessing.py
02_sorting.py
03_qc_curation.py
04_analysis.py
utils.py                    # 配置、JSON、运行记录、纯数据检查
config.yaml                 # 稳定设置 + 明确实验参数区
notebooks/
  01_signal_review.ipynb
  02_sorting_review.ipynb
  03_analysis.ipynb
slurm/run_sorting.sh
tests/test_scientific.py
results/                    # 被 .gitignore 排除
requirements-pipeline.txt
METHODS.md                  # 科学定义；历史 Stage 报告也保留
REFACTOR_RESULTS.md          # 旧→新映射与本次验收
```

|Stage|Input|Main object|Output（config paths 指定目录）|
|---|---|---|---|
|01|一份 .raw.h5|Recording → filtered Recording|metadata/raw_recording.json、filtered_recording.json、recording.json、channel map、signal QC CSV/图|
|02|01 lazy Recording 与校验记录|filtered Recording → raw Sorting|环境/参数/来源、binary scaling、kilosort2/、sorting_raw/；默认仅 preflight|
|03|02 sorting_raw + 同一01 Recording|SortingAnalyzer → sorting_curated|analyzer/、quality_metrics.csv、curation_decisions.csv、accepted/rejected_units.csv、QC图、sorting_curated/|
|04|03 sorting_curated + analysis_source.json|curated spike trains|analysis.csv、逐unit ISI NPZ、ISI图；不加载 Recording，不访问 HDF5|

每步保存 `run.json`：实际配置、输入/输出路径、Python/SI/依赖版本、KS2 commit（可获取时）、代码 SHA256、时间、warnings、成功/失败状态。KS2 默认/传入/wrapper解析参数、MATLAB template、binary writer 源码单独保存。

## 参数在哪里改

|参数|config.yaml 区域|性质与重跑范围|
|---|---|---|
|输入、输出、KS2 checkout|paths|相对路径以 config 所在目录为基准|
|带通频率、类型、阶数、dtype|preprocessing|300–6000 Hz 为历史论文值；其余软件默认/工程设置；重跑01及sorting|
|KS2 detect/projection threshold|sorter.overrides|6、[10,4]是显式软件默认，非论文确认；可添加其他合法 SI KS2 overrides|
|binary量化容差|sorter.max_quantization_error_uV|1 µV是工程界限，非论文值；不自动缩放/裁剪|
|SNR、ISI ratio、FR|curation；03中同名大写变量|SNR≥5、ISI ratio≤0.3、FR≥0.05 Hz；只重跑03|
|waveform、噪声、稀疏半径|analyzer|影响模板/SNR/接受决定；只重跑03|
|ISI violation/SNR定义|metrics|1.5 ms、min_isi=0、extremum 等为当前SI约定，非论文确认定义|
|图数量、ACG pair cap|visualization|诊断设置，不改sorting|
|窗口、normalization、median、短记录策略|isi_analysis|只重跑04；必须明确方法学约定|

依据历史 [METHODS.md](METHODS.md) 保留论文数值：Kilosort2、300–6000 Hz、三个 curation 门槛、180 s 内至少30 spikes、8–100 ms、median ISI/15、指数拟合、R²>0.9。本次未重新取得论文核验；**门槛的历史论文来源不意味着 SI metric 公式已与论文一致。** 两个 acknowledgements 默认 false，阅读并接受当前约定后显式置 true；这不要求宣称缺失的论文定义已找到。

## 日常运行

使用 SpikeInterface **0.105.0**，依赖见 requirements-pipeline.txt。现有 Windows Python：`C:\Users\Bin\miniconda3\envs\spike_sort\python.exe`。服务器须验证 Maxwell 解压插件。

先编辑 config 输入/输出路径：

```bash
python 01_preprocessing.py --config config.yaml
python 02_sorting.py --config config.yaml --output results/preflight_001
```

第二条只做 preflight。**不传 --run 就不启动 sorter、不写完整 binary。** 缺少KS2/MATLAB/NVIDIA返回退出码2；run.json区分 preflight completed、readiness=not_ready、sorting_started=false。检测完成不代表环境就绪。

在 NVIDIA compute node 完成 runtime 验证，选定明确 KS2 revision 并设 `sorter.runtime_confirmed=true` 后：

```bash
python 02_sorting.py --config config.yaml --run
# 阅读 METHODS.md，设置 metrics.definitions_acknowledged=true
python 03_qc_curation.py --config config.yaml
# 人工检查 QC；设置 isi_analysis.conventions_acknowledged=true
python 04_analysis.py --config config.yaml
```

所有输出必须是新目录，拒绝覆盖；失败输出也保留。修改 curation 门槛，只需：

```bash
python 03_qc_curation.py --config config.yaml --output results/qc_002
# 将 paths.qc 改为 results/qc_002，再运行
python 04_analysis.py --config config.yaml --output results/analysis_002
```

`--output`只改当前阶段输出；下游要将 config 对应 paths 指向新目录。raw sorting 永不覆盖，每次 curated 独立保存。HDF5移动/size/mtime变化会触发身份检查，应重跑01；lazy JSON需要原始HDF5与插件，不是独立缓存数据。

## Scaling 与 QC

01验证唯一有序映射通道、fs、时长、gain/offset、geometry、NaN/Inf、Nyquist，以及 `raw_uV ≈ raw_numerical × gain_to_uV + offset_to_uV`。显式选择所有channel IDs，避免Neo额外信号行。浮点 bandpass 保留ADC-count数值域和gain，offset为0；检查reload与扩大上下文读取的一致性。短窗口含首尾，不自动删除大幅/flat通道；bounded QC不证明全记录信号有效，MAD诊断不是unit SNR。

02的 --run先全时长分块检查finite/range、filtered numerical→µV、int16物理量化误差，拒绝溢出、过大量化和整通道非零信号量化归零。真实SI writer写短probe验证ADC-count int16字节；sorting后核对完整recording.dat体量与首/中/尾抽样字节，**不声称完整binary逐字节一致**。gain annotation不代表binary自动写µV。量化与float waveform QC的差异保存在binary_scaling.json。

KS2内部150 Hz高通、CAR、whitening、minFR=0.1等保留软件默认，不因外部带通而跳过内部预处理；内部minFR与事后0.05Hz不等价。wrapper没有MATLAB RNG seed参数，不宣称GPU位级重现。KS2 revision及batch-boundary patch选择须显式记录；不能把主仓库默认分支或Python kilosort包当作KS2。

03逐项拒绝NaN/Inf并记录全部原因，三个阈值等号均通过；仅诊断指标NaN不额外拒绝。SNR采用本机SI average模板extremum / sampled MAD noise；ISI violation ratio不是简单违规百分比，公式见METHODS.md。自动接受只是 **candidate accepted**，不证明 biological neuron。零单元保存空metrics/curation及curated Sorting，省略analyzer/图。

## ISI 方法学

单一recording内的半开窗口[start,end)，整数frames判定边界，不与窗口外spikes形成ISI、不跨recording。8–100ms包含两端，保留已验证的指数拟合，不实施STTC/burst/network框架。

默认窗口0–180s，short_recording_policy=skip：短记录仅报告实际窗口与观察到的spike count，rate/ISI/fit不计算，不补数据。显式use_available按实际剩余秒数算FR，complete_interval=false。当前文件179.83s不能称完整三分钟。

median_scope=retained取8–100ms的ISI；all取窗口内全部相邻ISI。probability=retained counts/retained ISI总数，density再除bin width（ms）。保持median/15等宽，末bin可超过100ms但只纳入8–100ms数据；零bins参与拟合，λ单位ms⁻¹、R²严格>0.9。minimum_spikes是拟合门槛，基础counts/rate/ISI仍可报告（short skip除外）。另存all_isi_median与ISI<refractory_ms数量/比例；refractory cutoff是当前约定。指数分类不参与unit curation。

Notebooks只用于学习/检查已存在的信号、PSD、filter response、scaling、waveform/template/spatial footprint/amplitude/rate/ISI/有cap的ACG、accepted/rejected比较；不启动sorter。探索图与正式分析方法不同之处在cell说明。

## 第一次取得 GPU 节点

```text
login node → sbatch → NVIDIA GPU compute node → 02_sorting.py --run → Kilosort2
```

1. 修改服务器config路径，安装依赖/Maxwell插件，重跑01构建服务器路径的lazy Recording。
2. 在compute node检查nvidia-smi。对选定KS2 revision编译CUDA MEX，实际验证MATLAB license、Parallel Computing Toolbox、gpuDevice、KS2所需MEX；preflight发现检查不能替代runtime验证。
3. 运行 `python 02_sorting.py --output results/preflight_001`，检查environment.json、参数和commit；确认后设runtime_confirmed=true。
4. 修改 [slurm/run_sorting.sh](slurm/run_sorting.sh) 全部 **USER MUST EDIT**：partition/account、module、conda、KS2 path、project root、config/input/output。4CPU/32G/8h只是未经集群验证的初始示例，非推荐资源。
5. login node执行 `sbatch slurm/run_sorting.sh`；用 `squeue -u "$USER"`、`sacct -j JOB_ID`、`tail -f ks2-JOB_ID.log` 查看任务；结束检查run.json status和sorting_raw。
6. sorting成功后运行03/04，人工检查真实unit QC与生物学解释。

当前单个int16 binary理论大小7,337,064,000 bytes，约6.83GiB；recording.dat + temp_wh.dat及其他文件需要更多空间。RAM/GPU显存/时限须目标硬件实测。

## 当前验证状态

|层级|证据|
|---|---|
|code reviewed|直接API与已安装SI0.105.0 wrapper/metric源码核对|
|synthetic tested|本次scientific tests：实际analyzer/metrics/QC/curated/analysis/binary writer；sorter被mock/禁止|
|real-data preprocessing tested|本次01：1020channels、20kHz、179.83s、7个bounded windows，results/preprocessing_001；无全量binary|
|GPU runtime tested|**未完成**|
|real Kilosort2 tested|**未完成**；本机未满足KS2/MATLAB/NVIDIA条件|

```bash
python -m unittest discover -s tests -v
```

映射与验收见 [REFACTOR_RESULTS.md](REFACTOR_RESULTS.md)。STAGE1–4、PIPELINE_BUILD_RESULTS.md为旧版本历史证据，旧命令不是当前入口；不能把历史synthetic smoke当作真实sorter验证。
