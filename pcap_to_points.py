#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
输出为点云格式
把抓包里的逐点数据转成 WaterScenes 风格的逐点 CSV（纯标准库，单文件，无依赖）。

目标列（与 WaterScenes_Samples/radar/*.csv 完全一致）：
    timestamp, range, doppler, azimuth, elevation, power, x, y, z,
    comp_height, comp_velocity, u, v, label, instance

从 E:\\lidardata\\99999.pcapng 解出的协议：
    UDP 载荷 1380 B = 36 B 头 + 96 点 × 14 B
    头：[0]=0x00  [1:3]=0x0564(1380,LE)  [5:7]=96(点数,LE)  [7:9]=包序号  [28:36]=设备时间戳(ns,LE)
    点：int32 x, int32 y, int32 z (mm) + uint8 反射率 + uint8 tag
    设备时间戳每包 +480 µs = 96 点 × 5 µs  → 200 kHz

用法：
    python pcap_to_points.py "E:\\lidardata\\99999.pcapng"                     # 全量，输出到 E:\\dsh-output
    python pcap_to_points.py "....pcapng" --duration 10                        # 只取前 10 秒
    python pcap_to_points.py "....pcapng" --start 60 --duration 10             # 第 60 秒起 10 秒
    python pcap_to_points.py "....pcapng" --split 1000000                      # 每 100 万点切一个文件
    python pcap_to_points.py "....pcapng" --keep-zero                          # 保留无回波空点
    python pcap_to_points.py "....pcapng" -o D:\\out.csv                       # 指定输出路径
"""

from __future__ import annotations

import argparse
import csv
import math
import os
import struct
import sys
import time

DEFAULT_PCAP = r"E:\lidardata\yyy.pcapng"
OUTPUT_DIR = r"E:\pycharm project\pacp——csv"

HEADER_LEN = 36          # 每包头部字节数
POINT_LEN = 14           # 每点字节数：int32 x,y,z + uint8 反射率 + uint8 tag
DOTS_PER_PACKET = 96
POINT_INTERVAL_S = 5e-6  # 每点 5 µs（480 µs / 96）
POINT_MAGIC = b"\x00\x64\x05"   # 载荷前 3 字节，0x0564 = 1380 = 载荷长度

FIELDS = ["timestamp", "range", "doppler", "azimuth", "elevation", "power",
          "x", "y", "z", "comp_height", "comp_velocity", "u", "v",
          "label", "instance"]

PCAP_MAGICS = {
    b"\xd4\xc3\xb2\xa1": ("<", 1e6), b"\xa1\xb2\xc3\xd4": (">", 1e6),
    b"\x4d\x3c\xb2\xa1": ("<", 1e9), b"\xa1\xb2\x3c\x4d": (">", 1e9),
}
PCAPNG_MAGIC = b"\x0a\x0d\x0d\x0a"


# ---------------------------------------------------------------- 容器层
def iter_records(path):
    """逐条产出 (抓包时间, 链路类型, 原始帧字节)。支持 pcap 与 pcapng。"""
    with open(path, "rb") as fh:
        magic = fh.read(4)
        if magic == PCAPNG_MAGIC:
            fh.seek(0)
            yield from _pcapng(fh)
        elif magic in PCAP_MAGICS:
            yield from _pcap(fh, magic)
        else:
            raise ValueError("不是 pcap/pcapng 文件（文件头 %r）" % magic)


def _pcap(fh, magic):
    endian, div = PCAP_MAGICS[magic]
    rest = fh.read(20)
    if len(rest) < 20:
        return
    linktype = struct.unpack(endian + "I", rest[16:20])[0]
    while True:
        hdr = fh.read(16)
        if len(hdr) < 16:
            return
        sec, frac, incl, _orig = struct.unpack(endian + "IIII", hdr)
        if incl > 0x04000000:
            raise ValueError("异常抓包长度: %d" % incl)
        data = fh.read(incl)
        if len(data) < incl:
            return
        yield sec + frac / div, linktype, data


def _pcapng(fh):
    endian, interfaces = "<", []
    while True:
        head = fh.read(8)
        if len(head) < 8:
            return
        if head[0:4] == PCAPNG_MAGIC:                      # Section Header Block
            bom = fh.read(4)
            if len(bom) < 4:
                return
            endian = ">" if bom == b"\x1a\x2b\x3c\x4d" else "<"
            blen = struct.unpack(endian + "I", head[4:8])[0]
            fh.read(blen - 12)
            interfaces = []
            continue
        btype, blen = struct.unpack(endian + "II", head)
        if blen < 12 or blen > 0x0FFFFFFF:
            raise ValueError("pcapng 块长度异常: %d" % blen)
        rest = fh.read(blen - 8)
        if len(rest) < blen - 8:
            return
        body = rest[:-4]                                   # 去掉尾部重复长度
        if btype == 1:                                     # Interface Description
            linktype = struct.unpack(endian + "H", body[0:2])[0]
            divisor, pos = 10 ** 6, 8
            while pos + 4 <= len(body):
                code, olen = struct.unpack(endian + "HH", body[pos:pos + 4])
                pos += 4
                if code == 0:
                    break
                if code == 9 and olen >= 1:                # if_tsresol
                    v = body[pos]
                    divisor = (2 ** (v & 0x7F)) if (v & 0x80) else (10 ** v)
                pos += (olen + 3) // 4 * 4
            interfaces.append((linktype, divisor))
        elif btype == 6:                                   # Enhanced Packet Block
            if len(body) < 20:
                continue
            iface, hi, lo, cap, _orig = struct.unpack(endian + "IIIII", body[:20])
            linktype, div = interfaces[iface] if iface < len(interfaces) else (1, 10 ** 6)
            yield ((hi << 32) | lo) / div, linktype, body[20:20 + cap]


# ---------------------------------------------------------------- 帧层
def udp_payload(frame):
    """从以太网帧里取出 UDP 载荷；不是 IPv4/UDP 就返回 None。"""
    if len(frame) < 42:
        return None
    off, etype = 12, struct.unpack(">H", frame[12:14])[0]
    while etype in (0x8100, 0x88A8, 0x9100) and len(frame) > off + 6:   # VLAN
        etype, off = struct.unpack(">H", frame[off + 4:off + 6])[0], off + 4
    if etype != 0x0800:
        return None
    ip = off + 2
    if (frame[ip] >> 4) != 4:
        return None
    ihl = (frame[ip] & 0x0F) * 4
    if frame[ip + 9] != 17:                                # 只取 UDP
        return None
    sport, dport = struct.unpack(">HH", frame[ip + ihl:ip + ihl + 4])
    if sport != 56300 or dport != 56301:                   # 点云流端口
        return None
    return frame[ip + ihl + 8:]


def iter_points(path):
    """逐点产出 (时间戳, x_m, y_m, z_m, 反射率, tag)。"""
    for cap_ts, _linktype, frame in iter_records(path):
        payload = udp_payload(frame)
        if not payload or not payload.startswith(POINT_MAGIC):
            continue
        if len(payload) != HEADER_LEN + DOTS_PER_PACKET * POINT_LEN:
            continue
        body = payload[HEADER_LEN:]
        for i in range(DOTS_PER_PACKET):
            x, y, z, refl, tag = struct.unpack("<iiiBB", body[i * POINT_LEN:(i + 1) * POINT_LEN])
            yield cap_ts + i * POINT_INTERVAL_S, x / 1000.0, y / 1000.0, z / 1000.0, refl, tag


# ---------------------------------------------------------------- 输出层
def convert(pcap_path, csv_path, start=0.0, duration=None, keep_zero=False,
            split_rows=0, verbose=True):
    stem, ext = os.path.splitext(csv_path)
    if not ext:
        stem, ext = csv_path, ".csv"
    t0 = time.perf_counter()
    state = {"part": 0, "rows": 0, "fh": None, "writer": None, "files": []}
    first_ts = None
    stop_ts = None
    total = 0

    def open_part():
        if state["fh"] is not None:
            state["fh"].close()
        state["part"] += 1
        name = ("%s_part%02d%s" % (stem, state["part"], ext)) if split_rows else stem + ext
        fh = open(name, "w", newline="", encoding="utf-8-sig")
        state["fh"] = fh
        state["writer"] = csv.writer(fh)
        state["writer"].writerow(FIELDS)
        state["rows"] = 0
        state["files"].append(name)

    open_part()
    try:
        for ts, x, y, z, refl, _tag in iter_points(pcap_path):
            if first_ts is None:
                first_ts = ts
                stop_ts = ts + duration if duration else None
            if ts - first_ts < start:
                continue
            if stop_ts is not None and ts > stop_ts:
                break
            if not keep_zero and x == 0.0 and y == 0.0 and z == 0.0:
                continue
            if split_rows and state["rows"] >= split_rows:
                open_part()
            rng = math.sqrt(x * x + y * y + z * z)
            state["writer"].writerow([
                "%.6f" % ts,
                "%.4f" % rng,
                "",                                   # doppler：激光无多普勒
                "%.6f" % math.atan2(y, x),            # azimuth
                "%.6f" % math.atan2(z, math.hypot(x, y)),   # elevation
                refl,                                 # power = 反射率
                "%.4f" % x, "%.4f" % y, "%.4f" % z,
                "", "", "", "",                       # comp_height / comp_velocity / u / v
                -1, 0,                                # label / instance（未标注）
            ])
            state["rows"] += 1
            total += 1
    finally:
        if state["fh"] is not None:
            state["fh"].close()

    if verbose:
        print("完成：%d 个点，耗时 %.1f 秒" % (total, time.perf_counter() - t0))
        for name in state["files"]:
            print("  输出: %s (%.1f MB)" % (name, os.path.getsize(name) / 1024 / 1024))
    return total


def main(argv=None):
    ap = argparse.ArgumentParser(description="抓包逐点数据 -> WaterScenes 风格 CSV")
    ap.add_argument("pcap", nargs="?", default=DEFAULT_PCAP, help="抓包文件，默认 %s" % DEFAULT_PCAP)
    ap.add_argument("-o", "--output", default=None, help="输出 CSV，默认 %s\\<名>_points.csv" % OUTPUT_DIR)
    ap.add_argument("--start", type=float, default=0.0, help="从第几秒开始")
    ap.add_argument("--duration", type=float, default=None, help="取多长（秒）")
    ap.add_argument("--keep-zero", action="store_true", help="保留无回波空点（x=y=z=0）")
    ap.add_argument("--split", type=int, default=0, metavar="ROWS", help="每多少点切一个文件")
    args = ap.parse_args(argv)

    path = os.path.abspath(args.pcap)
    if not os.path.isfile(path):
        print("找不到文件: %s" % path, file=sys.stderr)
        return 2
    if args.output:
        out = args.output
    else:
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        out = os.path.join(OUTPUT_DIR, os.path.splitext(os.path.basename(path))[0] + "_points.csv")

    try:
        convert(path, out, start=args.start, duration=args.duration,
                keep_zero=args.keep_zero, split_rows=args.split)
    except Exception as exc:
        print("转换失败: %s" % exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
