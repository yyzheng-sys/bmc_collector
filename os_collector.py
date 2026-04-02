#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OS 级硬件采集: 通过 SSH 登录操作系统，运行命令获取硬件信息
用于补充 BMC 无法采集的场景（如 GPU 卡信息）
"""

import re
import paramiko
from typing import Dict, List, Optional


class OSCollector:
    def __init__(self, ip: str, username: str, password: str):
        self.ip = ip
        self.username = username
        self.password = password
        self.ssh = None

    def connect(self) -> bool:
        try:
            self.ssh = paramiko.SSHClient()
            self.ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            self.ssh.connect(
                self.ip, username=self.username, password=self.password,
                timeout=30, allow_agent=False, look_for_keys=False,
                banner_timeout=30)
            return True
        except Exception as e:
            print(f"  OS SSH连接失败 {self.ip}: {str(e)}")
            return False

    def disconnect(self):
        if self.ssh:
            self.ssh.close()
            self.ssh = None

    def _run(self, cmd: str) -> str:
        if not self.ssh:
            return ''
        try:
            _, stdout, stderr = self.ssh.exec_command(cmd, timeout=30)
            return stdout.read().decode('utf-8', errors='ignore')
        except Exception:
            return ''

    # -------------------- GPU (NVIDIA) --------------------
    def collect_gpu_nvidia(self) -> List[Dict]:
        gpus = []
        output = self._run(
            'nvidia-smi --query-gpu=index,name,serial,memory.total '
            '--format=csv,noheader,nounits 2>/dev/null')
        if not output.strip():
            return gpus
        for line in output.strip().split('\n'):
            parts = [p.strip() for p in line.split(',')]
            if len(parts) >= 4:
                gpus.append({
                    'slot': f'GPU {parts[0]}',
                    'manufacturer': 'NVIDIA',
                    'type': parts[1],
                    'serial': parts[2] if parts[2] != '[N/A]' else '',
                    'capacity': f'{parts[3]} MiB',
                    'processor_type': 'gpu',
                })
            elif len(parts) >= 2:
                gpus.append({
                    'slot': f'GPU {parts[0]}',
                    'manufacturer': 'NVIDIA',
                    'type': parts[1] if len(parts) > 1 else '',
                    'serial': parts[2] if len(parts) > 2 and parts[2] != '[N/A]' else '',
                    'capacity': '',
                    'processor_type': 'gpu',
                })
        return gpus

    # -------------------- NPU (Huawei Ascend) --------------------
    def collect_npu_huawei(self) -> List[Dict]:
        npus = []
        output = self._run('npu-smi info -l 2>/dev/null')
        if not output.strip():
            return npus
        # 解析 NPU 列表
        npu_ids = re.findall(r'NPU ID\s*:\s*(\d+)', output)
        for npu_id in npu_ids:
            detail = self._run(f'npu-smi info -t board -i {npu_id} 2>/dev/null')
            npu = {
                'slot': f'NPU {npu_id}',
                'manufacturer': 'Huawei',
                'type': '',
                'serial': '',
                'capacity': '',
                'processor_type': 'npu',
            }
            for line in detail.split('\n'):
                if 'Chip Name' in line:
                    parts = line.split(':', 1)
                    if len(parts) == 2:
                        npu['type'] = parts[1].strip()
                elif 'Board Id' in line or 'Serial Number' in line:
                    parts = line.split(':', 1)
                    if len(parts) == 2 and parts[1].strip():
                        npu['serial'] = parts[1].strip()
            npus.append(npu)
        return npus

    # -------------------- CPU --------------------
    def collect_cpu(self) -> List[Dict]:
        cpus = []
        output = self._run('lscpu 2>/dev/null')
        if not output:
            return cpus
        cpu = {'processor_type': 'cpu', 'slot': 'CPU', 'manufacturer': '', 'type': '', 'serial': ''}
        for line in output.split('\n'):
            if line.startswith('Model name:'):
                cpu['type'] = line.split(':', 1)[1].strip()
            elif line.startswith('Vendor ID:'):
                cpu['manufacturer'] = line.split(':', 1)[1].strip()
            elif line.startswith('Socket(s):'):
                try:
                    count = int(line.split(':', 1)[1].strip())
                    cpus = [dict(cpu, slot=f'CPU {i}') for i in range(count)]
                except ValueError:
                    cpus = [cpu]
        if not cpus:
            cpus = [cpu]
        return cpus

    # -------------------- Memory --------------------
    def collect_memory(self) -> List[Dict]:
        mems = []
        output = self._run('sudo dmidecode -t memory 2>/dev/null || dmidecode -t memory 2>/dev/null')
        if not output:
            return mems
        current = None
        for line in output.split('\n'):
            line = line.strip()
            if line == 'Memory Device':
                if current and current.get('capacity') and current['capacity'] != 'No Module Installed':
                    mems.append(current)
                current = {'component_type': 'memory', 'slot': '', 'manufacturer': '',
                           'model': '', 'serial': '', 'capacity': ''}
            elif current is not None:
                if line.startswith('Size:'):
                    current['capacity'] = line.split(':', 1)[1].strip()
                elif line.startswith('Locator:'):
                    current['slot'] = line.split(':', 1)[1].strip()
                elif line.startswith('Manufacturer:'):
                    current['manufacturer'] = line.split(':', 1)[1].strip()
                elif line.startswith('Serial Number:'):
                    sn = line.split(':', 1)[1].strip()
                    if sn and sn.lower() not in ('not specified', 'unknown', 'no dimm'):
                        current['serial'] = sn
                elif line.startswith('Part Number:'):
                    current['model'] = line.split(':', 1)[1].strip()
        if current and current.get('capacity') and current['capacity'] != 'No Module Installed':
            mems.append(current)
        return mems

    # -------------------- Disk --------------------
    def collect_disk(self) -> List[Dict]:
        disks = []
        output = self._run('lsblk -d -o NAME,MODEL,SERIAL,SIZE -n 2>/dev/null')
        if not output:
            return disks
        idx = 0
        for line in output.strip().split('\n'):
            parts = line.split()
            if len(parts) < 2:
                continue
            name = parts[0]
            # 跳过 loop 设备
            if name.startswith('loop'):
                continue
            idx += 1
            disks.append({
                'slot': f'Disk {idx} ({name})',
                'manufacturer': '',
                'model': parts[1] if len(parts) > 1 else '',
                'serial': parts[2] if len(parts) > 2 else '',
                'capacity': parts[3] if len(parts) > 3 else '',
            })
        return disks

    # -------------------- NIC --------------------
    def collect_nic(self) -> List[Dict]:
        nics = []
        output = self._run("lspci 2>/dev/null | grep -i 'ethernet\\|network'")
        if not output:
            return nics
        idx = 0
        for line in output.strip().split('\n'):
            if not line.strip():
                continue
            idx += 1
            # 格式: 00:1f.6 Ethernet controller: Intel Corporation ...
            match = re.match(r'[\da-f:.]+\s+\w[\w\s]*:\s*(.*)', line, re.I)
            model = match.group(1).strip() if match else line.strip()
            nics.append({
                'slot': f'NIC {idx}',
                'manufacturer': '',
                'model': model,
                'serial': '',
                'speed': '',
            })
        return nics

    # -------------------- 全量采集 --------------------
    def collect_all(self) -> Dict:
        """返回 OS 级采集结果"""
        if not self.connect():
            return {}
        try:
            result = {
                'gpus': self.collect_gpu_nvidia(),
                'npus': self.collect_npu_huawei(),
                'cpus': self.collect_cpu(),
                'memory': self.collect_memory(),
                'disks': self.collect_disk(),
                'nics': self.collect_nic(),
            }
            return result
        finally:
            self.disconnect()
