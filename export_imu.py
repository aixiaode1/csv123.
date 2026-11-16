#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
【第 3 步】从 export_all.py 的产物里导出**IMU 数据**，列格式对齐 WaterScenes。

输入：E:\\dsh-output\\<抓包名>_all.csv  （export_all.py 生成，只读不写）
输出：<项目目录>\\IMU\\<抓包名>_imu.csv   （默认 E:\\pycharm project\\pacp——csv\\IMU）

输出列（与 WaterScenes_Samples/imu/*.csv 完全一致）：
    timestamp, pitch, roll, yaw,
    angular_velocity_x, angular_velocity_y, angular_velocity_z,
    linear_acceleration_x, linear_acceleration_y, linear_acceleration_z,
    magnetic_field_x, magnetic_field_y, magnetic_field_z

IMU 载荷结构（60 B = 36 B 头 + 6 × float32）：
    [1:3] 长度=60  [7:9] 包序号  [28:36] 设备时间戳(ns)
    [36:48] 角速度 x/y/z（rad/s）
    [48:60] 加速度 x/y/z（g）—— 输出前换算成 m/s²，和 WaterScenes 一致

说明：
    pitch / roll 由重力方向反解（静止时可用）；yaw 需要磁力计，本设备没有 → 留空
    magnetic_field 三列同样留空（无磁力计）

用法：
    python export_imu.py                                # 读最新的 *_all.csv
    python export_imu.py -i "E:\\dsh-output\\6666_all.csv"
    python export_imu.py --gyro-deg                     # 角速度输出 deg/s
    python export_imu.py --flat-attitude                # 不反解 pitch/roll，全部留空
"""

from __future__ import annotations

import argparse
import collections
import csv
import math
import os
import struct
import sys
import statistics
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from udp_common import default_all_csv, iter_all_rows  # noqa: E402

HEADER_LEN = 36
IMU_LEN = 60
G0 = 9.80665                      # g -> m/s²
IMU_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "IMU")   # IMU 专用输出目录
FIELDS = ["timestamp", "pitch", "roll", "yaw",
          "angular_velocity_x", "angular_velocity_y", "angular_velocity_z",
          "linear_acceleration_x", "linear_acceleration_y", "linear_acceleration_z",
          "magnetic_field_x", "magnetic_field_y", "magnetic_field_z"]


def is_imu_packet(payload):
    return len(payload) == IMU_LEN and payload[0] == 0x00 \
        and struct.unpack("<H", payload[1:3])[0] == IMU_LEN


def convert(all_csv, out_csv, gyro_deg=False, flat_attitude=False, max_rows=0, verbose=True):
    t0 = time.perf_counter()
    rows = 0
    acc = {k: [] for k in range(6)}
    seen = collections.Counter()
    t_first = t_last = None

    os.makedirs(os.path.dirname(out_csv) or ".", exist_ok=True)
    with open(out_csv, "w", newline="", encoding="utf-8-sig") as dst:
        writer = csv.writer(dst)
        writer.writerow(FIELDS)

        for rec in iter_all_rows(all_csv):
            payload = bytes.fromhex(rec.get("PayloadHex", ""))
            if not is_imu_packet(payload):
                seen["%d B" % len(payload)] += 1
                continue

            ts = float(rec["EpochTime"])
            gx, gy, gz, ax, ay, az = struct.unpack("<6f", payload[HEADER_LEN:IMU_LEN])
            for k, v in enumerate((gx, gy, gz, ax, ay, az)):
                acc[k].append(v)

            if gyro_deg:
                gx, gy, gz = (math.degrees(v) for v in (gx, gy, gz))
            # g -> m/s²，与 WaterScenes 的 9.84 量级一致
            lax, lay, laz = ax * G0, ay * G0, az * G0

            if flat_attitude:
                pitch = roll = ""
            else:
                # 由重力向量反解姿态（度）；yaw 需要磁力计，留空
                pitch = "%.6f" % math.degrees(math.atan2(-lax, math.hypot(lay, laz)))
                roll = "%.6f" % math.degrees(math.atan2(lay, laz))

            writer.writerow([
                "%.6f" % ts, pitch, roll, "",
                "%.6f" % gx, "%.6f" % gy, "%.6f" % gz,
                "%.6f" % lax, "%.6f" % lay, "%.6f" % laz,
                "", "", "",
            ])
            rows += 1
            if t_first is None:
                t_first = ts
            t_last = ts
            if max_rows and rows >= max_rows:
                break

    if verbose:
        dur = (t_last - t_first) if (t_first and t_last) else 0
        print("IMU 导出完成：%d 条，耗时 %.1f 秒" % (rows, time.perf_counter() - t0))
        print("  输出: %s (%.0f KB)" % (out_csv, os.path.getsize(out_csv) / 1024))
        if rows == 0:
            print("  提示：这份数据里没有 60 字节的 IMU 包。见到的载荷长度分布：%s"
                  % ", ".join("%s×%d" % kv for kv in seen.most_common(5)))
            print("        有些设备把 IMU 放在别的端口/长度，可用 export_all.py 的流统计确认后再改 is_imu_packet()")
        if dur > 0:
            print("  时长 %.2f 秒，采样率约 %.0f Hz" % (dur, (rows - 1) / dur))
        if rows:
            names = ["角速度x", "角速度y", "角速度z", "加速度x", "加速度y", "加速度z"]
            for k, name in enumerate(names):
                col = acc[k]
                print("    %-6s 均值 %+8.4f  标准差 %.5f" % (name, statistics.mean(col), statistics.pstdev(col)))
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description="导出 IMU（流水线第 3 步，读第 1 步的 CSV）")
    ap.add_argument("-i", "--input", default=None, help="export_all.py 的产物，默认取最新 *_all.csv")
    ap.add_argument("-o", "--output", default=None,
                    help="输出 CSV，默认 %s\\<名>_imu.csv" % IMU_DIR)
    ap.add_argument("--gyro-deg", action="store_true", help="角速度输出 deg/s（默认 rad/s）")
    ap.add_argument("--flat-attitude", action="store_true", help="不反解 pitch/roll，留空")
    ap.add_argument("--max-rows", type=int, default=0, help="最多导出多少条（试跑用）")
    args = ap.parse_args(argv)

    src = args.input or default_all_csv()
    if not os.path.isfile(src):
        print("找不到输入: %s" % src, file=sys.stderr)
        return 2
    if args.output:
        out = args.output
    else:
        os.makedirs(IMU_DIR, exist_ok=True)
        out = os.path.join(IMU_DIR, os.path.splitext(os.path.basename(src))[0].replace("_all", "") + "_imu.csv")

    print("输入:", src)
    try:
        convert(src, out, gyro_deg=args.gyro_deg, flat_attitude=args.flat_attitude,
                max_rows=args.max_rows)
    except Exception as exc:
        print("导出失败: %s" % exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
