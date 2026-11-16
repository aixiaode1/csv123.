#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
画出 UDP 载荷的「分区结构图」：每一段字节代表什么字段、实际值是多少。

三层：
  ① 整体布局：36 B 头 + 96 个点 × 14 B = 1380 B
  ② 头部放大：逐字段（偏移、字节数、字段名、实际值）
  ③ 单个点放大：x / y / z / 反射率 / tag

用法：python draw_payload_map.py [pcap] [-o out.png]
"""

from __future__ import annotations

import argparse
import os
import struct
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import Rectangle

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)                                  # tools/ 自身
sys.path.insert(0, os.path.dirname(_HERE))                 # 项目根，导入 udp_common
from udp_common import iter_records, udp_of

# ---------------- 路径配置（全局唯一来源：config.py） ----------------
from config import DEFAULT_PCAP, PHOTO_DIR     # noqa: F401,E402
OUTPUT_DIR = PHOTO_DIR                          # 输出：<项目目录>\photo
C_HEAD = "#cfe2f3"
C_POINT = "#ffe599"
C_X, C_Y, C_Z = "#f4cccc", "#d9ead3", "#c9daf8"
C_R, C_T = "#d0e0e3", "#e6d7f0"


def setup_cjk_font():
    for name in ("Microsoft YaHei", "SimHei", "SimSun", "DengXian"):
        try:
            font_manager.findfont(font_manager.FontProperties(family=name), fallback_to_default=False)
            plt.rcParams["font.sans-serif"] = [name]
            plt.rcParams["axes.unicode_minus"] = False
            return name
        except Exception:
            continue
    return None


def grab(pcap_path, sport=56300, sample=300):
    """取代表包 + 统计：首个有回波的点、包间设备时间戳差、平均填充率。"""
    tail = "half" if sport == 57000 else "u8"
    first, devs, fills, n = None, [], [], 0
    first_p = None
    for ts, linktype, frame in iter_records(pcap_path):
        info = udp_of(frame, linktype)
        if not info or info[1] != sport:
            continue
        p = info[4]
        if len(p) != 1380:
            continue
        n += 1
        devs.append(struct.unpack("<Q", p[28:36])[0])
        filled = 0
        for i in range(96):
            c = p[36 + i * 14: 36 + (i + 1) * 14]
            if tail == "half":
                x, y, z, h = struct.unpack("<iiie", c)
                ok = bool(x or y or z)
                vals = (x, y, z, h, None)
            else:
                x, y, z, r, t = struct.unpack("<iiiBB", c)
                ok = bool(x or y or z)
                vals = (x, y, z, r, t)
            if ok:
                filled += 1
                if first is None:
                    first = (i, vals)
                    first_p = p
        fills.append(filled)
        if n >= sample and first is not None:
            break
    if first is None:
        raise SystemExit("没读到点云包")
    deltas = sorted(devs[i + 1] - devs[i] for i in range(len(devs) - 1)) if len(devs) > 1 else []
    delta = deltas[len(deltas) // 2] if deltas else 0
    return first_p, first[0], first[1], delta, sum(fills) / len(fills) / 96 * 100, tail




def box(ax, x, w, y, h, face, name, value, offset, rot=0, fs=10, fs2=9):
    ax.add_patch(Rectangle((x, y), w, h, facecolor=face, edgecolor="#333333", lw=1.0))
    ax.text(x + w / 2, y + h + 0.12, name, ha="center", va="bottom",
            fontsize=fs, fontweight="bold")
    ax.text(x + w / 2, y + h / 2, value, ha="center", va="center",
            fontsize=fs2, family="monospace")
    ax.text(x + w / 2, y - 0.08, offset, ha="center", va="top", fontsize=8, color="#555555")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("pcap", nargs="?", default=r"E:\lidardata\99999.pcapng")
    ap.add_argument("--sport", type=int, default=56300)
    ap.add_argument("-o", "--out", default=None, help="默认写入 photo 目录")
    args = ap.parse_args(argv)
    if not args.out:                       # 默认文件名与文档里的图片对应
        args.out = os.path.join(OUTPUT_DIR, "B-file.png" if args.sport == 57000 else "A-file.png")

    setup_cjk_font()
    p, idx, vals, delta_ns, fill_pct, tail = grab(args.pcap, args.sport)
    px, py, pz = vals[0], vals[1], vals[2]

    ln = struct.unpack("<H", p[1:3])[0]
    vendor = p[3:5]
    dots = struct.unpack("<H", p[5:7])[0]
    seq = struct.unpack("<H", p[7:9])[0]
    dev_ns = struct.unpack("<Q", p[28:36])[0]

    fig = plt.figure(figsize=(14, 10))
    gs = fig.add_gridspec(4, 1, height_ratios=[0.7, 1.7, 1.0, 0.45], hspace=0.55)
    fig.suptitle("UDP 载荷结构图 · 1380 字节 = 36 B 头部 + 96 点 × 14 B\n(%s)" %
                 os.path.basename(args.pcap), fontsize=14)

    # ---------------- ① 整体布局 ----------------
    ax = fig.add_subplot(gs[0])
    ax.set_title("① 整体布局：载荷 1380 字节", fontsize=11, loc="left")
    ax.add_patch(Rectangle((0, 0), 36, 1, facecolor=C_HEAD, edgecolor="#333", lw=1.2))
    ax.text(18, 0.5, "36 B", ha="center", va="center", fontsize=8, fontweight="bold")
    for i in range(96):
        ax.add_patch(Rectangle((36 + i * 14, 0), 14, 1,
                               facecolor=C_POINT if i % 2 == 0 else "#fff2cc",
                               edgecolor="#888", lw=0.4))
    ax.annotate("96 个点 × 14 B = 1344 B", xy=(36 + 48 * 14 + 7, 1.0),
                xytext=(36 + 38 * 14, 1.9), fontsize=10,
                arrowprops=dict(arrowstyle="->", lw=0.8))
    ax.annotate("头部 36 B", xy=(18, 1.0), xytext=(60, 1.9), fontsize=10,
                arrowprops=dict(arrowstyle="->", lw=0.8))
    ax.set_xlim(-10, 1395)
    ax.set_ylim(-0.1, 2.3)
    ax.set_xlabel("字节偏移")
    ax.set_yticks([])
    ax.grid(axis="x", alpha=0.3)

    # ---------------- ② 头部 ----------------
    ax = fig.add_subplot(gs[1])
    ax.set_title("② 头部 36 字节逐字段", fontsize=11, loc="left")
    fields = [
        (1, "版本", "0x%02x" % p[0], "[0]"),
        (2, "长度", "%d" % ln, "[1:3]"),
        (2, "厂商", p[3:5].hex(" "), "[3:5]"),
        (2, "点数", "%d" % dots, "[5:7]"),
        (2, "包序号", "%d" % seq, "[7:9]"),
        (2, "类型", p[9:11].hex(" "), "[9:11]"),
        (17, "保留（全 0）", "", "[11:28]"),
        (8, "设备时间戳 (ns)", "%.6f s" % (dev_ns / 1e9), "[28:36]"),
    ]
    x = 0
    for w, name, value, off in fields:
        box(ax, x, w, 0, 1, C_HEAD, name, value, off, fs=9, fs2=9)
        x += w
    ax.set_xlim(-0.3, 36.3)
    ax.set_ylim(-0.35, 1.45)
    ax.axis("off")

    # ---------------- ③ 单个点 ----------------
    ax = fig.add_subplot(gs[2])
    if tail == "half":
        pr, pt = vals[3], None
        ax.set_title("③ 第 %d 个点（14 字节）：坐标 + 强度（设备 B，尾部是 float16）" % idx,
                     fontsize=11, loc="left")
        fields = [
            (4, "x = %d mm\n(%.3f m)" % (px, px / 1000), C_X, "[0:4]"),
            (4, "y = %d mm\n(%.3f m)" % (py, py / 1000), C_Y, "[4:8]"),
            (4, "z = %d mm\n(%.3f m)" % (pz, pz / 1000), C_Z, "[8:12]"),
            (2, "强度 = %.2f dB" % pr, C_R, "[12:14]"),
        ]
    else:
        pr, pt = vals[3], vals[4]
        ax.set_title("③ 第 %d 个点（14 字节）：坐标 + 反射率 + tag（设备 A）" % idx,
                     fontsize=11, loc="left")
        fields = [
            (4, "x = %d mm\n(%.3f m)" % (px, px / 1000), C_X, "[0:4]"),
            (4, "y = %d mm\n(%.3f m)" % (py, py / 1000), C_Y, "[4:8]"),
            (4, "z = %d mm\n(%.3f m)" % (pz, pz / 1000), C_Z, "[8:12]"),
            (1, "反射率\n%d" % pr, C_R, "[12]"),
            (1, "tag\n%d" % pt, C_T, "[13]"),
        ]
    x = 0
    for w, name, face, off in fields:
        ax.add_patch(Rectangle((x, 0), w, 1, facecolor=face, edgecolor="#333", lw=1.0))
        ax.text(x + w / 2, 0.62, name, ha="center", va="center", fontsize=9)
        raw = p[36 + idx * 14 + x: 36 + idx * 14 + x + w].hex(" ")
        ax.text(x + w / 2, 0.22, raw, ha="center", va="center", fontsize=9, family="monospace")
        ax.text(x + w / 2, -0.08, off, ha="center", va="top", fontsize=8, color="#555")
        x += w
    ax.set_xlim(-0.2, 14.2)
    ax.set_ylim(-0.4, 1.2)
    ax.axis("off")

    # ---------------- ④ 汇总文字 ----------------
    ax = fig.add_subplot(gs[3])
    ax.axis("off")
    import math
    per_point_us = (delta_ns / 96 / 1000.0) if delta_ns else 0.0
    rate_khz = (96 / (delta_ns / 1e9) / 1000.0) if delta_ns else 0.0
    txt = (
        "填充率：抽样统计平均 {fill:.0f}% 的点槽有回波，其余为 x=y=z=0 的空点\n"
        "坐标单位是毫米，÷1000 得米；本点距离 = √(x²+y²+z²) = {dist:.3f} m\n"
        "设备时间戳每包递增 {delta:,} ns = 96 点 × {per:.2f} µs，即点频 {rate:.0f} kHz"
    ).format(fill=fill_pct,
             dist=math.sqrt(px ** 2 + py ** 2 + pz ** 2) / 1000,
             delta=delta_ns, per=per_point_us, rate=rate_khz)
    ax.text(0.01, 0.5, txt, fontsize=10, va="center", linespacing=1.8)

    fig.savefig(args.out, dpi=150, bbox_inches="tight")
    print("已保存:", args.out, "%.0f KB" % (os.path.getsize(args.out) / 1024))


if __name__ == "__main__":
    main()
