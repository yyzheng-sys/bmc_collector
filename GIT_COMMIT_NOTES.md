# Git 提交说明（可直接使用）

## 1) 提交前检查（敏感信息）

已在 `.gitignore` 中忽略：

- `bmc_platform.db`
- `.encryption_key`
- `devices.txt`
- `init_devices.py`

如这些文件此前已被跟踪，先执行：

```bash
git rm --cached devices.txt init_devices.py bmc_platform.db .encryption_key
```

仅取消追踪，不会删除本地文件。

## 2) 本次建议提交内容

功能点：

- 设备总览支持按列排序（升/降序）
- 机型/版本自动采集回填
- 内存采集带宽（MHz）并落库展示
- 网卡采集增强（芯片型号/链路规格），支持 MT2245XZ0LLH 映射为 `CX7` + `1*400G IB`
- 硬盘采集增强：SSH 解析加强 + Redfish 兜底（修复 7.6.241.150 硬盘缺失场景）

## 3) 建议提交命令

```bash
git add app.py bmc_collector_hybrid.py collector_service.py templates/index.html static/style.css README.md .gitignore GIT_COMMIT_NOTES.md
```

```bash
git commit -m "feat: 支持设备排序与资产采集增强（机型版本/内存带宽/网卡/硬盘兜底）" -m "- 设备总览表支持点击列排序\n- 自动回填机型与版本\n- 内存增加带宽采集并展示\n- 网卡信息优化（芯片型号+链路规格），适配 MT2245XZ0LLH\n- 硬盘采集增加 Redfish 兜底，修复 G5500 部分设备硬盘缺失" -m "安全说明：敏感文件已通过 .gitignore 忽略，且仅本地保留 devices.txt / init_devices.py / bmc_platform.db / .encryption_key" 
```

## 4) 推送前复核

```bash
git status
```

确认不存在以下敏感文件：

- `devices.txt`
- `init_devices.py`
- `.encryption_key`
- `bmc_platform.db`
