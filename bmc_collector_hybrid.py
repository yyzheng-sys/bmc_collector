#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import paramiko
import requests
import urllib3
import os
import json
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
                if self.auth_token:
                    self.session.headers.update({'X-Auth-Token': self.auth_token})
                    return True
            
            return False
        except Exception as e:
            return False
    
    def disconnect_redfish(self):
        if self.session and self.auth_token:
            try:
                self.session.delete(f"{self.base_url}/redfish/v1/SessionService/Sessions", timeout=10)
            except:
                pass
            self.session.close()
            self.session = None
            self.auth_token = None
    
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
    
    def get_cpu_info(self) -> List[Dict]:
        cpu_list = []
        
        if not self.connect_redfish():
            return cpu_list
        
        try:
            response = self.session.get(f"{self.base_url}/redfish/v1/Systems", timeout=30)
            if response.status_code == 200:
                data = response.json()
                if 'Members' in data and len(data['Members']) > 0:
                    system_url = data['Members'][0]['@odata.id']
                    response = self.session.get(f"{self.base_url}{system_url}/Processors", timeout=30)
                    if response.status_code == 200:
                        processors_data = response.json()
                        if 'Members' in processors_data:
                            for member in processors_data['Members']:
                                processor_url = member['@odata.id']
                                response = self.session.get(f"{self.base_url}{processor_url}", timeout=30)
                                if response.status_code == 200:
                                    processor_data = response.json()
                                    cpu = {}
                                    if 'Manufacturer' in processor_data:
                                        cpu['manufacturer'] = processor_data['Manufacturer']
                                    if 'SerialNumber' in processor_data:
                                        cpu['serial'] = processor_data['SerialNumber']
                                    if 'Model' in processor_data:
                                        cpu['type'] = processor_data['Model']
                                    if cpu:
                                        cpu_list.append(cpu)
        except Exception as e:
            print(f"  获取CPU信息异常: {str(e)}")
        
        self.disconnect_redfish()
        return cpu_list
    
    def get_memory_info(self) -> List[Dict]:
        memory_list = []
        
        if not self.connect_redfish():
            return memory_list
        
        try:
            response = self.session.get(f"{self.base_url}/redfish/v1/Systems", timeout=30)
            if response.status_code == 200:
                data = response.json()
                if 'Members' in data and len(data['Members']) > 0:
                    system_url = data['Members'][0]['@odata.id']
                    response = self.session.get(f"{self.base_url}{system_url}/Memory", timeout=30)
                    if response.status_code == 200:
                        memory_data = response.json()
                        if 'Members' in memory_data:
                            for member in memory_data['Members']:
                                memory_url = member['@odata.id']
                                response = self.session.get(f"{self.base_url}{memory_url}", timeout=30)
                                if response.status_code == 200:
                                    mem_data = response.json()
                                    mem = {}
                                    if 'Manufacturer' in mem_data:
                                        mem['manufacturer'] = mem_data['Manufacturer']
                                    if 'SerialNumber' in mem_data:
                                        mem['serial'] = mem_data['SerialNumber']
                                    if 'CapacityMiB' in mem_data:
                                        mem['capacity'] = f"{mem_data['CapacityMiB']} MB"
                                    if mem:
                                        memory_list.append(mem)
        except Exception as e:
            print(f"  获取内存信息异常: {str(e)}")
        
        self.disconnect_redfish()
        return memory_list
    
    def get_disk_info(self) -> List[Dict]:
        disk_list = []
        
        if not self.connect_ssh():
            return disk_list
        
        try:
            output = self.run_ssh_command('ipmcget -t storage -d pdinfo -v all')
            if output:
                disk_list = self._parse_disks(output)
        except Exception as e:
            print(f"  获取硬盘信息异常: {str(e)}")
        
        self.disconnect_ssh()
        return disk_list
    
    def _parse_disks(self, output: str) -> List[Dict]:
        disks = []
        current_disk = {}
        
        for line in output.split('\n'):
            line = line.strip()
            if 'ID' in line and ':' in line and 'Device Name' not in line:
                if current_disk and 'serial' in current_disk:
                    disks.append(current_disk)
                    current_disk = {}
            elif 'Serial Number' in line:
                parts = line.split(':', 1)
                if len(parts) == 2:
                    current_disk['serial'] = parts[1].strip()
            elif 'Manufacturer' in line:
                parts = line.split(':', 1)
                if len(parts) == 2:
                    current_disk['manufacturer'] = parts[1].strip()
            elif 'Capacity' in line:
                parts = line.split(':', 1)
                if len(parts) == 2:
                    current_disk['capacity'] = parts[1].strip()
        
        if current_disk and 'serial' in current_disk:
            disks.append(current_disk)
        
        return disks
    
    def get_all_info(self) -> Dict:
        print(f"\n正在采集 {self.ip} 的信息...")
        
        info = {
            'ip': self.ip,
            'fru': self.get_fru_info(),
            'cpus': self.get_cpu_info(),
            'memory': self.get_memory_info(),
            'disks': self.get_disk_info()
        }
        
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
