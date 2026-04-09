# -*- coding: utf-8 -*-
import os
from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
from cryptography.fernet import Fernet

db = SQLAlchemy()

# --------------- 密码加密/解密 ---------------
_cipher = None


def _get_cipher():
    global _cipher
    if _cipher:
        return _cipher
    from config import Config
    key_file = Config.ENCRYPTION_KEY_FILE
    if os.path.exists(key_file):
        with open(key_file, 'rb') as f:
            key = f.read()
    else:
        key = Fernet.generate_key()
        with open(key_file, 'wb') as f:
            f.write(key)
    _cipher = Fernet(key)
    return _cipher


def encrypt_password(password: str) -> str:
    return _get_cipher().encrypt(password.encode('utf-8')).decode('utf-8')


def decrypt_password(encrypted: str) -> str:
    return _get_cipher().decrypt(encrypted.encode('utf-8')).decode('utf-8')


# --------------- 数据模型 ---------------
class Device(db.Model):
    __tablename__ = 'device'

    id = db.Column(db.Integer, primary_key=True)
    server_model = db.Column(db.String(100), default='')          # 服务器机型
    server_version = db.Column(db.String(50), default='')         # 服务器版本
    asset_code = db.Column(db.String(100), default='')            # 资产编码
    sn = db.Column(db.String(200), default='')                    # SN (产品序列号)
    bmc_ip = db.Column(db.String(50), unique=True, nullable=False)
    bmc_username = db.Column(db.String(100), nullable=False)
    bmc_password_enc = db.Column(db.Text, nullable=False)
    os_ip = db.Column(db.String(50), default='')
    os_username = db.Column(db.String(100), default='')
    os_password_enc = db.Column(db.Text, default='')
    asset_status = db.Column(db.String(20), default='')           # 整机挂账 / 整机转散件 / 散件挂账
    asset_description = db.Column(db.Text, default='')            # 整机资产描述（整机挂账时维护）
    status = db.Column(db.String(20), default='unknown')          # online / offline / collecting / unknown
    last_collected = db.Column(db.DateTime)
    collection_message = db.Column(db.Text, default='')
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

    components = db.relationship('Component', backref='device', lazy='dynamic',
                                 cascade='all, delete-orphan')

    # 便捷属性
    @property
    def bmc_password(self):
        try:
            return decrypt_password(self.bmc_password_enc)
        except Exception:
            return ''

    @bmc_password.setter
    def bmc_password(self, value):
        self.bmc_password_enc = encrypt_password(value)

    @property
    def os_password(self):
        if not self.os_password_enc:
            return ''
        try:
            return decrypt_password(self.os_password_enc)
        except Exception:
            return ''

    @os_password.setter
    def os_password(self, value):
        self.os_password_enc = encrypt_password(value) if value else ''

    def to_dict(self):
        return {
            'id': self.id,
            'server_model': self.server_model,
            'server_version': self.server_version,
            'asset_code': self.asset_code,
            'sn': self.sn,
            'bmc_ip': self.bmc_ip,
            'bmc_username': self.bmc_username,
            'bmc_password': self.bmc_password,
            'os_ip': self.os_ip,
            'os_username': self.os_username,
            'os_password': self.os_password,
            'asset_status': self.asset_status or '',
            'asset_description': self.asset_description or '',
            'status': self.status,
            'last_collected': self.last_collected.strftime('%Y-%m-%d %H:%M:%S') if self.last_collected else '',
            'collection_message': self.collection_message or '',
            'component_count': self.components.count(),
            'created_at': self.created_at.strftime('%Y-%m-%d %H:%M:%S') if self.created_at else '',
        }


class Component(db.Model):
    __tablename__ = 'component'

    id = db.Column(db.Integer, primary_key=True)
    device_id = db.Column(db.Integer, db.ForeignKey('device.id'), nullable=False)
    component_type = db.Column(db.String(20), nullable=False)     # cpu / gpu / npu / memory / nic / disk
    slot = db.Column(db.String(100), default='')                  # 槽位
    manufacturer = db.Column(db.String(200), default='')          # 制造商
    model = db.Column(db.String(300), default='')                 # 型号
    serial_number = db.Column(db.String(200), default='')         # SN
    capacity = db.Column(db.String(100), default='')              # 容量/大小
    extra_info = db.Column(db.Text, default='')                   # JSON 扩展
    is_manual = db.Column(db.Boolean, default=False)              # 手动添加标识
    collected_at = db.Column(db.DateTime, default=datetime.now)

    TYPE_LABELS = {
        'cpu': 'CPU',
        'gpu': 'GPU',
        'npu': 'NPU',
        'memory': '内存',
        'nic': '网卡',
        'disk': '硬盘',
    }

    @property
    def type_label(self):
        return self.TYPE_LABELS.get(self.component_type, self.component_type)

    def to_dict(self):
        return {
            'id': self.id,
            'device_id': self.device_id,
            'component_type': self.component_type,
            'type_label': self.type_label,
            'slot': self.slot,
            'manufacturer': self.manufacturer,
            'model': self.model,
            'serial_number': self.serial_number,
            'capacity': self.capacity,
            'extra_info': self.extra_info,
            'is_manual': self.is_manual or False,
            'collected_at': self.collected_at.strftime('%Y-%m-%d %H:%M:%S') if self.collected_at else '',
        }
