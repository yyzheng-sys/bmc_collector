#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import paramiko
import requests
import urllib3
import os
import json
import re
import pandas as pd
from typing import Dict, List, Optional

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 禁用代理
os.environ['NO_PROXY'] = '*'
os.environ['no_proxy'] = '*'

class BMCHybridCollector:
    def __init__(self, ip: str, username: str, password: str):
        self.ip = ip
        self.username = username
        self.password = password
        self.ssh = None
        self.session = None
        self.base_url = f"https://{ip}"
        self.auth_token = None
        self.session_location = None  # Redfish session URL for proper logout
    
    def connect_ssh(self) -> bool:
        try:
            self.ssh = paramiko.SSHClient()
            self.ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            self.ssh.connect(
                self.ip,
                username=self.username,
                password=self.password,
                timeout=30,
                allow_agent=False,
                look_for_keys=False,
                banner_timeout=30
            )
            return True
        except Exception as e:
            print(f"  SSH连接失败: {str(e)}")
            return False
    
    def disconnect_ssh(self):
        if self.ssh:
            self.ssh.close()
            self.ssh = None
    
    def run_ssh_command(self, command: str) -> Optional[str]:
        if not self.ssh:
            return None
        
        try:
            stdin, stdout, stderr = self.ssh.exec_command(command, timeout=30)
            output = stdout.read().decode('utf-8', errors='ignore')
            error = stderr.read().decode('utf-8', errors='ignore')
            
            if error and 'COMMAND NOT SUPPORTED' not in error:
                print(f"  命令错误: {error[:200]}")
            
            return output
        except Exception as e:
            print(f"  命令执行异常: {str(e)}")
            return None
    
    def connect_redfish(self) -> bool:
        try:
            self.session = requests.Session()
            self.session.verify = False
            self.session.trust_env = False
            self.session.headers.update({'Content-Type': 'application/json'})
            
            login_url = f"{self.base_url}/redfish/v1/SessionService/Sessions"
            payload = {
                'UserName': self.username,
                'Password': self.password
            }
            
            response = self.session.post(login_url, json=payload, timeout=30)

            if response.status_code == 201:
                self.auth_token = response.headers.get('X-Auth-Token')
                self.session_location = response.headers.get('Location', '')
                if self.auth_token:
                    self.session.headers.update({'X-Auth-Token': self.auth_token})
                    return True

            # 兼容部分 iBMC 固件: Session 登录失败但支持 Basic Auth
            self.session.auth = (self.username, self.password)
            probe = self.session.get(f"{self.base_url}/redfish/v1", timeout=30)
            if probe.status_code == 200:
                self.auth_token = None
                return True

            return False
        except Exception as e:
            return False
    
    def disconnect_redfish(self):
        if self.session:
            if self.auth_token:
                try:
                    # 正确注销: 使用登录时返回的 session URL
                    if self.session_location:
                        loc = self.session_location
                        if not loc.startswith('http'):
                            loc = f"{self.base_url}{loc}"
                        self.session.delete(loc, timeout=10)
                except Exception:
                    pass
            self.session.close()
            self.session = None
            self.auth_token = None
            self.session_location = None
    
    def get_fru_info(self) -> Dict:
        fru_info = {}
        
        if not self.connect_ssh():
            return fru_info
        
        try:
            output = self.run_ssh_command('ipmcget -d fru')
            if output:
                fru_info = self._parse_fru(output)
        except Exception as e:
            print(f"  获取FRU信息异常: {str(e)}")
        
        self.disconnect_ssh()
        return fru_info
    
    def _parse_fru(self, output: str) -> Dict:
        info = {}
        
        for line in output.split('\n'):
            line = line.strip()
            if 'Product Serial Number' in line:
                parts = line.split(':', 1)
                if len(parts) == 2:
                    info['product_serial'] = parts[1].strip()
            elif 'Board Serial Number' in line:
                parts = line.split(':', 1)
                if len(parts) == 2:
                    info['board_serial'] = parts[1].strip()
            elif 'Chassis Serial' in line:
                parts = line.split(':', 1)
                if len(parts) == 2:
                    info['chassis_serial'] = parts[1].strip()
        
        return info

    def _extract_model_version(self, model_text: str) -> Dict:
        text = (model_text or '').strip()
        if not text:
            return {'server_model': '', 'server_version': ''}

        # 常见格式: "2288H V6" / "G5500 V7"
        match = re.search(r'^(.*?)\s+(V\d+)\b', text, re.IGNORECASE)
        if match:
            return {
                'server_model': match.group(1).strip(),
                'server_version': match.group(2).upper(),
            }
        return {'server_model': text, 'server_version': ''}

    def _ensure_redfish(self) -> bool:
        """确保 Redfish 连接可用，已连接则复用"""
        if self.session and self.auth_token:
            return True
        if self.session:
            # 有 session 但无 token (Basic Auth)
            return True
        return self.connect_redfish()

    def get_system_info(self) -> Dict:
        """采集整机基础信息，用于自动回填机型/版本"""
        system_info = {
            'server_model': '',
            'server_version': '',
            'manufacturer': '',
            'serial': '',
            'raw_model': '',
        }

        if not self._ensure_redfish():
            return system_info

        try:
            resp = self.session.get(f"{self.base_url}/redfish/v1/Systems", timeout=30)
            if resp.status_code != 200:
                return system_info

            systems_data = resp.json()
            for member in systems_data.get('Members', []):
                system_url = member.get('@odata.id', '')
                if not system_url:
                    continue
                detail = self.session.get(f"{self.base_url}{system_url}", timeout=30)
                if detail.status_code != 200:
                    continue

                d = detail.json()
                model_raw = (
                    d.get('Model', '')
                    or d.get('Name', '')
                    or d.get('SKU', '')
                )
                # 过滤 Redfish 通用默认名称
                _generic = {'computer system', 'system', 'server', ''}
                if model_raw.strip().lower() in _generic:
                    model_raw = ''

                # 从 Oem 字段提取型号/版本（华为/xFusion 设备常见）
                oem = d.get('Oem', {})
                oem_product_name = ''
                oem_product_version = ''
                for vendor in oem.values():
                    if isinstance(vendor, dict):
                        oem_product_name = oem_product_name or vendor.get('ProductName', '') or ''
                        oem_product_version = oem_product_version or vendor.get('ProductVersion', '') or ''

                # 如果标准 Model 为空/通用，优先使用 OEM ProductName
                if not model_raw and oem_product_name:
                    model_raw = oem_product_name

                parsed = self._extract_model_version(model_raw)

                version = (
                    d.get('Version', '')
                    or d.get('BiosVersion', '')
                    or ''
                )
                if version and re.match(r'^\d+(\.\d+)*$', str(version)):
                    # 避免把 BIOS 版本号误当成服务器版本
                    version = ''

                # OEM ProductVersion 作为版本兜底
                if not parsed.get('server_version', '') and not version and oem_product_version:
                    if re.match(r'^V\d+', oem_product_version, re.IGNORECASE):
                        version = oem_product_version.upper()

                system_info = {
                    'server_model': parsed.get('server_model', ''),
                    'server_version': parsed.get('server_version', '') or version,
                    'manufacturer': d.get('Manufacturer', ''),
                    'serial': d.get('SerialNumber', ''),
                    'raw_model': model_raw,
                }

                if system_info['server_model']:
                    break
        except Exception as e:
            print(f"  获取系统信息异常: {str(e)}")

        return system_info
    
    def get_processor_info(self) -> List[Dict]:
        """获取所有处理器信息（CPU / GPU / NPU 等），遍历所有 Systems 节点"""
        proc_list = []
        
        if not self._ensure_redfish():
            return proc_list
        
        try:
            response = self.session.get(f"{self.base_url}/redfish/v1/Systems", timeout=30)
            if response.status_code == 200:
                data = response.json()
                # 遍历所有系统节点(G5500/G8600 多节点服务器有多个 Systems 成员)
                for sys_member in data.get('Members', []):
                    system_url = sys_member['@odata.id']
                    proc_resp = self.session.get(
                        f"{self.base_url}{system_url}/Processors", timeout=30)
                    if proc_resp.status_code != 200:
                        continue
                    processors_data = proc_resp.json()
                    for member in processors_data.get('Members', []):
                        processor_url = member['@odata.id']
                        resp = self.session.get(
                            f"{self.base_url}{processor_url}", timeout=30)
                        if resp.status_code != 200:
                            continue
                        pd_ = resp.json()
                        proc = {}
                        proc['manufacturer'] = pd_.get('Manufacturer', '')
                        proc['type'] = pd_.get('Model', '')
                        proc['slot'] = pd_.get('Name', pd_.get('Id', ''))

                        # --- SN: 尝试多个字段 ---
                        sn = pd_.get('SerialNumber', '')
                        if not sn:
                            oem = pd_.get('Oem', {})
                            for vendor in oem.values():
                                if isinstance(vendor, dict):
                                    sn = (vendor.get('SerialNumber', '')
                                          or vendor.get('SN', ''))
                                    if sn:
                                        break
                        proc['serial'] = sn

                        # --- 处理器类型判断 ---
                        proc_type_raw = pd_.get('ProcessorType', '').upper()
                        name_model = (proc.get('type', '') + proc.get('slot', '')
                                      + proc.get('manufacturer', '')).upper()
                        
                        if proc_type_raw == 'GPU' or any(
                                kw in name_model for kw in
                                ['GPU', 'NVIDIA', 'TESLA', 'RADEON', 'VGA', 'DISPLAY']):
                            proc['processor_type'] = 'gpu'
                        elif proc_type_raw == 'OEM' or any(
                                kw in name_model for kw in
                                ['NPU', 'ASCEND', 'ATLAS', 'DAVINCI']):
                            proc['processor_type'] = 'npu'
                        elif any(kw in name_model for kw in ['FPGA']):
                            proc['processor_type'] = 'gpu'
                        else:
                            proc['processor_type'] = 'cpu'
                        
                        if proc:
                            proc_list.append(proc)
        except Exception as e:
            print(f"  获取处理器信息异常: {str(e)}")
        
        return proc_list

    def get_pcie_gpu_info(self) -> List[Dict]:
        """扫描所有 Chassis 下 PCIeDevices，查找 GPU/NPU 卡"""
        gpu_list = []
        
        if not self._ensure_redfish():
            return gpu_list
        
        try:
            resp = self.session.get(
                f"{self.base_url}/redfish/v1/Chassis", timeout=30)
            if resp.status_code != 200:
                return gpu_list
            
            chassis_data = resp.json()
            for chassis_member in chassis_data.get('Members', []):
                chassis_url = chassis_member['@odata.id']
                pcie_resp = self.session.get(
                    f"{self.base_url}{chassis_url}/PCIeDevices", timeout=30)
                if pcie_resp.status_code != 200:
                    continue
                pcie_data = pcie_resp.json()
                for pcie_member in pcie_data.get('Members', []):
                    pcie_url = pcie_member['@odata.id']
                    dev_resp = self.session.get(
                        f"{self.base_url}{pcie_url}", timeout=30)
                    if dev_resp.status_code != 200:
                        continue
                    d = dev_resp.json()
                    name = (d.get('Name', '') + d.get('Model', '')
                            + d.get('DeviceType', '')).upper()
                    is_gpu = any(kw in name for kw in
                                 ['GPU', 'NVIDIA', 'TESLA', 'RADEON',
                                  'ACCELERAT', 'VGA', 'DISPLAY'])
                    is_npu = any(kw in name for kw in
                                 ['NPU', 'ASCEND', 'DAVINCI'])
                    if not is_gpu and not is_npu:
                        continue
                    sn = d.get('SerialNumber', '')
                    if not sn:
                        oem = d.get('Oem', {})
                        for v in oem.values():
                            if isinstance(v, dict):
                                sn = (v.get('SerialNumber', '')
                                      or v.get('SN', ''))
                                if sn:
                                    break
                    gpu_list.append({
                        'manufacturer': d.get('Manufacturer', ''),
                        'type': d.get('Model', d.get('Name', '')),
                        'slot': d.get('Name', d.get('Id', '')),
                        'serial': sn,
                        'processor_type': 'npu' if is_npu else 'gpu',
                    })
        except Exception as e:
            print(f"  获取PCIe GPU信息异常: {str(e)}")
        
        return gpu_list
    
    # 保持向后兼容
    def get_cpu_info(self) -> List[Dict]:
        return [p for p in self.get_processor_info() if p.get('processor_type') == 'cpu']
    
    def get_memory_info(self) -> List[Dict]:
        memory_list = []
        
        if not self._ensure_redfish():
            return memory_list
        
        try:
            response = self.session.get(f"{self.base_url}/redfish/v1/Systems", timeout=30)
            if response.status_code == 200:
                data = response.json()
                for sys_member in data.get('Members', []):
                    system_url = sys_member['@odata.id']
                    response = self.session.get(f"{self.base_url}{system_url}/Memory", timeout=30)
                    if response.status_code != 200:
                        continue
                    memory_data = response.json()
                    for member in memory_data.get('Members', []):
                        memory_url = member['@odata.id']
                        response = self.session.get(f"{self.base_url}{memory_url}", timeout=30)
                        if response.status_code != 200:
                            continue

                        mem_data = response.json()
                        mem = {}
                        if 'Manufacturer' in mem_data:
                            mem['manufacturer'] = mem_data['Manufacturer']

                        # --- SN: 尝试多种字段路径 ---
                        sn = mem_data.get('SerialNumber', '')
                        if not sn:
                            oem = mem_data.get('Oem', {})
                            for vendor in oem.values():
                                if isinstance(vendor, dict):
                                    sn = vendor.get('SerialNumber', '') or vendor.get('SN', '')
                                    if sn:
                                        break
                        mem['serial'] = sn

                        if 'CapacityMiB' in mem_data:
                            mem['capacity'] = f"{mem_data['CapacityMiB']} MB"

                        speed = (mem_data.get('OperatingSpeedMhz')
                                 or mem_data.get('MemoryOperatingSpeedMhz'))
                        if not speed:
                            allowed_speeds = mem_data.get('AllowedSpeedsMHz', [])
                            if isinstance(allowed_speeds, list) and allowed_speeds:
                                speed = allowed_speeds[0]
                        if speed:
                            mem['bandwidth'] = f"{speed} MHz"

                        mem['model'] = mem_data.get('PartNumber', mem_data.get('MemoryDeviceType', ''))
                        mem['slot'] = mem_data.get('Name', mem_data.get('Id', ''))
                        if mem:
                            memory_list.append(mem)
        except Exception as e:
            print(f"  获取内存信息异常: {str(e)}")
        
        return memory_list
    
    def get_disk_info(self) -> List[Dict]:
        disk_list = []

        # 先走 BMC SSH 命令
        if self.connect_ssh():
            try:
                cmd_list = [
                    'ipmcget -t storage -d pdinfo -v all',
                    'ipmcget -d pdinfo',
                ]
                for cmd in cmd_list:
                    output = self.run_ssh_command(cmd)
                    if output:
                        disk_list = self._parse_disks(output)
                        if disk_list:
                            break
            except Exception as e:
                print(f"  获取硬盘信息异常: {str(e)}")
            finally:
                self.disconnect_ssh()

        # SSH 未拿到时，Redfish 兜底
        if not disk_list:
            disk_list = self._get_disk_info_redfish()

        return disk_list

    def _parse_disks(self, output: str) -> List[Dict]:
        disks = []
        current_disk = {}

        def flush_current():
            if current_disk and (current_disk.get('serial') or current_disk.get('model')
                                 or current_disk.get('capacity')):
                disks.append(dict(current_disk))

        for raw_line in output.split('\n'):
            line = raw_line.strip()
            if not line:
                continue

            # 新盘分隔（兼容不同 iBMC 输出）
            if ((line.startswith('ID') or line.startswith('Disk') or line.startswith('Slot'))
                    and ':' in line and 'Device Name' not in line and current_disk):
                flush_current()
                current_disk = {}

            if ':' not in line:
                continue

            key, value = line.split(':', 1)
            key = key.strip().lower()
            value = value.strip()

            if key in ['serial number', 'serial', 'sn']:
                current_disk['serial'] = value
            elif key in ['manufacturer', 'vendor']:
                current_disk['manufacturer'] = value
            elif key in ['capacity', 'size']:
                current_disk['capacity'] = value
            elif key in ['model', 'device model', 'product']:
                current_disk['model'] = value

        flush_current()
        return disks

    def _format_capacity(self, capacity_bytes) -> str:
        try:
            size = int(capacity_bytes)
            tb = size / (1024 ** 4)
            if tb >= 1:
                return f"{tb:.3f} TB"
            gb = size / (1024 ** 3)
            return f"{gb:.1f} GB"
        except Exception:
            return ''

    def _get_disk_info_redfish(self) -> List[Dict]:
        disk_list = []

        if not self._ensure_redfish():
            return disk_list

        try:
            # 路径1: Systems/*/Storage
            systems_resp = self.session.get(f"{self.base_url}/redfish/v1/Systems", timeout=30)
            if systems_resp.status_code == 200:
                systems_data = systems_resp.json()
                for sys_member in systems_data.get('Members', []):
                    system_url = sys_member.get('@odata.id', '')
                    if not system_url:
                        continue
                    storage_resp = self.session.get(
                        f"{self.base_url}{system_url}/Storage", timeout=30)
                    if storage_resp.status_code != 200:
                        continue
                    storage_data = storage_resp.json()
                    for storage_member in storage_data.get('Members', []):
                        storage_url = storage_member.get('@odata.id', '')
                        if not storage_url:
                            continue
                        storage_detail = self.session.get(
                            f"{self.base_url}{storage_url}", timeout=30)
                        if storage_detail.status_code != 200:
                            continue
                        sdata = storage_detail.json()
                        for drive in sdata.get('Drives', []):
                            drive_url = drive.get('@odata.id', '')
                            if not drive_url:
                                continue
                            drive_resp = self.session.get(
                                f"{self.base_url}{drive_url}", timeout=30)
                            if drive_resp.status_code != 200:
                                continue
                            dd = drive_resp.json()
                            disk_list.append({
                                'serial': dd.get('SerialNumber', ''),
                                'manufacturer': dd.get('Manufacturer', ''),
                                'model': dd.get('Model', ''),
                                'capacity': self._format_capacity(dd.get('CapacityBytes', 0)),
                            })

            # 路径2: Chassis/*/Drives（用于部分 G5500/G8600 固件）
            if not disk_list:
                chassis_resp = self.session.get(f"{self.base_url}/redfish/v1/Chassis", timeout=30)
                if chassis_resp.status_code == 200:
                    chassis_data = chassis_resp.json()
                    for ch_member in chassis_data.get('Members', []):
                        ch_url = ch_member.get('@odata.id', '')
                        if not ch_url:
                            continue
                        drives_resp = self.session.get(
                            f"{self.base_url}{ch_url}/Drives", timeout=30)
                        if drives_resp.status_code != 200:
                            continue
                        for d_member in drives_resp.json().get('Members', []):
                            d_url = d_member.get('@odata.id', '')
                            if not d_url:
                                continue
                            d_resp = self.session.get(f"{self.base_url}{d_url}", timeout=30)
                            if d_resp.status_code != 200:
                                continue
                            dd = d_resp.json()
                            disk_list.append({
                                'serial': dd.get('SerialNumber', ''),
                                'manufacturer': dd.get('Manufacturer', ''),
                                'model': dd.get('Model', dd.get('Name', '')),
                                'capacity': self._format_capacity(dd.get('CapacityBytes', 0)),
                            })
        except Exception as e:
            print(f"  Redfish获取硬盘信息异常: {str(e)}")

        # 清理空盘项
        cleaned = []
        for d in disk_list:
            if d.get('serial') or d.get('model') or d.get('capacity'):
                cleaned.append(d)
        return cleaned
    
    def get_all_info(self) -> Dict:
        print(f"\n正在采集 {self.ip} 的信息...")
        
        # 建立单一 Redfish 连接，所有采集方法复用
        self.connect_redfish()
        
        try:
            processors = self.get_processor_info()
            
            # 补充: 仅当 Processors 端点未采集到 GPU/NPU 时，才从 PCIeDevices 扫描
            gpu_npu_from_proc = [p for p in processors if p.get('processor_type') in ('gpu', 'npu')]
            if not gpu_npu_from_proc:
                pcie_gpus = self.get_pcie_gpu_info()
                processors.extend(pcie_gpus)
            
            info = {
                'ip': self.ip,
                'fru': self.get_fru_info(),
                'system': self.get_system_info(),
                'processors': processors,
                'cpus': [p for p in processors if p.get('processor_type') == 'cpu'],
                'memory': self.get_memory_info(),
                'disks': self.get_disk_info()
            }
        finally:
            # 统一注销 Redfish session，避免泄漏
            self.disconnect_redfish()
        
        return info


def save_to_excel(all_results: List[Dict], output_file: str):
    data = []
    
    for result in all_results:
        ip = result['ip']
        fru = result['fru']
        cpus = result['cpus']
        memory = result['memory']
        disks = result['disks']
        
        # FRU信息
        product_serial = fru.get('product_serial', 'N/A')
        board_serial = fru.get('board_serial', 'N/A')
        chassis_serial = fru.get('chassis_serial', 'N/A')
        
        # 确定最大行数（CPU、内存、硬盘的最大数量）
        max_rows = max(len(cpus), len(memory), len(disks))
        if max_rows == 0:
            max_rows = 1
        
        # 按索引顺序组合CPU、内存、硬盘
        for i in range(max_rows):
            row = {
                'IP地址': ip,
                '产品序列号': product_serial,
                '主板序列号': board_serial,
                '机箱序列号': chassis_serial,
                'CPU制造商': '',
                'CPU型号': '',
                'CPU序列号': '',
                '内存制造商': '',
                '内存序列号': '',
                '内存容量': '',
                '硬盘制造商': '',
                '硬盘序列号': '',
                '硬盘容量': ''
            }
            
            # 添加CPU信息
            if i < len(cpus):
                cpu = cpus[i]
                row['CPU制造商'] = cpu.get('manufacturer', 'N/A')
                row['CPU型号'] = cpu.get('type', 'N/A')
                row['CPU序列号'] = cpu.get('serial', 'N/A')
            
            # 添加内存信息
            if i < len(memory):
                mem = memory[i]
                row['内存制造商'] = mem.get('manufacturer', 'N/A')
                row['内存序列号'] = mem.get('serial', 'N/A')
                row['内存容量'] = mem.get('capacity', 'N/A')
            
            # 添加硬盘信息
            if i < len(disks):
                disk = disks[i]
                row['硬盘制造商'] = disk.get('manufacturer', 'N/A')
                row['硬盘序列号'] = disk.get('serial', 'N/A')
                row['硬盘容量'] = disk.get('capacity', 'N/A')
            
            data.append(row)
    
    df = pd.DataFrame(data)
    
    with pd.ExcelWriter(output_file, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name='硬件序列号', index=False)
        
        worksheet = writer.sheets['硬件序列号']
        
        for column in worksheet.columns:
            max_length = 0
            column_letter = column[0].column_letter
            for cell in column:
                try:
                    if len(str(cell.value)) > max_length:
                        max_length = len(str(cell.value))
                except:
                    pass
            adjusted_width = min(max_length + 2, 50)
            worksheet.column_dimensions[column_letter].width = adjusted_width


def main():
    devices_file = 'devices.txt'
    
    if not os.path.exists(devices_file):
        print(f"错误: 找不到设备配置文件 {devices_file}")
        return
    
    devices = []
    with open(devices_file, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#'):
                parts = line.split()
                if len(parts) >= 3:
                    devices.append({
                        'ip': parts[0],
                        'username': parts[1],
                        'password': parts[2]
                    })
    
    if not devices:
        print(f"错误: 设备配置文件 {devices_file} 中没有找到有效的设备信息")
        return
    
    all_results = []
    
    for device in devices:
        collector = BMCHybridCollector(
            device['ip'],
            device['username'],
            device['password']
        )
        
        result = collector.get_all_info()
        all_results.append(result)
    
    print("\n" + "="*80)
    print("采集结果汇总")
    print("="*80)
    
    for result in all_results:
        print(f"\nIP: {result['ip']}")
        print("-" * 40)
        
        fru = result['fru']
        if fru:
            print(f"产品序列号: {fru.get('product_serial', 'N/A')}")
            print(f"主板序列号: {fru.get('board_serial', 'N/A')}")
            print(f"机箱序列号: {fru.get('chassis_serial', 'N/A')}")
        
        cpus = result['cpus']
        if cpus:
            print(f"\nCPU信息 ({len(cpus)} 个):")
            for i, cpu in enumerate(cpus, 1):
                print(f"  CPU {i}: {cpu.get('manufacturer', 'N/A')} - 序列号: {cpu.get('serial', 'N/A')}")
        
        memory = result['memory']
        if memory:
            print(f"\n内存信息 ({len(memory)} 条):")
            for i, mem in enumerate(memory, 1):
                print(f"  内存 {i}: {mem.get('manufacturer', 'N/A')} - 序列号: {mem.get('serial', 'N/A')}")
        
        disks = result['disks']
        if disks:
            print(f"\n硬盘信息 ({len(disks)} 个):")
            for i, disk in enumerate(disks, 1):
                print(f"  硬盘 {i}: {disk.get('manufacturer', 'N/A')} - 序列号: {disk.get('serial', 'N/A')} - 容量: {disk.get('capacity', 'N/A')}")
        
        if not cpus and not memory and not disks:
            print("\n未获取到详细的硬件信息")
    
    print("\n" + "="*80)
    
    import datetime
    timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    
    output_json_file = f'bmc_hardware_info_{timestamp}.json'
    with open(output_json_file, 'w', encoding='utf-8') as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)
    print(f"\nJSON结果已保存到: {output_json_file}")
    
    output_excel_file = f'bmc_hardware_info_{timestamp}.xlsx'
    save_to_excel(all_results, output_excel_file)
    print(f"Excel结果已保存到: {output_excel_file}")


if __name__ == '__main__':
    main()
