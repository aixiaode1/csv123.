#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
【第 1 步】导出抓包里**所有 UDP 数据**（每包一行，含完整载荷十六进制）。
流水线里唯一解析 pcap 的脚本；②③ 都只读它产出的 CSV。

输入：pcap / pcapng 文件
      默认取 config.py 的 DEFAULT_PCAP，可用位置参数覆盖
输出：<项目目录>/all/<抓包名>_all.csv     （目录由 config.py 的 ALL_DIR 决定）
      列：No, Time, EpochTime, SrcIP, SrcPort, DstIP, DstPort, UDPLen, PayloadHex

用法：
    python export_all.py                                   # 用 DEFAULT_PCAP
    python export_all.py "E:\\lidardata\\6666.pcapng"
    python export_all.py "....pcap" --duration 10          # 只导前 10 秒（试跑用）
    python export_all.py "....pcap" --hex 32               # 只保留载荷前 32 字节，文件小很多
    python export_all.py "....pcap" --start 60 --duration 10
"""

from __future__ import annotations

import argparse
import collections
import csv
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from udp_common import ALL_DIR, iter_records, udp_of          # noqa: E402

# ---------------- 路径配置（全局唯一来源：config.py） ----------------
from config import ALL_DIR, DEFAULT_PCAP       # noqa: F401,E402
OUTPUT_DIR = ALL_DIR                            # 输出：<项目目录>\all

FIELDS = ["No", "Time", "EpochTime", "SrcIP", "SrcPort", "DstIP", "DstPort", "UDPLen", "PayloadHex"]


def export(pcap_path, csv_path, hex_bytes=0, start=0.0, duration=None,
           max_packets=0, verbose=True):
    """hex_bytes: 0 = 完整载荷（下游要用），N = 只保留前 N 字节。"""
    t0 = time.perf_counter()
    flows = collections.Counter()
    n = 0
    t_first = None
    stop_ts = None

    os.makedirs(os.path.dirname(csv_path) or ".", exist_ok=True)
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerow(FIELDS)
        for ts, linktype, frame in iter_records(pcap_path):
            info = udp_of(frame, linktype)
            if info is None:
                continue
            src, sport, dst, dport, payload = info
            if t_first is None:
                t_first = ts
                stop_ts = ts + duration if duration else None
            if ts - t_first < start:
                continue
            if stop_ts is not None and ts > stop_ts:
                break

            n += 1
            flows[(src, sport, dst, dport)] += 1
            hexs = payload.hex() if hex_bytes == 0 else payload[:hex_bytes].hex()
            writer.writerow([n, "%.6f" % ts, "%.6f" % ts, src, sport, dst, dport, len(payload), hexs])

            if max_packets and n >= max_packets:
                break

    if verbose:
        print("导出完成：%d 个 UDP 包，%d 条流，耗时 %.1f 秒" % (n, len(flows), time.perf_counter() - t0))
        print("  输出: %s (%.1f MB)" % (csv_path, os.path.getsize(csv_path) / 1024 / 1024))
        for key, cnt in flows.most_common(10):
            print("    %s:%d -> %s:%d   %d 包" % (key[0], key[1], key[2], key[3], cnt))
        print("  下一步: python export_radar.py    或    python export_imu.py")
    return n


def main(argv=None):
    ap = argparse.ArgumentParser(description="导出所有 UDP 数据（流水线第 1 步）")
    ap.add_argument("pcap", nargs="?", default=DEFAULT_PCAP, help="抓包文件，默认 %s" % DEFAULT_PCAP)
    ap.add_argument("-o", "--output", default=None,
                    help="输出 CSV，默认 %s\\<抓包名>_all.csv" % OUTPUT_DIR)
    ap.add_argument("--hex", type=int, default=0,
                    help="载荷保留字节数，0=完整（默认，下游需要）；给 32 可大幅缩小文件")
    ap.add_argument("--start", type=float, default=0.0, help="从第几秒开始")
    ap.add_argument("--duration", type=float, default=None, help="取多长（秒）")
    ap.add_argument("--max-packets", type=int, default=0, help="最多导出多少包")
    args = ap.parse_args(argv)

    path = os.path.abspath(args.pcap)
    if not os.path.isfile(path):
        print("找不到文件: %s" % path, file=sys.stderr)
        return 2
    if args.output:
        out = args.output
    else:
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        out = os.path.join(OUTPUT_DIR, os.path.splitext(os.path.basename(path))[0] + "_all.csv")

    try:
        export(path, out, hex_bytes=args.hex, start=args.start, duration=args.duration,
               max_packets=args.max_packets)
    except Exception as exc:
        print("导出失败: %s" % exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
