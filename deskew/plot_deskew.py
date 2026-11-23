#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
画「IMU 预积分去畸变前后对比图」，读三份现成文件（不需要 pcap）：

    ① 原始 radar CSV        LIDAR\\<名>_radar.csv
    ② 去畸变产物            deskew\\data\\<名>_radar_deskew.csv     （imu.py 生成）
    ③ 逐窗统计              deskew\\data\\<名>_radar_deskew_windows.csv

六联图：
    ① 处理段全程俯视：原始（灰）vs 去畸变后（红）
    ② 旋转最猛的一个补偿窗·去畸变前：点按 Δt（距窗起始的时延）着色，
       拖影表现为「时间彩虹」
    ③ 同一窗·去畸变后：同一色标、同一视野——彩虹弧被收拢/拉直
    ④ 每窗旋转角与点位移随时间
    ⑤ 旋转角 vs 点位移散点（位移 ≈ 角 × 距离，应强相关）
    ⑥ 每窗位移均值直方图

用法：
    python "deskew\\plot_deskew.py" -i "LIDAR\\6666_radar.csv"
    python "deskew\\plot_deskew.py" -i "LIDAR\\6666_radar.csv" --win 120
输出：photo\\<名>_deskew_cmp.png
依赖：numpy、matplotlib（跑 tools\\ 画图脚本的那个 Python）
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
PROJECT_ROOT = os.path.dirname(_HERE)
DESKW_DIR = os.path.join(_HERE, "data")     # imu.py 的产物目录

# ---------------- 路径配置（输出） ----------------
OUTPUT_DIR = os.path.join(PROJECT_ROOT, "photo")

# ---------------- 设备规格（Livox Mid-360L） ----------------
SENSOR = "Livox Mid-360L"
MAX_RANGE = 70.0        # 最大量程（米）：图① 用虚线圈标出，便于判断拖影/杂点距离


def setup_cjk_font():
    for name in ("Microsoft YaHei", "SimHei", "SimSun", "DengXian"):
        try:
            font_manager.findfont(font_manager.FontProperties(family=name),
                                  fallback_to_default=False)
            plt.rcParams["font.sans-serif"] = [name]
            plt.rcParams["axes.unicode_minus"] = False
            return name
        except Exception:
            continue
    return None


def read_range(path, t_lo, t_hi, stride=1):
    """读 [t_lo, t_hi] 内的点（ts, x, y, z）。文件按 ts 升序，可早停；
    跳过段只解析时间戳，不全行 split。返回 4 个 np.array。"""
    ts, xs, ys, zs = [], [], [], []
    i = 0
    with open(path, encoding="utf-8-sig", newline="") as fh:
        next(fh, None)
        for line in fh:
            line = line.rstrip("\r\n")
            if not line:
                continue
            k = line.find(",")
            if k <= 0:
                continue
            try:
                t = float(line[:k])
            except ValueError:
                continue
            if t < t_lo:
                continue
            if t > t_hi:
                break
            i += 1
            if (i - 1) % stride:
                continue
            parts = line.split(",")
            if len(parts) < 9:
                continue
            try:
                x, y, z = float(parts[6]), float(parts[7]), float(parts[8])
            except ValueError:
                continue
            ts.append(t)
            xs.append(x)
            ys.append(y)
            zs.append(z)
    return (np.asarray(ts, float), np.asarray(xs, float),
            np.asarray(ys, float), np.asarray(zs, float))


def load_windows(path):
    cols = {"t_start": [], "t_ref": [], "points": [], "rot_deg": [],
            "disp_mean_m": [], "disp_max_m": []}
    with open(path, encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            for k in cols:
                cols[k].append(float(row[k]))
    return {k: np.asarray(v, float) for k, v in cols.items()}


def main(argv=None):
    ap = argparse.ArgumentParser(description="画去畸变前后对比图")
    ap.add_argument("-i", "--input", required=True,
                    help="原始雷达 CSV（LIDAR\\<名>_radar.csv）")
    ap.add_argument("--deskew", default=None,
                    help="deskew CSV，默认 <名>_radar_deskew.csv")
    ap.add_argument("--windows", default=None,
                    help="逐窗统计 CSV，默认 <名>_radar_deskew_windows.csv")
    ap.add_argument("-o", "--out", default=None,
                    help="输出 PNG，默认 photo\\<名>_deskew_cmp.png")
    ap.add_argument("--win", type=int, default=-1,
                    help="放大哪个窗（序号），默认自动选旋转角最大的")
    ap.add_argument("--stride", type=int, default=3,
                    help="① 全程图的点抽样步长，默认 3")
    args = ap.parse_args(argv)

    orig = os.path.abspath(args.input)
    stem = os.path.splitext(os.path.basename(orig))[0]
    dsk = args.deskew or os.path.join(DESKW_DIR, stem + "_deskew.csv")
    wincsv = args.windows or os.path.join(DESKW_DIR, stem + "_deskew_windows.csv")
    for p in (orig, dsk, wincsv):
        if not os.path.isfile(p):
            print("找不到文件: %s" % p, file=sys.stderr)
            return 2
    out = args.out or os.path.join(OUTPUT_DIR, stem + "_deskew_cmp.png")
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)

    setup_cjk_font()
    win = load_windows(wincsv)
    n_win = len(win["rot_deg"])
    if n_win == 0:
        print("逐窗统计是空的", file=sys.stderr)
        return 2
    W = float(np.median(np.diff(win["t_start"]))) if n_win > 1 else 0.1

    wi = int(np.argmax(win["rot_deg"])) if args.win < 0 else args.win
    t_start = win["t_start"][wi]
    t_ref = win["t_ref"][wi]
    rot_deg = win["rot_deg"][wi]
    disp_mean_cm = win["disp_mean_m"][wi] * 100

    # —— 放大窗：原始（按 ts 落在 [t_start, t_start+W)）与去畸变（ts=t_ref） ——
    _, ox, oy, oz = read_range(orig, t_start, t_start + W - 1e-9)
    _, dx, dy, dz = read_range(dsk, t_ref - 1e-6, t_ref + 1e-6)
    n = min(len(ox), len(dx))
    if n == 0:
        print("放大窗 %d 里没有点" % wi, file=sys.stderr)
        return 2
    if len(ox) != len(dx):
        print("注意：该窗两文件点数不一致（%d vs %d），按前 %d 个对齐"
              % (len(ox), len(dx), n), file=sys.stderr)
    ox, oy, oz = ox[:n], oy[:n], oz[:n]
    dx, dy, dz = dx[:n], dy[:n], dz[:n]
    # 视野框：以位移最大的点为中心，边长 ≈ 20×p99 位移
    d = np.sqrt((dx - ox) ** 2 + (dy - oy) ** 2 + (dz - oz) ** 2)
    k = int(np.argmax(d))
    half = float(np.clip(10.0 * np.percentile(d, 99), 1.0, 8.0))
    cx, cy = 0.5 * (ox[k] + dx[k]), 0.5 * (oy[k] + dy[k])
    dt_ms = (np.arange(n) * (W * 1000.0 / max(n - 1, 1)))   # 行序≈窗内时序

    # —— ① 全程段：原始段（同一时间范围） vs 去畸变全文件 ——
    seg_lo, seg_hi = win["t_start"][0], win["t_start"][-1] + W
    _, gx, gy, _ = read_range(orig, seg_lo, seg_hi, stride=args.stride)
    _, hx, hy, _ = read_range(dsk, -1.0, float("inf"), stride=args.stride)

    t0 = win["t_start"][0]
    fig, axes = plt.subplots(2, 3, figsize=(16.5, 10))
    fig.suptitle("IMU 预积分去畸变前后对比（%s）：%s（%d 窗 × %.2f s，"
                 "共 %.0f 点；ref=%s）" % (SENSOR, os.path.basename(orig), n_win, W,
                                          win["points"].sum(), "start"), fontsize=14)

    ax = axes[0, 0]
    ax.scatter(gx, gy, s=0.3, c="0.55", alpha=0.35, edgecolors="none",
               label="原始（%d 点抽样）" % len(gx))
    ax.scatter(hx, hy, s=0.3, c="#e06666", alpha=0.35, edgecolors="none",
               label="去畸变后（%d 点抽样）" % len(hx))
    ax.set_title("① 处理段俯视 x-y（灰=原始，红=deskew；虚线圈=%.0f m 量程）"
                 % MAX_RANGE)
    ax.set_xlabel("x (m)")
    ax.set_ylabel("y (m)")
    ax.set_aspect("equal", adjustable="datalim")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8, loc="upper right")
    ax.add_patch(plt.Circle((0.0, 0.0), MAX_RANGE, fill=False, ls="--",
                            lw=0.8, ec="0.65", zorder=0))
    ax.text(MAX_RANGE * 0.70, MAX_RANGE * 0.70, "%.0f m" % MAX_RANGE,
            fontsize=8, color="0.45", ha="left", va="bottom")

    for j, (ax, x, y, ttl) in enumerate((
            (axes[0, 1], ox, oy, "② 窗 %d 去畸变前（t=%.1fs，Δt 着色）" % (wi, t_start - t0)),
            (axes[0, 2], dx, dy, "③ 同一窗去畸变后（同一视野、同一色标）"))):
        sc = ax.scatter(x, y, s=1.0, c=dt_ms, cmap="turbo", edgecolors="none")
        ax.set_xlim(cx - half, cx + half)
        ax.set_ylim(cy - half, cy + half)
        ax.set_title(ttl)
        ax.set_xlabel("x (m)")
        ax.set_ylabel("y (m)")
        ax.set_aspect("equal", adjustable="box")
        ax.grid(alpha=0.3)
        if j == 0:
            fig.colorbar(sc, ax=[axes[0, 1], axes[0, 2]], pad=0.01,
                         label="窗内时延 Δt (ms)")
    axes[0, 1].text(0.02, 0.02,
                    "窗旋转 %.3f°，点位移均值 %.1f cm，p99 %.1f cm"
                    % (rot_deg, disp_mean_cm, np.percentile(d, 99) * 100),
                    transform=axes[0, 1].transAxes, fontsize=9,
                    bbox=dict(fc="white", alpha=0.8, ec="0.7"))

    ax = axes[1, 0]
    trel = win["t_ref"] - win["t_ref"][0]
    ax.plot(trel, win["rot_deg"], lw=1.1, color="#e06666", label="每窗旋转角")
    ax.set_title("④ 每窗旋转角 / 点位移均值")
    ax.set_xlabel("时间 (s)")
    ax.set_ylabel("旋转角 (°)")
    ax.grid(alpha=0.3)
    ax2 = ax.twinx()
    ax2.plot(trel, win["disp_mean_m"] * 100, lw=1.1, color="#6d9eeb",
             label="点位移均值 (cm)")
    ax2.set_ylabel("点位移均值 (cm)")
    ax.axvline(t_ref - win["t_ref"][0], color="gray", ls="--", lw=0.8)
    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, fontsize=8, loc="upper right")

    ax = axes[1, 1]
    ax.scatter(win["rot_deg"], win["disp_mean_m"] * 100, s=9,
               c="#674ea7", alpha=0.6, edgecolors="none")
    r = np.corrcoef(win["rot_deg"], win["disp_mean_m"] * 100)[0, 1]
    ax.set_title("⑤ 旋转角 vs 位移均值（r=%.3f；位移≈角×距离）" % r)
    ax.set_xlabel("每窗旋转角 (°)")
    ax.set_ylabel("点位移均值 (cm)")
    ax.grid(alpha=0.3)

    ax = axes[1, 2]
    ax.hist(win["disp_mean_m"] * 100, bins=30, color="#45818e",
            edgecolor="white")
    ax.set_title("⑥ 每窗点位移均值分布")
    ax.set_xlabel("位移均值 (cm)")
    ax.set_ylabel("窗数")
    ax.grid(alpha=0.3)

    fig.tight_layout(rect=(0, 0, 1, 0.95))
    fig.savefig(out, dpi=150)
    print("对比图完成：%s（放大窗 %d：旋转 %.3f°，位移均值 %.1f cm）"
          % (out, wi, rot_deg, disp_mean_cm))
    print("已保存: %s (%.0f KB)" % (out, os.path.getsize(out) / 1024))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
