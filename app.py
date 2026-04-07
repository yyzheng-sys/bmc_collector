#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
机房资产管理平台 — Flask 主应用
"""

import io
import threading
from datetime import datetime

import pandas as pd
from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask, jsonify, render_template, request, send_file

from config import Config
from models import Component, Device, db


def create_app():
    app = Flask(__name__, template_folder='templates', static_folder='static')
    app.config.from_object(Config)
    db.init_app(app)

    with app.app_context():
        db.create_all()
        _migrate_db()

    return app


def _column_exists(table_name: str, column_name: str) -> bool:
    sql = f"PRAGMA table_info({table_name})"
    rows = db.session.execute(db.text(sql)).fetchall()
    return any(row[1] == column_name for row in rows)


def _migrate_db():
    """轻量迁移: 为旧库补充新字段"""
    if not _column_exists('device', 'asset_status'):
        db.session.execute(db.text(
            "ALTER TABLE device ADD COLUMN asset_status VARCHAR(20) DEFAULT ''"
        ))
    if not _column_exists('component', 'is_manual'):
        db.session.execute(db.text(
            "ALTER TABLE component ADD COLUMN is_manual BOOLEAN DEFAULT 0"
        ))
    db.session.commit()


app = create_app()


@app.route('/')
def index():
    return render_template('index.html')


# ======================== 设备 API ========================

@app.route('/api/devices', methods=['GET'])
def list_devices():
    keyword = request.args.get('keyword', '').strip()

    query = Device.query
    matched_map = {}

    if keyword:
        like = f'%{keyword}%'

        matched_components = (
            db.session.query(Component, Device.id)
            .join(Device, Component.device_id == Device.id)
            .filter(
                db.or_(
                    Component.serial_number.ilike(like),
                    Component.model.ilike(like),
                    Component.slot.ilike(like),
                )
            )
            .all()
        )

        matched_device_ids = set()
        for comp, did in matched_components:
            matched_device_ids.add(did)
            matched_map.setdefault(did, []).append({
                'id': comp.id,
                'component_type': comp.component_type,
                'type_label': comp.type_label,
                'slot': comp.slot,
                'model': comp.model,
                'serial_number': comp.serial_number,
                'is_manual': bool(comp.is_manual),
            })

        query = query.filter(
            db.or_(
                Device.server_model.ilike(like),
                Device.server_version.ilike(like),
                Device.asset_code.ilike(like),
                Device.sn.ilike(like),
                Device.bmc_ip.ilike(like),
                Device.os_ip.ilike(like),
                Device.bmc_username.ilike(like),
                Device.os_username.ilike(like),
                Device.asset_status.ilike(like),
                Device.id.in_(matched_device_ids) if matched_device_ids else db.text('0'),
            )
        )

    devices = query.order_by(Device.id.asc()).all()
    result = []
    for d in devices:
        item = d.to_dict()
        if keyword and d.id in matched_map:
            item['matched_components'] = matched_map[d.id]
        result.append(item)
    return jsonify(result)


@app.route('/api/devices', methods=['POST'])
def add_device():
    data = request.get_json(force=True)

    bmc_ip = data.get('bmc_ip', '').strip()
    if not bmc_ip:
        return jsonify({'error': 'BMC IP 不能为空'}), 400

    exists = Device.query.filter_by(bmc_ip=bmc_ip).first()
    if exists:
        return jsonify({'error': f'BMC IP {bmc_ip} 已存在'}), 409

    device = Device(
        server_model=data.get('server_model', '').strip(),
        server_version=data.get('server_version', '').strip(),
        asset_code=data.get('asset_code', '').strip(),
        sn=data.get('sn', '').strip(),
        bmc_ip=bmc_ip,
        bmc_username=data.get('bmc_username', 'Administrator').strip(),
        bmc_password_enc='',
        os_ip=data.get('os_ip', '').strip(),
        os_username=data.get('os_username', '').strip(),
        os_password_enc='',
        asset_status=data.get('asset_status', '').strip(),
    )

    device.bmc_password = data.get('bmc_password', '')
    if data.get('os_password'):
        device.os_password = data.get('os_password', '')

    db.session.add(device)
    db.session.commit()
    return jsonify(device.to_dict()), 201


@app.route('/api/devices/<int:device_id>', methods=['PUT'])
def update_device(device_id):
    device = db.session.get(Device, device_id)
    if not device:
        return jsonify({'error': '设备不存在'}), 404

    data = request.get_json(force=True)

    new_bmc_ip = data.get('bmc_ip', '').strip()
    if new_bmc_ip and new_bmc_ip != device.bmc_ip:
        dup = Device.query.filter_by(bmc_ip=new_bmc_ip).first()
        if dup:
            return jsonify({'error': f'BMC IP {new_bmc_ip} 已被占用'}), 409
        device.bmc_ip = new_bmc_ip

    device.server_model = data.get('server_model', device.server_model).strip()
    device.server_version = data.get('server_version', device.server_version).strip()
    device.asset_code = data.get('asset_code', device.asset_code).strip()
    device.sn = data.get('sn', device.sn).strip()
    device.bmc_username = data.get('bmc_username', device.bmc_username).strip()
    device.os_ip = data.get('os_ip', device.os_ip).strip()
    device.os_username = data.get('os_username', device.os_username).strip()
    device.asset_status = data.get('asset_status', device.asset_status or '').strip()

    if data.get('bmc_password'):
        device.bmc_password = data.get('bmc_password', '')
    if data.get('os_password'):
        device.os_password = data.get('os_password', '')

    db.session.commit()
    return jsonify(device.to_dict())


@app.route('/api/devices/<int:device_id>/asset-status', methods=['PATCH'])
def update_asset_status(device_id):
    device = db.session.get(Device, device_id)
    if not device:
        return jsonify({'error': '设备不存在'}), 404

    data = request.get_json(force=True)
    status = data.get('asset_status', '').strip()
    allowed = {'', '整机挂账', '整机转散件', '散件挂账'}
    if status not in allowed:
        return jsonify({'error': 'asset_status 非法'}), 400

    device.asset_status = status
    db.session.commit()
    return jsonify({'message': '更新成功', 'device': device.to_dict()})


@app.route('/api/devices/<int:device_id>', methods=['DELETE'])
def delete_device(device_id):
    device = db.session.get(Device, device_id)
    if not device:
        return jsonify({'error': '设备不存在'}), 404
    db.session.delete(device)
    db.session.commit()
    return jsonify({'message': '删除成功'})


@app.route('/api/devices/batch', methods=['POST'])
def batch_add_devices():
    data_list = request.get_json(force=True)
    if not isinstance(data_list, list):
        return jsonify({'error': '请提供设备列表'}), 400

    added = 0
    skipped = 0

    for data in data_list:
        if not isinstance(data, dict):
            skipped += 1
            continue

        bmc_ip = data.get('bmc_ip', '').strip()
        if not bmc_ip or Device.query.filter_by(bmc_ip=bmc_ip).first():
            skipped += 1
            continue

        device = Device(
            server_model=data.get('server_model', '').strip(),
            server_version=data.get('server_version', '').strip(),
            asset_code=data.get('asset_code', '').strip(),
            sn=data.get('sn', '').strip(),
            bmc_ip=bmc_ip,
            bmc_username=data.get('bmc_username', 'Administrator').strip(),
            bmc_password_enc='',
            os_ip=data.get('os_ip', '').strip(),
            os_username=data.get('os_username', '').strip(),
            os_password_enc='',
            asset_status=data.get('asset_status', '').strip(),
        )
        device.bmc_password = data.get('bmc_password', '')
        if data.get('os_password'):
            device.os_password = data.get('os_password', '')

        db.session.add(device)
        added += 1

    db.session.commit()
    return jsonify({'added': added, 'skipped': skipped})


@app.route('/api/devices/batch-delete', methods=['POST'])
def batch_delete_devices():
    data = request.get_json(force=True)
    ids = data.get('ids', []) if isinstance(data, dict) else []
    if not isinstance(ids, list) or not ids:
        return jsonify({'error': '请提供待删除设备ID列表'}), 400

    int_ids = []
    for item in ids:
        try:
            int_ids.append(int(item))
        except Exception:
            continue

    if not int_ids:
        return jsonify({'error': '无有效ID'}), 400

    devices = Device.query.filter(Device.id.in_(int_ids)).all()
    deleted = len(devices)

    for d in devices:
        db.session.delete(d)
    db.session.commit()

    return jsonify({'message': '批量删除完成', 'deleted': deleted})


# ======================== 物料 API ========================

@app.route('/api/devices/<int:device_id>/components', methods=['GET'])
def get_device_components(device_id):
    device = db.session.get(Device, device_id)
    if not device:
        return jsonify({'error': '设备不存在'}), 404

    comps = Component.query.filter_by(device_id=device_id).order_by(
        Component.component_type.asc(),
        Component.slot.asc(),
        Component.id.asc(),
    ).all()

    return jsonify({
        'device': device.to_dict(),
        'components': [c.to_dict() for c in comps],
    })


@app.route('/api/devices/<int:device_id>/components', methods=['POST'])
def add_manual_component(device_id):
    device = db.session.get(Device, device_id)
    if not device:
        return jsonify({'error': '设备不存在'}), 404

    data = request.get_json(force=True)
    ctype = data.get('component_type', '').strip().lower()
    allowed_types = {'cpu', 'gpu', 'npu', 'memory', 'nic', 'disk'}
    if ctype not in allowed_types:
        return jsonify({'error': 'component_type 非法'}), 400

    comp = Component(
        device_id=device_id,
        component_type=ctype,
        slot=data.get('slot', '').strip(),
        manufacturer=data.get('manufacturer', '').strip(),
        model=data.get('model', '').strip(),
        serial_number=data.get('serial_number', '').strip(),
        capacity=data.get('capacity', '').strip(),
        extra_info=data.get('extra_info', '').strip(),
        is_manual=True,
        collected_at=datetime.now(),
    )
    db.session.add(comp)
    db.session.commit()

    return jsonify(comp.to_dict()), 201


@app.route('/api/components/<int:component_id>', methods=['PUT'])
def update_component(component_id):
    comp = db.session.get(Component, component_id)
    if not comp:
        return jsonify({'error': '物料不存在'}), 404

    data = request.get_json(force=True)

    ctype = data.get('component_type', comp.component_type).strip().lower()
    allowed_types = {'cpu', 'gpu', 'npu', 'memory', 'nic', 'disk'}
    if ctype not in allowed_types:
        return jsonify({'error': 'component_type 非法'}), 400

    comp.component_type = ctype
    comp.slot = data.get('slot', comp.slot).strip()
    comp.manufacturer = data.get('manufacturer', comp.manufacturer).strip()
    comp.model = data.get('model', comp.model).strip()
    comp.serial_number = data.get('serial_number', comp.serial_number).strip()
    comp.capacity = data.get('capacity', comp.capacity).strip()
    comp.extra_info = data.get('extra_info', comp.extra_info or '').strip()

    if 'is_manual' in data:
        comp.is_manual = bool(data.get('is_manual'))

    db.session.commit()
    return jsonify(comp.to_dict())


@app.route('/api/components/<int:component_id>', methods=['DELETE'])
def delete_component(component_id):
    comp = db.session.get(Component, component_id)
    if not comp:
        return jsonify({'error': '物料不存在'}), 404

    db.session.delete(comp)
    db.session.commit()
    return jsonify({'message': '物料已删除'})


# ======================== 采集 API ========================

@app.route('/api/collect/<int:device_id>', methods=['POST'])
def collect_single(device_id):
    device = db.session.get(Device, device_id)
    if not device:
        return jsonify({'error': '设备不存在'}), 404

    from collector_service import collect_device
    thread = threading.Thread(target=collect_device, args=(device_id, app), daemon=True)
    thread.start()

    return jsonify({'message': f'已开始采集 {device.bmc_ip}'})


@app.route('/api/collect/all', methods=['POST'])
def collect_all():
    from collector_service import collect_all_devices
    thread = threading.Thread(target=collect_all_devices, args=(app,), daemon=True)
    thread.start()

    return jsonify({'message': '已开始全量采集'})


# ======================== 导出 API ========================

@app.route('/api/export/devices', methods=['GET'])
def export_devices():
    devices = Device.query.order_by(Device.id).all()

    rows = []
    for d in devices:
        rows.append({
            '序号': d.id,
            '服务器机型': d.server_model,
            '服务器版本': d.server_version,
            '资产编码': d.asset_code,
            '设备SN': d.sn,
            '整机状态': d.asset_status or '',
            'BMC IP': d.bmc_ip,
            'BMC账号': d.bmc_username,
            'BMC密码': d.bmc_password,
            'OS IP': d.os_ip,
            'OS账号': d.os_username,
            'OS密码': d.os_password,
            '状态': d.status,
            '物料数量': d.components.count(),
            '上次采集': d.last_collected.strftime('%Y-%m-%d %H:%M:%S') if d.last_collected else '',
        })

    return _export_excel(rows, '设备列表', f"设备列表_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx")


@app.route('/api/export/components', methods=['GET'])
def export_components():
    comps = (
        db.session.query(Component, Device)
        .join(Device, Component.device_id == Device.id)
        .order_by(Device.id, Component.component_type, Component.slot)
        .all()
    )

    rows = []
    for comp, dev in comps:
        rows.append({
            '设备ID': dev.id,
            '服务器机型': dev.server_model,
            '服务器版本': dev.server_version,
            '资产编码': dev.asset_code,
            '设备SN': dev.sn,
            'BMC IP': dev.bmc_ip,
            '物料类型': comp.type_label,
            '槽位': comp.slot,
            '制造商': comp.manufacturer,
            '型号': comp.model,
            'SN(序列号)': comp.serial_number,
            '容量/大小': comp.capacity,
            '来源': '手动' if comp.is_manual else '自动',
            '采集时间': comp.collected_at.strftime('%Y-%m-%d %H:%M:%S') if comp.collected_at else '',
        })

    return _export_excel(rows, '物料明细', f"物料明细_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx")


@app.route('/api/export/device/<int:device_id>', methods=['GET'])
def export_single_device(device_id):
    device = db.session.get(Device, device_id)
    if not device:
        return jsonify({'error': '设备不存在'}), 404

    comps = Component.query.filter_by(device_id=device_id).order_by(
        Component.component_type, Component.slot).all()

    rows = []
    for comp in comps:
        rows.append({
            '物料类型': comp.type_label,
            '槽位': comp.slot,
            '制造商': comp.manufacturer,
            '型号': comp.model,
            'SN(序列号)': comp.serial_number,
            '容量/大小': comp.capacity,
            '来源': '手动' if comp.is_manual else '自动',
            '采集时间': comp.collected_at.strftime('%Y-%m-%d %H:%M:%S') if comp.collected_at else '',
        })

    sheet_name = device.bmc_ip if device.bmc_ip else 'device'
    return _export_excel(rows, sheet_name, f"{sheet_name}_物料_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx")


def _export_excel(rows, sheet_name, filename):
    df = pd.DataFrame(rows)
    buffer = io.BytesIO()

    with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name=sheet_name, index=False)
        _auto_width(writer.sheets[sheet_name])

    buffer.seek(0)
    return send_file(
        buffer,
        as_attachment=True,
        download_name=filename,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )


def _auto_width(ws):
    for col in ws.columns:
        max_len = 0
        col_letter = col[0].column_letter
        for cell in col:
            try:
                if cell.value:
                    max_len = max(max_len, len(str(cell.value)))
            except Exception:
                pass
        ws.column_dimensions[col_letter].width = min(max_len + 4, 60)


# ======================== 定时任务 ========================

scheduler = BackgroundScheduler(daemon=True)


def _scheduled_collect():
    from collector_service import collect_all_devices
    collect_all_devices(app)


scheduler.add_job(
    _scheduled_collect,
    'interval',
    hours=Config.COLLECTION_INTERVAL_HOURS,
    id='auto_collect',
    replace_existing=True,
    next_run_time=None,
)
scheduler.start()


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='机房资产管理平台')
    parser.add_argument('-p', '--port', type=int, default=Config.SERVER_PORT,
                        help=f'服务端口 (默认: {Config.SERVER_PORT}，也可通过环境变量 BMC_PORT 设置)')
    args = parser.parse_args()
    app.run(host='0.0.0.0', port=args.port, debug=True)
