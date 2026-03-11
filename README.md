# BMC硬件信息采集工具使用说明

## 工具简介

本工具用于通过BMC（基板管理控制器）采集服务器的硬件信息，包括：
- 产品序列号
- 主板序列号
- 机箱序列号
- CPU信息（制造商、型号、序列号）
- 内存信息（制造商、容量、序列号）
- 硬盘信息（制造商、容量、序列号）

## 文件说明

1. **bmc_collector_hybrid.py** - 主采集工具
2. **devices.txt** - 设备配置文件（包含IP、用户名、密码）
3. **README.md** - 使用说明文档

## 环境要求

- Python 3.6+
- 需要安装的Python库：
  - paramiko（用于SSH连接）
  - requests（用于Redfish API）
  - pandas（用于生成Excel表格）
  - openpyxl（用于Excel文件操作）

安装依赖：
```bash
pip install paramiko requests pandas openpyxl
```

## 使用方法

### 步骤1：安装依赖

首先确保已安装Python 3.6+，然后安装所需的Python库：

```bash
pip install paramiko requests pandas openpyxl
```

### 步骤2：配置设备信息

打开 `devices.txt` 文件，修改为您的设备信息。文件格式如下：

```
70.189.131.92 FDuser Fusion@1234#$
70.189.131.153 FDuser Fusion@1235#$
70.189.131.213 FDuser Fusion@1236#$
```

**配置说明**：
- 每行代表一台设备
- 格式：`IP地址 用户名 密码`（用空格分隔）
- 支持使用 `#` 开头的注释行
- 您可以添加、删除或修改设备列表，工具会自动处理所有列出的设备

### 步骤3：运行工具

进入 `bmc_collector` 目录，运行采集工具：

```bash
cd bmc_collector
python bmc_collector_hybrid.py
```

### 步骤4：查看结果

工具运行完成后，会在控制台输出采集结果，并在当前目录生成以下文件：

1. **bmc_hardware_info.json** - JSON格式的原始数据
2. **bmc_hardware_info.xlsx** - Excel格式的表格数据

打开Excel文件即可查看整理好的硬件序列号表格。

## Excel表格格式

Excel文件采用组合布局，每行按索引顺序组合CPU、内存和硬盘信息，包含以下列：

**基础信息**（每行都显示）：
- IP地址 - 设备的BMC IP地址
- 产品序列号 - 设备产品序列号
- 主板序列号 - 设备主板序列号
- 机箱序列号 - 设备机箱序列号

**硬件信息**（按索引组合）：
- CPU制造商、CPU型号、CPU序列号 - 第i个CPU
- 内存制造商、内存序列号、内存容量 - 第i条内存
- 硬盘制造商、硬盘序列号、硬盘容量 - 第i个硬盘

**示例**：
```
IP地址          产品序列号         主板序列号      机箱序列号  CPU制造商              CPU型号                          CPU序列号         内存制造商  内存序列号  内存容量  硬盘制造商  硬盘序列号         硬盘容量
70.189.131.92   02Y054X6P4000014  XD2331001430   N/A         Intel(R) Corporation  Intel(R) Xeon(R) Platinum 8450H  CAECD9BEB1D618  Micron      3EA92C58   32768 MB  Samsung     S63UNG0T324738  3.493 TB
70.189.131.92   02Y054X6P4000014  XD2331001430   N/A         Intel(R) Corporation  Intel(R) Xeon(R) Platinum 8450H  CABDC32A9D92C30A  Micron      3EA967D9   32768 MB  Samsung     S63UNG0T324719  3.493 TB
70.189.131.92   02Y054X6P4000014  XD2331001430   N/A                                                                 Micron      3EA967D7   32768 MB
70.189.131.92   02Y054X6P4000014  XD2331001430   N/A                                                                 Micron      3EA96948   32768 MB
70.189.131.92   02Y054X6P4000014  XD2331001430   N/A                                                                 Micron      3EA92C87   32768 MB
70.189.131.92   02Y054X6P4000014  XD2331001430   N/A                                                                 Micron      3EA92C45   32768 MB
70.189.131.92   02Y054X6P4000014  XD2331001430   N/A                                                                 Micron      3EA92C37   32768 MB
70.189.131.92   02Y054X6P4000014  XD2331001430   N/A                                                                 Micron      3EA91A8B   32768 MB
```

## 工作原理

本工具采用混合方式采集信息：

1. **SSH连接**：使用SSH连接到BMC，执行 `ipmcget` 命令获取FRU和硬盘信息
2. **Redfish API**：使用Redfish API获取CPU和内存信息

## 注意事项

1. 确保网络可以访问BMC IP地址
2. 确保BMC账户密码正确
3. 如果设备不支持SSH或Redfish API，可能无法获取部分信息
4. 工具会自动处理连接失败的情况，不会中断整个采集过程
5. 设备配置文件 `devices.txt` 可以随时修改，方便添加或删除设备
6. 输出文件会自动添加时间戳，避免覆盖历史数据

## 故障排除

### SSH连接失败
- 检查网络连接
- 检查用户名和密码是否正确
- 检查BMC是否启用SSH服务

### Redfish API连接失败
- 检查BMC是否支持Redfish API
- 检查用户名和密码是否正确
- 可能会话数量超限，等待一段时间后重试

### 无法获取CPU/内存信息
- 确保BMC支持Redfish API
- 检查Redfish API的访问权限

### 无法获取硬盘信息
- 确保BMC支持 `ipmcget` 命令
- 检查存储控制器是否正常工作

## 测试结果

已成功测试设备：70.189.131.92

采集到的信息：
- 产品序列号: 02Y054X6P4000014
- 主板序列号: XD2331001430
- CPU: 2个Intel Xeon Platinum 8450H
- 内存: 8条Micron 32GB内存
- 硬盘: 2个Samsung 3.493TB SSD

## 优势

1. **配置分离**：设备信息单独存储在 `devices.txt` 文件中，方便修改和管理
2. **表格输出**：结果自动生成Excel表格，方便查看和统计
3. **混合采集**：结合SSH和Redfish API，获取更全面的硬件信息
4. **容错处理**：单个设备失败不影响其他设备的采集

## 许可证

本工具仅供内部使用。
