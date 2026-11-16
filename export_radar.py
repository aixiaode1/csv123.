#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
【第 2 步】从 export_all.py 的产物里导出**雷达点云数据**（WaterScenes 逐点格式）。

输入：<项目目录>\\all\\<抓包名>_all.csv    （① 生成，只读不写；-i 可指定，缺省取最新）
      路径常量：udp_common.ALL_DIR
输出：<项目目录>\\LIDAR\\<抓包名>_radar.csv（OUTPUT_DIR 常量）
      列：timestamp, range, doppler, azimuth, elevation, power, x, y, z,
          comp_height, comp_velocity, u, v, label, instance

载荷结构（36 B 头 + 96 点 × 14 B），逐点解码后落成一行：
    [1:3] 长度=1380  [5:7] 点数=96  [7:9] 包序号  [28:36] 设备时间戳(ns)
    点：int32 x/y/z（毫米）+ 2 字节尾巴
        端口 57000 的设备：float16 强度(dB)
        其它（56300 等）：u8 反射率 + u8 tag

用法：
    python export_radar.py                              # 读最新的 *_all.csv
    python export_radar.py -i "all\\6666_all.csv"
    python export_radar.py --keep-zero                  # 保留空点
    python export_radar.py --split 1000000              # 每 100 万点切一个文件
"""

from __future__ import annotations

import argparse
import csv
import math
import os
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from udp_common import default_all_csv, iter_all_rows  # noqa: E402

HEADER_LEN = 36
POINT_LEN = 14
DOTS = 96
FIELDS = ["timestamp", "range", "doppler", "azimuth", "elevation", "power",
          "x", "y", "z", "comp_height", "comp_velocity", "u", "v",
          "label", "instance"]
HALF_PORTS = {57000}          # 这些端口的点尾 2 字节是 float16 强度
# ---------------- 路径配置（全局唯一来源：config.py） ----------------
from config import DEFAULT_PCAP, LIDAR_DIR     # noqa: F401,E402
OUTPUT_DIR = LIDAR_DIR                          # 输出：<项目目录>\LIDAR


def is_point_packet(sport, payload):
    return (len(payload) == HEADER_LEN + DOTS * POINT_LEN
            and payload[:3] == b"\x00\x64\x05")


def convert(all_csv, out_csv, keep_zero=False, split_rows=0, max_points=0, verbose=True):
    t0 = time.perf_counter()
    os.makedirs(os.path.dirname(out_csv) or ".", exist_ok=True)
    stem, ext = os.path.splitext(out_csv)
    state = {"part": 0, "rows": 0, "fh": None, "w": None, "files": []}

    def open_part():
        if state["fh"] is not None:
            state["fh"].close()
        state["part"] += 1
        name = ("%s_part%02d%s" % (stem, state["part"], ext)) if split_rows else stem + ext
        fh = open(name, "w", newline="", encoding="utf-8-sig")
        state["fh"], state["w"], state["rows"] = fh, csv.writer(fh), 0
        state["w"].writerow(FIELDS)
        state["files"].append(name)

    packets = points = 0
    open_part()
    try:
        for rec in iter_all_rows(all_csv):
            try:
                sport = int(rec["SrcPort"])
            except (KeyError, ValueError):
                continue
            payload = bytes.fromhex(rec.get("PayloadHex", ""))
            if not is_point_packet(sport, payload):
                continue
            packets += 1
            ts = float(rec["EpochTime"])
            half = sport in HALF_PORTS
            body = payload[HEADER_LEN:]
            for i in range(DOTS):
                c = body[i * POINT_LEN:(i + 1) * POINT_LEN]
                if half:
                    x, y, z, pw = struct.unpack("<iiie", c)
                else:
                    x, y, z, pw, _tag = struct.unpack("<iiiBB", c)
                if not keep_zero and x == 0 and y == 0 and z == 0:
                    continue
                if max_points and points >= max_points:
                    break
                xm, ym, zm = x / 1000.0, y / 1000.0, z / 1000.0
                if split_rows and state["rows"] >= split_rows:
                    open_part()
                state["w"].writerow([
                    "%.6f" % ts,
                    "%.4f" % math.sqrt(xm * xm + ym * ym + zm * zm),
                    "",                                   # doppler：无多普勒
                    "%.6f" % math.atan2(ym, xm),
                    "%.6f" % math.atan2(zm, math.hypot(xm, ym)),
                    ("%.2f" % pw) if half else pw,
                    "%.4f" % xm, "%.4f" % ym, "%.4f" % zm,
                    "", "", "", "",                       # comp_height / comp_velocity / u / v
                    -1, 0,                                # label / instance
                ])
                state["rows"] += 1
                points += 1
            if max_points and points >= max_points:
                break
    finally:
        if state["fh"] is not None:
            state["fh"].close()

    if verbose:
        print("雷达数据导出完成：%d 个点云包 -> %d 个点，耗时 %.1f 秒" % (packets, points, time.perf_counter() - t0))
        for name in state["files"]:
            print("  输出: %s (%.1f MB)" % (name, os.path.getsize(name) / 1024 / 1024))
    return points


def main(argv=None):
    ap = argparse.ArgumentParser(description="导出雷达点云（流水线第 2 步，读第 1 步的 CSV）")
    ap.add_argument("-i", "--input", default=None, help="export_all.py 的产物，默认取目录里最新的 *_all.csv")
    ap.add_argument("-o", "--output", default=None, help="输出 CSV，默认 %s\\<名>_radar.csv" % OUTPUT_DIR)
    ap.add_argument("--keep-zero", action="store_true", help="保留无回波空点（默认丢弃）")
    ap.add_argument("--split", type=int, default=0, metavar="ROWS", help="每多少点切一个文件")
    ap.add_argument("--max-points", type=int, default=0, help="最多导出多少点（试跑用）")
    args = ap.parse_args(argv)

    src = args.input or default_all_csv()
    if not os.path.isfile(src):
        print("找不到输入: %s" % src, file=sys.stderr)
        return 2
    if args.output:
        out = args.output
    else:
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        out = os.path.join(OUTPUT_DIR, os.path.splitext(os.path.basename(src))[0].replace("_all", "") + "_radar.csv")

    print("输入:", src)
    try:
        convert(src, out, keep_zero=args.keep_zero, split_rows=args.split, max_points=args.max_points)
    except Exception as exc:
        print("导出失败: %s" % exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
