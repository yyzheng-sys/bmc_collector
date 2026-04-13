# 机房资产管理平台（BMC + OS）

## 1. 项目定位

本项目用于机房资产盘点与管理，核心目标是围绕 SN 做快速检索、对账和导出。

支持的数据来源：

- BMC Redfish + SSH（主采集链路）
- OS SSH（补充链路，解决 BMC 无法完整识别 GPU/NPU 的场景）

适用场景：

- 多台服务器统一采集硬件信息
- 通过 SN 反查整机
- 手动补录物料并与自动采集结果并存

---

## 2. 核心功能

- 设备管理
  - 单台新增、编辑、删除
  - 批量新增、批量删除
  - 显示 BMC/OS 的 IP、账号、密码（个人内网使用）
  - 整机状态字段：整机挂账 / 整机转散件 / 散件挂账

- 采集管理
  - 单台采集
  - 全量采集
  - 定时自动采集
  - BMC 数据优先，OS 数据补充（GPU/NPU/CPU 等）

- 物料管理
  - 按类别分栏显示（CPU/GPU/NPU/内存/网卡/硬盘）
  - 支持手动物料新增、编辑、删除
  - 自动采集物料与手动物料分来源标识

- 检索与导出
  - 支持按设备字段搜索
  - 支持按物料 SN/型号/槽位搜索并定位到设备
  - 导出设备列表、全量物料、单台物料 Excel

---

## 3. 目录结构

```text
bmc_collector/
  app.py                      Flask 入口
  models.py                   数据模型（Device/Component）
  collector_service.py        采集调度与落库
  bmc_collector_hybrid.py     BMC 采集（Redfish + SSH）
  os_collector.py             OS 采集（SSH）
  config.py                   配置
  templates/index.html        前端页面
  static/style.css            前端样式
  requirements.txt            Python 依赖
  bmc_platform.db             SQLite 数据库（运行时生成）
```

---

## 4. 运行环境

- Python 3.9+
- Windows / Linux 均可
- 可访问目标设备 BMC 网络（必要）
- 可访问目标 OS SSH 网络（可选，用于补采）

安装依赖：

```bash
pip install -r requirements.txt
```

---

## 5. 启动步骤

在项目目录执行：

```bash
python app.py
```

默认访问地址：

```text
http://127.0.0.1:5010
```

说明：

- 首次启动会自动创建 SQLite 数据库
- 启动时会自动执行轻量迁移，补齐新增字段
- 默认端口 5010，可通过 `--port` 参数或 `BMC_PORT` 环境变量自定义

启动示例：

```bash
# 默认端口 5010
python app.py

# 自定义端口
python app.py --port 8080
```

---

## 6. 设备导入建议

可通过以下方式导入：

- 页面「新增设备」单条录入
- 页面「批量新增」按逗号分隔逐行导入
- 调用后端批量 API 导入 JSON 列表

批量行格式：

```text
server_model,server_version,asset_code,sn,bmc_ip,bmc_username,bmc_password,os_ip,os_username,os_password,asset_status
```

---

## 7. 关键接口（摘要）

设备：

- GET /api/devices
- POST /api/devices
- PUT /api/devices/<id>
- DELETE /api/devices/<id>
- POST /api/devices/batch
- POST /api/devices/batch-delete
- PATCH /api/devices/<id>/asset-status

物料：

- GET /api/devices/<id>/components
- POST /api/devices/<id>/components
- PUT /api/components/<id>
- DELETE /api/components/<id>

采集：

- POST /api/collect/<id>
- POST /api/collect/all

导出：

- GET /api/export/devices
- GET /api/export/components
- GET /api/export/device/<id>

---

## 8. 采集策略说明

1. 先走 BMC 采集（Redfish + SSH）
2. 再走 OS 采集补充（如 nvidia-smi / npu-smi）
3. 合并去重后写入数据库
4. 保留手动物料，不在自动采集中覆盖删除

针对多节点服务器（如 G5500/G8600）已做以下处理：

- 遍历全部 Systems 成员，不只取第一个节点
- 扫描全部 Chassis/PCIeDevices 补抓 GPU/NPU

---

## 9. 从 Git 仓库升级

当代码有更新时，可在部署目录执行以下操作完成升级：

```bash
# 1. 进入项目目录
cd /path/to/bmc_collector

# 2. 拉取最新代码
git pull origin main

# 3. 安装/更新依赖（如有新增依赖）
pip install -r requirements.txt

# 4. 重启服务
# 如果使用 systemd 管理:
sudo systemctl restart bmc_collector

# 如果是前台运行，先 Ctrl+C 停止旧进程，再启动:
python app.py
```

说明：

- 升级前无需手动迁移数据库，启动时会自动执行轻量迁移
- SQLite 数据库文件（`bmc_platform.db`）和加密密钥（`.encryption_key`）已在 .gitignore 中忽略，`git pull` 不会覆盖本地数据
- 如有本地修改冲突，可先执行 `git stash` 暂存，拉取后再 `git stash pop` 恢复

---

## 10. 安全与 Git 提交建议

已在 .gitignore 中忽略以下敏感或运行时文件：

- bmc_platform.db
- .encryption_key
- devices.txt
- init_devices.py

建议：

- 提交前执行 git status 确认不包含账号密码
- 内网使用时也建议定期更换口令

---

## 11. 常见问题

1. BMC 可连通但采集为空
   - 检查设备是否开启对应 Redfish 资源
   - 检查 BMC 账号权限

2. GPU 数量不完整
   - 检查是否配置了 OS SSH 信息
   - 在 OS 上确认 nvidia-smi 或 npu-smi 可用

3. 手动物料被覆盖
   - 当前逻辑不会删除 is_manual=true 的记录
   - 若出现异常，请检查数据库字段是否迁移成功

---

## 11. 许可证

仅用于内部机房资产管理。
