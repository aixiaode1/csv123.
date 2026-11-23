#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
IMU 包结构图：60 字节 = 36 B 头部 + 6 × float32（陀螺三轴 + 加速度三轴）

面板：
  ① 整体布局      ② 头部逐字段      ③ 6 个 float32 逐字段      ④ 通道时序曲线

用法：python draw_imu_map.py [pcap] [--sport 56400] [-o out.png]
"""

from __future__ import annotations

import argparse
import os
import statistics
import struct
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)                                  # tools/ 自身
sys.path.insert(0, os.path.dirname(_HERE))                 # 项目根，导入 udp_common
from database.udp_common import iter_records, udp_of
from draw_payload_map import box, setup_cjk_font, C_HEAD

# ---------------- 路径配置（输入 + data） ----------------
DEFAULT_PCAP = r"E:\lidardata\yyy.pcapng"          # 输入：默认抓包文件
OUTPUT_DIR = r"E:\pycharm project\pacp——csv\photo"                      # data：结构图 / 点云图
NAMES = ["陀螺 x", "陀螺 y", "陀螺 z", "加速度 x", "加速度 y", "加速度 z"]
UNITS = ["rad/s"] * 3 + ["g"] * 3
COLORS = ["#e06666", "#93c47d", "#6d9eeb", "#e69138", "#8e7cc3", "#76a5af"]
FILLS = ["#e6b8af", "#d9ead3", "#c9daf8", "#fcdeb5", "#d9d2e9", "#d0e0e3"]


def grab(pcap_path, sport=56400, sample=400):
    packets = []
    for ts, linktype, frame in iter_records(pcap_path):
        info = udp_of(frame, linktype)
        if not info or info[1] != sport:
            continue
        p = info[4]
        if len(p) != 60:
            continue
        vals = struct.unpack("<6f", p[36:60])
        packets.append((ts, p, vals))
        if len(packets) >= sample:
            break
    if not packets:
        raise SystemExit("没读到 IMU 包（端口 56400，载荷 60 字节）")
    return packets


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("pcap", nargs="?", default=r"E:\lidardata\99999.pcapng")
    ap.add_argument("--sport", type=int, default=56400)
    ap.add_argument("--sample", type=int, default=400, help="取多少个包画时序")
    ap.add_argument("-o", "--out", default=None, help="默认写入 photo 目录")
    args = ap.parse_args(argv)
    if not args.out:                       # 默认文件名与文档里的图片对应
        args.out = os.path.join(OUTPUT_DIR, "imu.png")

    setup_cjk_font()
    packets = grab(args.pcap, args.sport, args.sample)
    ts0, p0, vals0 = packets[0]
    dev_ns = struct.unpack("<Q", p0[28:36])[0]
    ln = struct.unpack("<H", p0[1:3])[0]
    cnt = struct.unpack("<H", p0[5:7])[0]
    seq = struct.unpack("<H", p0[7:9])[0]

    fig = plt.figure(figsize=(14, 12.5))
    gs = fig.add_gridspec(5, 1, height_ratios=[0.45, 1.05, 0.95, 1.0, 1.0], hspace=0.62)
    fig.suptitle("IMU 包结构图 · 60 字节 = 36 B 头部 + 6 × float32（三轴陀螺 + 三轴加速度）\n(%s)"
                 % os.path.basename(args.pcap), fontsize=14)

    # ---------------- ① 布局 ----------------
    ax = fig.add_subplot(gs[0])
    ax.set_title("① 整体布局：载荷 60 字节", fontsize=11, loc="left")
    ax.add_patch(Rectangle((0, 0), 36, 1, facecolor=C_HEAD, edgecolor="#333", lw=1.2))
    ax.text(18, 0.5, "36 B", ha="center", va="center", fontsize=9, fontweight="bold")
    for i in range(6):
        ax.add_patch(Rectangle((36 + i * 4, 0), 4, 1, facecolor=FILLS[i], edgecolor="#666", lw=0.8))
        ax.text(36 + i * 4 + 2, 0.5, "f%d" % (i + 1), ha="center", va="center", fontsize=9)
    ax.annotate("头部 36 B", xy=(18, 1.0), xytext=(18, 1.9), fontsize=10,
                ha="center", arrowprops=dict(arrowstyle="->", lw=0.8))
    ax.annotate("6 个 float32 × 4 B = 24 B", xy=(48, 1.0), xytext=(50, 1.9), fontsize=10,
                arrowprops=dict(arrowstyle="->", lw=0.8))
    ax.set_xlim(-2, 62)
    ax.set_ylim(-0.1, 2.3)
    ax.set_xlabel("字节偏移")
    ax.set_yticks([])
    ax.grid(axis="x", alpha=0.3)

    # ---------------- ② 头部 ----------------
    ax = fig.add_subplot(gs[1])
    ax.set_title("② 头部 36 字节逐字段", fontsize=11, loc="left")
    fields = [
        (1, "版本", "0x%02x" % p0[0], "[0]"),
        (2, "长度", "%d" % ln, "[1:3]"),
        (2, "保留", p0[3:5].hex(" "), "[3:5]"),
        (2, "条数", "%d" % cnt, "[5:7]"),
        (2, "包序号", "%d" % seq, "[7:9]"),
        (2, "类型", p0[9:11].hex(" "), "[9:11]"),
        (13, "保留（全 0）", "", "[11:24]"),
        (4, "未知（随包变化）", p0[24:28].hex(" "), "[24:28]"),
        (8, "设备时间戳 (ns)", "%.6f s" % (dev_ns / 1e9), "[28:36]"),
    ]
    x = 0
    for w, name, value, off in fields:
        box(ax, x, w, 0, 1, C_HEAD, name, value, off, fs=8, fs2=9)
        x += w
    ax.set_xlim(-0.3, 36.3)
    ax.set_ylim(-0.35, 1.45)
    ax.axis("off")

    # ---------------- ③ 6 个 float32 ----------------
    ax = fig.add_subplot(gs[2])
    ax.set_title("③ 6 个 float32：低字节在前（小端），这里是一个包里的实际值", fontsize=11, loc="left")
    for i in range(6):
        x0 = i * 4
        ax.add_patch(Rectangle((x0, 0), 4, 1, facecolor=FILLS[i], edgecolor="#333", lw=1.0))
        ax.text(x0 + 2, 0.84, NAMES[i], ha="center", va="center", fontsize=9)
        ax.text(x0 + 2, 0.58, "%.4f %s" % (vals0[i], UNITS[i]),
                ha="center", va="center", fontsize=9, fontweight="bold")
        ax.text(x0 + 2, 0.28, p0[36 + i * 4:40 + i * 4].hex(" "),
                ha="center", va="center", family="monospace", fontsize=9)
        ax.text(x0 + 2, -0.08, "[%d:%d]" % (36 + i * 4, 40 + i * 4),
                ha="center", va="top", fontsize=8, color="#555")
    ax.set_xlim(-0.2, 24.2)
    ax.set_ylim(-0.4, 1.25)
    ax.axis("off")

    # ---------------- ④ 时序：陀螺 ----------------
    t = [pk[0] - ts0 for pk in packets]
    ax = fig.add_subplot(gs[3])
    for i in range(3):
        ax.plot(t, [pk[2][i] for pk in packets], lw=1.0, color=COLORS[i], label=NAMES[i])
    ax.set_title("④ 陀螺三轴时序（%d 个包，%.2f 秒）  单位 rad/s" % (len(packets), t[-1]))
    ax.set_ylabel("rad/s")
    ax.legend(loc="upper right", ncol=3, fontsize=9)
    ax.grid(alpha=0.3)

    # ---------------- ⑤ 时序：加速度 ----------------
    ax = fig.add_subplot(gs[4])
    for i in range(3, 6):
        ax.plot(t, [pk[2][i] for pk in packets], lw=1.0, color=COLORS[i], label=NAMES[i])
    ax.set_title("⑤ 加速度三轴时序  单位 g（z 轴稳定在 1 g，说明设备静止且水平）")
    ax.set_xlabel("时间 (s)")
    ax.set_ylabel("g")
    ax.legend(loc="upper right", ncol=3, fontsize=9)
    ax.grid(alpha=0.3)

    fig.savefig(args.out, dpi=150, bbox_inches="tight")
    print("已保存:", args.out, "%.0f KB" % (os.path.getsize(args.out) / 1024))
    for i in range(6):
        col = [pk[2][i] for pk in packets]
        print("  %-8s 均值 %+.4f  标准差 %.5f  范围 [%+.4f, %+.4f] %s"
              % (NAMES[i], statistics.mean(col), statistics.pstdev(col),
                 min(col), max(col), UNITS[i]))


if __name__ == "__main__":
    main()
