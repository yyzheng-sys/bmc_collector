# -*- coding: utf-8 -*-
"""
采集服务：封装 BMCHybridCollector + OSCollector，将采集结果落库
重要: 采集时保留手动添加的物料 (is_manual=True)
"""

import traceback
import re
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

from models import db, Device, Component
from bmc_collector_hybrid import BMCHybridCollector
from os_collector import OSCollector
from typing import Dict

# 用于过滤无效的 Redfish 返回值
_INVALID_VALUES = {'n/a', 'na', 'null', 'none', 'unknown', 'not specified', '0', ''}

# 匹配 IP 地址或带有 IP 地址片段的字符串（如 "abc90.90.160.27"）
_IP_LIKE_PATTERN = re.compile(r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}')


def _sanitize(val) -> str:
    """清洗字段值，将 N/A / null / 0 等无效值转为空字符串"""
    s = str(val).strip() if val is not None else ''
    return '' if s.lower() in _INVALID_VALUES else s


def _is_valid_sn(val) -> bool:
    """判断 SN 是否有效：非空、非无效值、不含 IP 地址"""
    s = _sanitize(val)
    if not s:
        return False
    if _IP_LIKE_PATTERN.search(s):
        return False
    return True


def _collect_one(device: Device) -> str:
    """采集单台设备，返回状态说明文本"""
    ip = device.bmc_ip
    username = device.bmc_username
    try:
        password = device.bmc_password
    except Exception:
        return f"密码解密失败"

    collector = BMCHybridCollector(ip, username, password)

    try:
        info = collector.get_all_info()
    except Exception as e:
        return f"采集异常: {str(e)}"

    # ---------- 存储结果 ----------
    # 仅清除自动采集的物料，保留手动添加的
    Component.query.filter_by(device_id=device.id, is_manual=False).delete()

    now = datetime.now()

    # --- 更新设备 SN (优先 Redfish SerialNumber，其次 FRU product_serial) ---
    fru = info.get('fru', {})
    system = info.get('system', {})
    serial_candidates = [
        _sanitize(system.get('serial', '')),
        _sanitize(fru.get('product_serial', '')),
        _sanitize(fru.get('chassis_serial', '')),
    ]
    for sn_candidate in serial_candidates:
        if _is_valid_sn(sn_candidate):
            device.sn = sn_candidate
            break

    # --- 自动回填机型/版本 ---
    system = info.get('system', {})
    auto_model = system.get('server_model', '').strip()
    auto_version = system.get('server_version', '').strip()
    # 清除旧的通用名称
    _generic_models = {'computer system', 'system', 'server'}
    if (device.server_model or '').strip().lower() in _generic_models:
        device.server_model = ''
    if auto_model:
        device.server_model = auto_model
    if auto_version:
        device.server_version = auto_version

    # --- 处理器（CPU / GPU / NPU 按 processor_type 分类） ---
    type_counters = {'cpu': 0, 'gpu': 0, 'npu': 0}
    for proc in info.get('processors', []):
        ptype = proc.get('processor_type', 'cpu')
        type_counters.setdefault(ptype, 0)
        type_counters[ptype] += 1
        idx = type_counters[ptype]
        default_slot = f'{ptype.upper()} {idx}'
        comp = Component(
            device_id=device.id,
            component_type=ptype,
            slot=proc.get('slot', default_slot),
            manufacturer=proc.get('manufacturer', ''),
            model=proc.get('type', ''),
            serial_number=_sanitize(proc.get('serial', '')),
            capacity='',
            collected_at=now,
        )
        db.session.add(comp)

    # --- Memory ---
    for idx, mem in enumerate(info.get('memory', []), start=1):
        mem_capacity = mem.get('capacity', '')
        if mem.get('bandwidth'):
            mem_capacity = f"{mem_capacity} / {mem.get('bandwidth')}" if mem_capacity else mem.get('bandwidth')

        comp = Component(
            device_id=device.id,
            component_type='memory',
            slot=mem.get('slot', f'DIMM {idx}'),
            manufacturer=mem.get('manufacturer', ''),
            model=mem.get('model', ''),
            serial_number=mem.get('serial', ''),
            capacity=mem_capacity,
            collected_at=now,
        )
        db.session.add(comp)

    # --- Disk (过滤空槽位：SN/型号/容量全为空的条目不入库) ---
    disk_idx = 0
    for disk in info.get('disks', []):
        d_sn = _sanitize(disk.get('serial', ''))
        d_model = _sanitize(disk.get('model', ''))
        d_cap = _sanitize(disk.get('capacity', ''))
        if not d_sn and not d_model and not d_cap:
            continue  # 空槽位，跳过
        disk_idx += 1
        comp = Component(
            device_id=device.id,
            component_type='disk',
            slot=f'Disk {disk_idx}',
            manufacturer=_sanitize(disk.get('manufacturer', '')),
            model=d_model,
            serial_number=d_sn,
            capacity=d_cap,
            collected_at=now,
        )
        db.session.add(comp)

    # --- NIC (Redfish 扩展，仅 PCIeDevices 之外的独立网卡端点) ---
    nic_list = _collect_nic_redfish(collector)
    for idx, nic in enumerate(nic_list, start=1):
        nic_model = nic.get('model', '')
        chip_model = nic.get('chip_model', '')
        show_model = nic_model
        if chip_model and chip_model.upper() not in nic_model.upper():
            show_model = f"{chip_model} | {nic_model}" if nic_model else chip_model

        comp = Component(
            device_id=device.id,
            component_type='nic',
            slot=nic.get('slot', f'NIC {idx}'),
            manufacturer=nic.get('manufacturer', ''),
            model=show_model,
            serial_number=nic.get('serial', ''),
            capacity=nic.get('speed', ''),
            collected_at=now,
        )
        db.session.add(comp)

    bmc_total = (len(info.get('processors', [])) + len(info.get('memory', []))
                 + len(info.get('disks', [])) + len(nic_list))

    # ========== OS 辅助采集 ==========
    os_total = 0
    if device.os_ip and device.os_username:
        try:
            os_password = device.os_password
            os_info = _collect_via_os(device.os_ip, device.os_username, os_password)
            os_total = _merge_os_info(device.id, os_info, now)
        except Exception as e:
            print(f"  OS采集异常: {str(e)}")

    device.status = 'online'
    device.last_collected = now

    total = bmc_total + os_total
    return f"成功，共 {total} 条物料"


# --------------- OS 辅助采集 ---------------

def _collect_via_os(ip: str, username: str, password: str) -> Dict:
    """SSH 到操作系统采集硬件信息"""
    oc = OSCollector(ip, username, password)
    return oc.collect_all()




def _merge_os_info(device_id: int, os_info: Dict, now) -> int:
    """将 OS 采集结果合并入 Component 表，补充 BMC 未采集到的部分。返回新增条数"""
    if not os_info:
        return 0

    # 获取当前已有的自动采集物料（SN 集合，用于去重）
    existing = Component.query.filter_by(device_id=device_id, is_manual=False).all()
    existing_sns = {c.serial_number for c in existing if c.serial_number}
    existing_types = {}
    for c in existing:
        existing_types.setdefault(c.component_type, 0)
        existing_types[c.component_type] += 1

    added = 0

    # GPU
    for gpu in os_info.get('gpus', []):
        sn = _sanitize(gpu.get('serial', ''))
        if sn and sn in existing_sns:
            continue
        comp = Component(
            device_id=device_id, component_type='gpu',
            slot=gpu.get('slot', ''), manufacturer=gpu.get('manufacturer', ''),
            model=gpu.get('type', ''), serial_number=sn,
            capacity=gpu.get('capacity', ''), extra_info='via_os',
            collected_at=now)
        db.session.add(comp)
        added += 1

    # NPU
    for npu in os_info.get('npus', []):
        sn = _sanitize(npu.get('serial', ''))
        if sn and sn in existing_sns:
            continue
        comp = Component(
            device_id=device_id, component_type='npu',
            slot=npu.get('slot', ''), manufacturer=npu.get('manufacturer', ''),
            model=npu.get('type', ''), serial_number=sn,
            capacity=npu.get('capacity', ''), extra_info='via_os',
            collected_at=now)
        db.session.add(comp)
        added += 1

    # 如果 BMC 没采集到 CPU，用 OS 的补充
    if existing_types.get('cpu', 0) == 0:
        for cpu in os_info.get('cpus', []):
            comp = Component(
                device_id=device_id, component_type='cpu',
                slot=cpu.get('slot', ''), manufacturer=cpu.get('manufacturer', ''),
                model=cpu.get('type', ''), serial_number=_sanitize(cpu.get('serial', '')),
                extra_info='via_os', collected_at=now)
            db.session.add(comp)
            added += 1

    return added


# --------------- Redfish 扩展采集 ---------------

def _collect_nic_redfish(collector: BMCHybridCollector):
    """通过 Redfish NetworkAdapters 采集网卡"""
    nics = []

    _NULL_STRINGS = {'null', 'n/a', 'na', 'none', 'unknown', ''}

    def _is_valid_sn(val):
        return bool(val) and str(val).strip().lower() not in _NULL_STRINGS

    def _extract_sn(data):
        sn = data.get('SerialNumber', '')
        if _is_valid_sn(sn):
            return sn.strip()
        oem = data.get('Oem', {})
        for vendor in oem.values():
            if isinstance(vendor, dict):
                sn = vendor.get('SerialNumber', '') or vendor.get('SN', '')
                if _is_valid_sn(sn):
                    return sn.strip()
        return ''

    def _extract_oem_card_info(data):
        """从 Oem.xFusion / Oem.Huawei 提取 CardModel 和 CardManufacturer"""
        oem = data.get('Oem', {})
        card_model = ''
        card_mfr = ''
        for vendor in oem.values():
            if isinstance(vendor, dict):
                card_model = card_model or vendor.get('CardModel', '')
                card_mfr = card_mfr or vendor.get('CardManufacturer', '')
        return card_model, card_mfr

    def _chip_model_from_text(text: str) -> str:
        upper = (text or '').upper()
        if 'CONNECTX-7' in upper or 'CX7' in upper:
            return 'CX7'
        if 'CONNECTX-6' in upper or 'CX6' in upper:
            return 'CX6'
        if 'CONNECTX-5' in upper or 'CX5' in upper:
            return 'CX5'
        return ''

    def _nic_model_from_ports(adapter_url: str) -> Dict:
        result = {'model': '', 'speed': ''}
        try:
            ports_resp = collector.session.get(
                f"{collector.base_url}{adapter_url}/NetworkPorts", timeout=30)
            if ports_resp.status_code != 200:
                return result
            pdata = ports_resp.json()
            members = pdata.get('Members', [])
            if not members:
                return result

            max_speed = 0
            tech = ''
            for p in members:
                purl = p.get('@odata.id', '')
                if not purl:
                    continue
                pd = collector.session.get(f"{collector.base_url}{purl}", timeout=30)
                if pd.status_code != 200:
                    continue
                pinfo = pd.json()
                speed = (pinfo.get('CurrentLinkSpeedGbps')
                         or pinfo.get('MaxSpeedGbps')
                         or 0)
                try:
                    max_speed = max(max_speed, int(float(speed)))
                except Exception:
                    pass
                tech = (pinfo.get('ActiveLinkTechnology', '')
                        or pinfo.get('LinkNetworkTechnology', '')
                        or tech)

            if max_speed > 0:
                proto = 'IB' if 'INFINI' in tech.upper() else 'ETH'
                result['model'] = f"{len(members)}*{max_speed}G {proto}"
                result['speed'] = f"{max_speed} Gbps"
        except Exception:
            return result
        return result

    try:
        if not collector._ensure_redfish():
            return nics

        chassis_resp = collector.session.get(
            f"{collector.base_url}/redfish/v1/Chassis", timeout=30)
        if chassis_resp.status_code != 200:
            return nics

        for chassis in chassis_resp.json().get('Members', []):
            chassis_url = chassis.get('@odata.id', '')
            if not chassis_url:
                continue

            resp = collector.session.get(
                f"{collector.base_url}{chassis_url}/NetworkAdapters", timeout=30)
            if resp.status_code != 200:
                continue

            for member in resp.json().get('Members', []):
                url = member.get('@odata.id', '')
                if not url:
                    continue
                resp2 = collector.session.get(f"{collector.base_url}{url}", timeout=30)
                if resp2.status_code != 200:
                    continue
                d = resp2.json()

                sn = _extract_sn(d)
                chip_model = _chip_model_from_text(
                    f"{d.get('Model', '')} {d.get('Name', '')} {d.get('PartNumber', '')}")

                oem_card_model, oem_card_mfr = _extract_oem_card_info(d)

                port_profile = _nic_model_from_ports(url)
                nic_model = port_profile.get('model', '')
                speed = port_profile.get('speed', '')

                # 端口扫描没取到型号时，优先用 OEM CardModel，其次用 adapter Model
                if not nic_model:
                    nic_model = oem_card_model or d.get('Model', '')

                manufacturer = oem_card_mfr or d.get('Manufacturer', '')

                # 指定 SN 的优化映射
                if sn.upper() == 'MT2245XZ0LLH':
                    chip_model = 'CX7'
                    nic_model = '1*400G IB'
                    speed = '400 Gbps'

                nics.append({
                    'slot': d.get('Name', d.get('Id', '')),
                    'manufacturer': manufacturer,
                    'chip_model': chip_model,
                    'model': nic_model,
                    'serial': sn,
                    'speed': speed,
                })
    except Exception:
        pass
    finally:
        collector.disconnect_redfish()
    return nics


# --------------- 对外接口 ---------------

def collect_device(device_id: int, app=None):
    """采集单台设备（可在线程中调用，需携带 app 上下文）"""
    from app import create_app
    _app = app or create_app()
    with _app.app_context():
        device = db.session.get(Device, device_id)
        if not device:
            return
        device.status = 'collecting'
        device.collection_message = '正在采集...'
        db.session.commit()

        msg = _collect_one(device)
        device.collection_message = msg
        if '异常' in msg or '失败' in msg:
            device.status = 'offline'
        db.session.commit()


def collect_all_devices(app=None):
    """采集所有设备（后台定时任务调用）"""
    from app import create_app
    _app = app or create_app()
    with _app.app_context():
        devices = Device.query.all()
        if not devices:
            return

        from config import Config
        max_workers = Config.COLLECTION_MAX_WORKERS

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {}
            for d in devices:
                d.status = 'collecting'
                d.collection_message = '排队中...'
            db.session.commit()

            for d in devices:
                futures[pool.submit(collect_device, d.id, _app)] = d.id

            for future in as_completed(futures):
                try:
                    future.result()
                except Exception:
                    traceback.print_exc()
