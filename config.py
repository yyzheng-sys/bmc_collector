# -*- coding: utf-8 -*-
import os
import secrets

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or secrets.token_hex(32)
    SQLALCHEMY_DATABASE_URI = f'sqlite:///{os.path.join(BASE_DIR, "bmc_platform.db")}'
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # 采集配置
    COLLECTION_INTERVAL_HOURS = 6       # 自动采集间隔(小时)
    COLLECTION_TIMEOUT = 60             # 单台设备采集超时(秒)
    COLLECTION_MAX_WORKERS = 5          # 并发采集线程数

    # 加密密钥文件
    ENCRYPTION_KEY_FILE = os.path.join(BASE_DIR, '.encryption_key')
