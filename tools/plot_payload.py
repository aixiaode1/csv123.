#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
把点云载荷里的数据画出来：
  A 一个 UDP 载荷（96 点）
  B 约 1 秒的数据（x-y 俯视）
  C 侧视（x-z）
  D 距离-反射率分布

用法：
    python plot_payload.py                                   # 默认设备 A（端口 56300）
    python plot_payload.py "E:\\lidardata\\20131206-1-w18.pcap" --sport 57000
    python plot_payload.py "....pcap" --packets 500 -o out.png

两台设备点结构相同（36 B 头 + 96 × 14 B），只有尾部 2 字节不同：
    A：u8 反射率 + u8 tag      B：float16 强度(dB)   —— 脚本按 --sport 自动选
"""

from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)                                  # tools/ 自身
sys.path.insert(0, os.path.dirname(_HERE))                 # 项目根，导入 udp_common
from udp_common import iter_records, udp_of      # 复用已验证的解析

OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "photo")
DTYPE_U8 = np.dtype([("x", "<i4"), ("y", "<i4"), ("z", "<i4"), ("r", "u1"), ("t", "u1")])
DTYPE_HALF = np.dtype([("x", "<i4"), ("y", "<i4"), ("z", "<i4"), ("r", "<f2")])


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


def load(pcap_path, max_packets=2000, sport=56300, tail="auto"):
    if tail == "auto":
        tail = "half" if sport == 57000 else "u8"
    dtype = DTYPE_HALF if tail == "half" else DTYPE_U8

    best, best_n = None, -1
    chunks, used = [], 0
    for ts, linktype, frame in iter_records(pcap_path):
        info = udp_of(frame, linktype)
        if not info or info[1] != sport:
            continue
        payload = info[4]
        if len(payload) != 1380:
            continue
        raw = np.frombuffer(payload[36:], dtype=dtype)
        valid = raw[(raw["x"] != 0) | (raw["y"] != 0) | (raw["z"] != 0)]
        if len(valid) > best_n:                     # 挑“点数最多”的那个包做 A 面板
            best, best_n = raw, len(valid)
        if len(valid):
            chunks.append(valid)
            used += 1
        if used >= max_packets:
            break
    if not chunks:
        raise SystemExit("没读到点云数据：检查端口（A=56300，w18=57000）与文件")
    return best, np.concatenate(chunks), tail


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("pcap", nargs="?", default=r"E:\lidardata\99999.pcapng")
    ap.add_argument("--packets", type=int, default=2000, help="读取多少个点云包")
    ap.add_argument("--sport", type=int, default=56300, help="点云流源端口，w18 设备是 57000")
    ap.add_argument("--tail", choices=("auto", "u8", "half"), default="auto",
                    help="点尾 2 字节含义：u8=反射率+tag，half=float16 强度")
    ap.add_argument("-o", "--out", default=None, help="默认写入 photo 目录")
    args = ap.parse_args(argv)
    if not args.out:                       # 默认文件名与文档里的图片对应
        args.out = os.path.join(OUT_DIR, "B-file.png" if args.sport == 57000 else "points-A.png")

    font = setup_cjk_font()
    print("中文字体:", font or "未找到（标签会显示成方块）")

    one, arr, tail = load(args.pcap, args.packets, sport=args.sport, tail=args.tail)
    x, y, z = arr["x"] / 1000.0, arr["y"] / 1000.0, arr["z"] / 1000.0
    refl = arr["r"].astype(float)
    rng = np.sqrt(x ** 2 + y ** 2 + z ** 2)
    cbar_label = "强度 (dB)" if tail == "half" else "反射率 (0-255)"

    fig, axes = plt.subplots(2, 2, figsize=(13, 10))
    fig.suptitle("UDP 载荷里的点云数据  (%s)" % os.path.basename(args.pcap), fontsize=14)

    # A: 单个载荷包
    ax = axes[0, 0]
    ok = (one["x"] != 0) | (one["y"] != 0) | (one["z"] != 0)
    ox, oy = one["x"][ok] / 1000.0, one["y"][ok] / 1000.0
    orf = one["r"][ok].astype(float)
    sc = ax.scatter(ox, oy, c=orf, s=10 + orf / (2.0 if tail == "half" else 3.0),
                    cmap="turbo", edgecolors="none")
    ax.set_title("A · 一个 UDP 载荷 = 1380 B = 头 36 B + 96 点 × 14 B\n（点数最多的那个包，96 点里 %d 个有回波）" % ok.sum())
    ax.set_xlabel("x (m)")
    ax.set_ylabel("y (m)")
    ax.set_aspect("equal", adjustable="datalim")
    ax.grid(alpha=0.3)
    fig.colorbar(sc, ax=ax, label=cbar_label)

    # B: 俯视
    ax = axes[0, 1]
    sc = ax.scatter(x, y, c=refl, s=0.25, cmap="turbo", alpha=0.6, edgecolors="none")
    ax.set_title("B · 约 1 秒的数据（%d 个包 / %d 个点）俯视 x-y" % (args.packets, len(arr)))
    ax.set_xlabel("x (m)")
    ax.set_ylabel("y (m)")
    ax.set_aspect("equal", adjustable="datalim")
    ax.grid(alpha=0.3)
    fig.colorbar(sc, ax=ax, label=cbar_label)

    # C: 侧视
    ax = axes[1, 0]
    sc = ax.scatter(x, z, c=refl, cmap="turbo", s=0.3, alpha=0.6, edgecolors="none")
    ax.set_title("C · 侧视 x-z（颜色=%s，看高度分布）" % cbar_label.split(" ")[0])
    ax.set_xlabel("x (m)")
    ax.set_ylabel("z (m)")
    ax.axhline(0, color="gray", lw=0.6, ls="--")
    ax.grid(alpha=0.3)
    fig.colorbar(sc, ax=ax, label=cbar_label)

    # D: 距离-反射率
    ax = axes[1, 1]
    hb = ax.hexbin(rng, refl, gridsize=60, cmap="magma", mincnt=1, bins="log")
    ax.set_title("D · 距离-%s 分布（对数色标）" % cbar_label.split(" ")[0])
    ax.set_xlabel("距离 (m)")
    ax.set_ylabel(cbar_label)
    fig.colorbar(hb, ax=ax, label="点数(log10)")

    fig.tight_layout(rect=(0, 0, 1, 0.96))
    fig.savefig(args.out, dpi=150)
    print("已保存: %s (%.0f KB)" % (args.out, os.path.getsize(args.out) / 1024))
    print("点数 %d，距离 %.1f~%.1f m，%s %.1f~%.1f" % (len(arr), rng.min(), rng.max(),
                                                 cbar_label, refl.min(), refl.max()))


if __name__ == "__main__":
    main()
