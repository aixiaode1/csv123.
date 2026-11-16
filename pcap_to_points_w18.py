#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
把 20131206-1-w18.pcap（端口 57000）的逐点数据转成 WaterScenes 风格逐点 CSV。

与 pcap_to_points.py 的区别（同一套点结构，字段语义不同）：
    端口        57000 -> 57000        （原脚本写死 56300 -> 56301，所以读不到）
    点间隔      动态：设备时间戳差 / 96，约 2.2 µs   （原设备固定 5 µs）
    点尾 2 字节 float16 强度（2.0~28.0 dB）          （原设备是 反射率 u8 + tag u8）

协议（从载荷反推，已交叉验证）：
    UDP 载荷 1380 B = 36 B 头 + 96 点 × 14 B
    头：[1:3]=0x0564(1380,LE)  [5:7]=96(点数,LE)  [7:9]=包序号  [28:36]=设备时间戳(ns,LE)
    点：int32 x, int32 y, int32 z (mm), float16 强度(dB)

输出列（与 WaterScenes_Samples/radar/*.csv 一致）：
    timestamp, range, doppler, azimuth, elevation, power, x, y, z,
    comp_height, comp_velocity, u, v, label, instance

用法：
    python pcap_to_points_w18.py                                  # 全量（约 5200 万点，CSV 约 4 GB）
    python pcap_to_points_w18.py --duration 10                    # 先取 10 秒
    python pcap_to_points_w18.py --split 1000000                  # 每 100 万点切一个文件
    python pcap_to_points_w18.py --stride 4                       # 每 4 个点取 1 个（降采样）
    python pcap_to_points_w18.py --keep-zero                      # 保留无回波空点
    python pcap_to_points_w18.py --power raw                      # power 输出原始 uint16
"""

from __future__ import annotations

import argparse
import csv
import math
import os
import struct
import sys
import time

DEFAULT_PCAP = r"E:\lidardata\20131206-1-w18.pcap"
OUTPUT_DIR = r"E:\pycharm project\pacp——csv"

HEADER_LEN = 36
POINT_LEN = 14
DOTS_PER_PACKET = 96
PAYLOAD_LEN = HEADER_LEN + DOTS_PER_PACKET * POINT_LEN     # 1380
POINT_MAGIC = b"\x00\x64\x05"                              # 0x0564 = 1380
DEFAULT_PACKET_NS = 208320                                 # 设备时间戳默认包间隔（兜底）

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
    """逐条产出 (抓包时间, 链路类型, 原始帧字节)。"""
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
        if head[0:4] == PCAPNG_MAGIC:
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
        body = rest[:-4]
        if btype == 1:
            linktype = struct.unpack(endian + "H", body[0:2])[0]
            divisor, pos = 10 ** 6, 8
            while pos + 4 <= len(body):
                code, olen = struct.unpack(endian + "HH", body[pos:pos + 4])
                pos += 4
                if code == 0:
                    break
                if code == 9 and olen >= 1:
                    v = body[pos]
                    divisor = (2 ** (v & 0x7F)) if (v & 0x80) else (10 ** v)
                pos += (olen + 3) // 4 * 4
            interfaces.append((linktype, divisor))
        elif btype == 6:
            if len(body) < 20:
                continue
            iface, hi, lo, cap, _orig = struct.unpack(endian + "IIIII", body[:20])
            linktype, div = interfaces[iface] if iface < len(interfaces) else (1, 10 ** 6)
            yield ((hi << 32) | lo) / div, linktype, body[20:20 + cap]


# ---------------------------------------------------------------- 帧层
def point_payload(frame):
    """取出点云 UDP 载荷（不按端口写死，靠载荷长度与魔数识别）。"""
    if len(frame) < 42:
        return None
    off, etype = 12, struct.unpack(">H", frame[12:14])[0]
    while etype in (0x8100, 0x88A8, 0x9100) and len(frame) > off + 6:
        etype, off = struct.unpack(">H", frame[off + 4:off + 6])[0], off + 4
    if etype != 0x0800:
        return None
    ip = off + 2
    if (frame[ip] >> 4) != 4 or frame[ip + 9] != 17:
        return None
    ihl = (frame[ip] & 0x0F) * 4
    payload = frame[ip + ihl + 8:]
    if len(payload) != PAYLOAD_LEN or not payload.startswith(POINT_MAGIC):
        return None
    if struct.unpack("<H", payload[5:7])[0] != DOTS_PER_PACKET:
        return None
    return payload


def iter_points(path):
    """逐点产出 (时间戳, x_m, y_m, z_m, 强度float, 原始uint16)。

    点内时间用“设备时间戳包间隔 / 96”线性分配；首包用默认值兜底。
    """
    prev_dev = None
    prev_cap = None
    for cap_ts, _link, frame in iter_records(path):
        payload = point_payload(frame)
        if payload is None:
            continue
        dev_ns = struct.unpack("<Q", payload[28:36])[0]
        if prev_dev is not None and 0 < dev_ns - prev_dev < 10_000_000 and dev_ns != prev_dev:
            packet_ns = dev_ns - prev_dev
        else:
            packet_ns = DEFAULT_PACKET_NS
        # 抓包时间也做一次对齐，防止设备时间戳跳变时点内时间错位
        if prev_cap is not None:
            cap_gap = cap_ts - prev_cap
            if 0 < cap_gap < 0.01:
                step = min(packet_ns / 1e9, cap_gap) / DOTS_PER_PACKET
            else:
                step = packet_ns / 1e9 / DOTS_PER_PACKET
        else:
            step = packet_ns / 1e9 / DOTS_PER_PACKET
        prev_dev, prev_cap = dev_ns, cap_ts

        body = payload[HEADER_LEN:]
        for i in range(DOTS_PER_PACKET):
            chunk = body[i * POINT_LEN:(i + 1) * POINT_LEN]
            x, y, z, half = struct.unpack("<iiie", chunk)      # 12B 坐标 + float16 强度
            raw = struct.unpack("<H", chunk[12:14])[0]
            yield cap_ts + i * step, x / 1000.0, y / 1000.0, z / 1000.0, half, raw


# ---------------------------------------------------------------- 输出层
def convert(pcap_path, csv_path, start=0.0, duration=None, keep_zero=False,
            split_rows=0, stride=1, power_mode="half", verbose=True):
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
        for idx, (ts, x, y, z, half, raw) in enumerate(iter_points(pcap_path)):
            if first_ts is None:
                first_ts = ts
                stop_ts = ts + duration if duration else None
            if ts - first_ts < start:
                continue
            if stop_ts is not None and ts > stop_ts:
                break
            if stride > 1 and idx % stride:
                continue
            if not keep_zero and x == 0.0 and y == 0.0 and z == 0.0:
                continue
            if split_rows and state["rows"] >= split_rows:
                open_part()
            rng = math.sqrt(x * x + y * y + z * z)
            state["writer"].writerow([
                "%.6f" % ts,
                "%.4f" % rng,
                "",
                "%.6f" % math.atan2(y, x),
                "%.6f" % math.atan2(z, math.hypot(x, y)),
                ("%.2f" % half) if power_mode == "half" else raw,
                "%.4f" % x, "%.4f" % y, "%.4f" % z,
                "", "", "", "",
                -1, 0,
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
    ap = argparse.ArgumentParser(description="w18 抓包 -> WaterScenes 风格逐点 CSV")
    ap.add_argument("pcap", nargs="?", default=DEFAULT_PCAP)
    ap.add_argument("-o", "--output", default=None)
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--duration", type=float, default=None)
    ap.add_argument("--keep-zero", action="store_true")
    ap.add_argument("--split", type=int, default=0, metavar="ROWS")
    ap.add_argument("--stride", type=int, default=1, metavar="N", help="每 N 个点取 1 个")
    ap.add_argument("--power", choices=("half", "raw"), default="half",
                    help="power 列：half=float16 强度(dB)，raw=原始 uint16")
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
        convert(path, out, start=args.start, duration=args.duration, keep_zero=args.keep_zero,
                split_rows=args.split, stride=args.stride, power_mode=args.power)
    except Exception as exc:
        print("转换失败: %s" % exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
