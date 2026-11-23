#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
把导出后的 CSV 画成图（不需要 pcap）。
自动按表头判断是「雷达点云表」还是「IMU 表」，各画一张：

雷达表（timestamp, range, ..., x, y, z, ...）：
    ① 俯视 x-y   ② 侧视 x-z   ③ 距离-反射率分布   ④ 每帧点数分布
IMU 表（timestamp, pitch, roll, ..., angular_velocity_x, ...）：
    ① 角速度三轴  ② 加速度三轴  ③ 姿态角  ④ 采样间隔分布

用法：
    python plot_csv.py "LIDAR\\99999_radar.csv"
    python plot_csv.py "IMU\\99999_imu.csv"
    python plot_csv.py "LIDAR\\yyy_radar.csv" --max-rows 500000   # 大文件抽样
默认输出到 <项目>\\photo\\<输入名>_csv.png
"""

from __future__ import annotations

import argparse
import csv
import os
import sys

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

# ---------------- 路径配置（data） ----------------
OUTPUT_DIR = r"E:\pycharm project\pacp——csv\photo"


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


def read_csv(path, max_rows=400_000):
    """返回 (表头, {列名: np.array}, 总行数)。用 stride 抽样，避免爆内存。"""
    with open(path, encoding="utf-8-sig", newline="") as fh:
        reader = csv.reader(fh)
        head = next(reader)
        total = 0
        rows = []
        for row in reader:
            total += 1
            if total <= max_rows:
                rows.append(row)
            elif total % 50 == 0 and len(rows) < max_rows * 2:   # 超出后继续稀疏抽样
                rows.append(row)
    cols = {}
    for i, name in enumerate(head):
        vals = []
        for r in rows:
            if i < len(r) and r[i] != "":
                try:
                    vals.append(float(r[i]))
                except ValueError:
                    pass
        cols[name] = np.asarray(vals, dtype=float)
    return head, cols, total


def plot_radar(head, c, path, out, total):
    ts = c.get("timestamp")
    x, y, z = c["x"], c["y"], c["z"]
    rng = c.get("range", np.sqrt(x ** 2 + y ** 2 + z ** 2))
    pw = c.get("power", np.zeros_like(x))

    fig, axes = plt.subplots(2, 2, figsize=(13.5, 10))
    fig.suptitle("雷达点云 CSV：%s（共 %d 行，抽 %d 点绘制）"
                 % (os.path.basename(path), total, len(x)), fontsize=13)

    ax = axes[0, 0]
    sc = ax.scatter(x, y, c=pw, s=0.3, cmap="turbo", alpha=0.6, edgecolors="none")
    ax.set_title("① 俯视 x-y（颜色=power）")
    ax.set_xlabel("x (m)")
    ax.set_ylabel("y (m)")
    ax.set_aspect("equal", adjustable="datalim")
    ax.grid(alpha=0.3)
    fig.colorbar(sc, ax=ax, label="power")

    ax = axes[0, 1]
    sc = ax.scatter(x, z, c=rng, s=0.3, cmap="viridis", alpha=0.6, edgecolors="none")
    ax.set_title("② 侧视 x-z（颜色=距离）")
    ax.set_xlabel("x (m)")
    ax.set_ylabel("z (m)")
    ax.axhline(0, color="gray", lw=0.6, ls="--")
    ax.grid(alpha=0.3)
    fig.colorbar(sc, ax=ax, label="距离 (m)")

    ax = axes[1, 0]
    hb = ax.hexbin(rng, pw, gridsize=60, cmap="magma", mincnt=1, bins="log")
    ax.set_title("③ 距离-power 分布（对数色标）")
    ax.set_xlabel("距离 (m)")
    ax.set_ylabel("power")
    fig.colorbar(hb, ax=ax, label="点数(log10)")

    ax = axes[1, 1]
    if ts is not None and len(ts) == len(x):
        uniq, counts = np.unique(ts, return_counts=True)
        ax.hist(counts, bins=40, color="#6d9eeb", edgecolor="white")
        ax.set_title("④ 每个时间戳的点数分布（均值 %.1f，共 %d 帧）"
                     % (counts.mean(), len(uniq)))
        ax.set_xlabel("一帧的点数")
        ax.set_ylabel("帧数")
    else:
        ax.hist(z, bins=60, color="#93c47d", edgecolor="white")
        ax.set_title("④ z 分布")
        ax.set_xlabel("z (m)")
    ax.grid(alpha=0.3)

    fig.tight_layout(rect=(0, 0, 1, 0.95))
    fig.savefig(out, dpi=150)
    print("雷达表：%d 行，抽 %d 点 → %s" % (total, len(x), out))


def plot_imu(head, c, path, out, total):
    ts = c["timestamp"]
    t = ts - ts[0] if len(ts) else ts
    g = [c.get("angular_velocity_%s" % k, np.array([])) for k in "xyz"]
    a = [c.get("linear_acceleration_%s" % k, np.array([])) for k in "xyz"]

    fig, axes = plt.subplots(2, 2, figsize=(13.5, 10))
    fig.suptitle("IMU CSV：%s（共 %d 行）" % (os.path.basename(path), total), fontsize=13)

    ax = axes[0, 0]
    for k, v, col in zip("xyz", g, ("#e06666", "#93c47d", "#6d9eeb")):
        if len(v) == len(t):
            ax.plot(t, v, lw=0.9, color=col, label="角速度 %s" % k)
    ax.set_title("① 角速度 (rad/s)")
    ax.set_xlabel("时间 (s)")
    ax.legend(ncol=3, fontsize=8)
    ax.grid(alpha=0.3)

    ax = axes[0, 1]
    for k, v, col in zip("xyz", a, ("#e69138", "#8e7cc3", "#76a5af")):
        if len(v) == len(t):
            ax.plot(t, v, lw=0.9, color=col, label="加速度 %s" % k)
    ax.set_title("② 加速度 (m/s²)")
    ax.set_xlabel("时间 (s)")
    ax.legend(ncol=3, fontsize=8)
    ax.grid(alpha=0.3)

    ax = axes[1, 0]
    for k, col in zip(("pitch", "roll"), ("#674ea7", "#38761d")):
        v = c.get(k)
        if v is not None and len(v) == len(t):
            ax.plot(t, v, lw=0.9, color=col, label=k)
    ax.set_title("③ 姿态角 (度)　yaw 无磁力计，留空")
    ax.set_xlabel("时间 (s)")
    ax.legend(ncol=2, fontsize=8)
    ax.grid(alpha=0.3)

    ax = axes[1, 1]
    if len(ts) > 2:
        d = np.diff(ts) * 1000.0
        d = d[(d > 0) & (d < 100)]
        ax.hist(d, bins=50, color="#45818e", edgecolor="white")
        ax.set_title("④ 采样间隔分布（中位 %.2f ms ≈ %.0f Hz）" % (np.median(d), 1000.0 / np.median(d)))
        ax.set_xlabel("间隔 (ms)")
        ax.set_ylabel("次数")
    ax.grid(alpha=0.3)

    fig.tight_layout(rect=(0, 0, 1, 0.95))
    fig.savefig(out, dpi=150)
    print("IMU 表：%d 行 → %s" % (total, out))


def main(argv=None):
    ap = argparse.ArgumentParser(description="把导出后的 CSV 画成图")
    ap.add_argument("csv", nargs="?", default=r"E:\pycharm project\pacp——csv\IMU\99999_IMU.csv")
    ap.add_argument("-o", "--out", default=None, help="data PNG，默认 photo\\<名>_csv.png")
    ap.add_argument("--max-rows", type=int, default=400000, help="最多读取多少行（大文件会抽样）")
    args = ap.parse_args(argv)

    setup_cjk_font()
    path = os.path.abspath(args.csv)
    if not os.path.isfile(path):
        print("找不到文件: %s" % path, file=sys.stderr)
        return 2

    out = args.out or os.path.join(
        OUTPUT_DIR, os.path.splitext(os.path.basename(path))[0] + "_csv.png")
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)

    head, cols, total = read_csv(path, args.max_rows)
    if {"x", "y", "z"} <= set(head):
        plot_radar(head, cols, path, out, total)
    elif any(h.startswith("angular_velocity") for h in head):
        plot_imu(head, cols, path, out, total)
    else:
        print("认不出表结构，表头是：%s" % ", ".join(head), file=sys.stderr)
        return 1
    print("已保存: %s (%.0f KB)" % (out, os.path.getsize(out) / 1024))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
