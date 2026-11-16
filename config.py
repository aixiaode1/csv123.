#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
全局路径配置：**改这一个文件，所有脚本一起生效**（export_all / export_radar / export_imu / tools/*）。

每项都支持环境变量覆盖，方便在多套数据/多台机器间切换：
    PAC_CSV_PCAP       默认抓包文件
    PAC_CSV_ALL_DIR    ① 全量导出目录
    PAC_CSV_LIDAR_DIR  ② 雷达输出目录
    PAC_CSV_IMU_DIR    ③ IMU 输出目录
    PAC_CSV_PHOTO_DIR  图片输出目录

用法：
    直接改下面的默认值；或
    set PAC_CSV_PCAP=E:\\lidardata\\99999.pcapng
"""

from __future__ import annotations

import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))     # 项目根目录

# ---------------- 输入 ----------------
DEFAULT_PCAP = os.environ.get("PAC_CSV_PCAP") or r"E:\lidardata\yyy.pcapng"

# ---------------- 输出 ----------------
ALL_DIR = os.environ.get("PAC_CSV_ALL_DIR") or os.path.join(BASE_DIR, "all")
LIDAR_DIR = os.environ.get("PAC_CSV_LIDAR_DIR") or os.path.join(BASE_DIR, "LIDAR")
IMU_DIR = os.environ.get("PAC_CSV_IMU_DIR") or os.path.join(BASE_DIR, "IMU")
PHOTO_DIR = os.environ.get("PAC_CSV_PHOTO_DIR") or os.path.join(BASE_DIR, "photo")

# 旧的汇总输出目录，仅作 *_all.csv 查找的兼容回退
LEGACY_OUTPUT_DIR = r"E:\dsh-output"


def describe():
    """打印当前生效的路径，便于排查“改了没生效”。"""
    return "\n".join([
        "DEFAULT_PCAP = %s" % DEFAULT_PCAP,
        "ALL_DIR      = %s" % ALL_DIR,
        "LIDAR_DIR    = %s" % LIDAR_DIR,
        "IMU_DIR      = %s" % IMU_DIR,
        "PHOTO_DIR    = %s" % PHOTO_DIR,
    ])


if __name__ == "__main__":
    print(describe())
