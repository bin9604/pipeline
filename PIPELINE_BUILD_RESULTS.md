# Pipeline 构建交付记录

2026-10-05 已补齐 single-recording、独立batch、受约束concatenation、Kilosort2调用模块、SortingAnalyzer、单元指标、自动筛选、人工review图、独立ISI拟合和Slurm入口。**没有运行真实 spike sorting，也没有安装或替代算法。**

主入口是 scripts/run_pipeline.py，主配置是 config/sorting_config.yaml。完整操作方式见 README.md；指标公式、文档与本机源码差异、未报告约定见 METHODS.md。

## 实际验证

1. `python -m unittest discover -s tests -v`：8项测试通过。测试真实SI analyzer、FR/SNR数学对应、accepted/rejected绘图、边界阈值、多理由拒绝、非有限值、零单位导出、不足180s策略、concatenation兼容性/边界局部时间、dry-run绝不调用sorter和不覆盖输出目录。
2. 真实文件 final dry-run：`results/Development_2953_5month/pipeline_dry_run_002/run_manifest.json` 为 completed，sorting_started=false。只做reader、lazy过滤、随机短窗QC、定义/metadata/config/sorter参数导出。
3. 5秒合成数据final smoke：`results/synthetic_pipeline_smoke_002/run_manifest.json` 为 completed，synthetic=true，sorting_started=false。采用ground-truth spike trains，3个原始单位全部完成后处理及独立ISI拟合。示例采用较短window/spike门槛，只是软件测试；主配置仍为180s/30 spikes。
4. 合成QC图已做视觉检查，原始notebook保留。

短合成recording的presence_ratio出现NaN并产生warning（默认60s诊断窗超过5s）；这是预期诊断限制，manifest记录warning，未把诊断NaN当成unit拒绝依据。Windows临时binary测试观察到SI内部ResourceWarning，测试释放recording/memmap后正常清理；未改动第三方库。

## 留待真实环境的验收

Kilosort2未被调用，因此不能验证MATLAB许可证/toolbox、CUDA MEX、GPU显存、实际耗时、KS2输出完整性或实际单元质量。Slurm是可配置提交模板，未提交HPC作业；批处理/concatenation逻辑用合成数据测试，未进行真实多文件sorting。

默认metrics.definitions_acknowledged=false阻止未经解释的正式sorting/QC；ISI conventions_acknowledged=false在完整pipeline明确延期。它们不是遗留未实现模块，而是论文信息不足的显式科研控制。确认当前SI约定或补充文献后修改配置并使用新run目录。

179.83s文件在严格180s下会产生short_recording_skipped；如明确允许use_available，必须保留complete_interval=false，不宣称完整3min。KS2 internal preprocessing与显式bandpass不同；concatenation seam对sortingQC的影响仍需人工review，原始spikes不会被默默删除。
