#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
把雷达点云 CSV 渲染成“感知结果图”风格：深色底、按高度着色、无坐标轴、可叠加 3D 框。

输入：LIDAR\\*_radar.csv（列：timestamp, range, ..., x, y, z, ...）
      可选 --boxes boxes.csv（列：cx,cy,cz,dx,dy,dz,yaw，单位米/度）
data：photo\\<名>_render.png

用法：
    python render_pointcloud.py                              # 用文件顶部 START_S / END_S
    python render_pointcloud.py --start 2 --end 3            # 直接指定起止秒
    python render_pointcloud.py --start 2 --duration 0.5     # 也可用“起点 + 时长”
    python render_pointcloud.py "LIDAR\\yyy_radar.csv" --stride 8 --view 8,-70
    python render_pointcloud.py --boxes boxes.csv
"""

from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# ---------------- 路径配置（输入 + data） ----------------
DEFAULT_CSV = r"E:\pycharm project\pacp——csv\LIDAR\7777_radar.csv"
OUTPUT_DIR = r"E:\pycharm project\pacp——csv\photo"

# ---------------- 时间窗（直接改这两行，命令行可临时覆盖） ----------------
START_S = 10.0        # 从第几秒开始（相对第一行的时间戳）
END_S = 11.0          # 到第几秒结束；写 None 表示一直读到文件末尾

BG = "#05070f"            # 深色背景
POINT_CMAP = "turbo"      # 高度着色：蓝 → 青 → 绿 → 黄 → 红
BOX_COLOR = "#ff2b2b"     # 3D 框颜色


def load_points(path, stride=3, max_points=300000, start=0.0, end=None):
    """读 CSV 的 timestamp/x/y/z，保留 [start, end] 秒区间（end=None 表示到文件尾）。"""
    import csv
    ts, xs, ys, zs = [], [], [], []
    t0 = None
    with open(path, encoding="utf-8-sig", newline="") as fh:
        for i, row in enumerate(csv.DictReader(fh)):
            try:
                t = float(row["timestamp"])
                x = float(row["x"]); y = float(row["y"]); z = float(row["z"])
            except (KeyError, ValueError):
                continue
            if t0 is None:
                t0 = t
            if t - t0 < start:                       # 还没到起点
                continue
            if end is not None and t - t0 > end:     # 过了终点，收工
                break
            if i % stride:
                continue
            ts.append(t); xs.append(x); ys.append(y); zs.append(z)
            if len(xs) >= max_points:
                break
    return (np.asarray(ts), np.asarray(xs), np.asarray(ys), np.asarray(zs))


def draw_box(ax, cx, cy, cz, dx, dy, dz, yaw_deg):
    """在 3D 轴上画一个立方体线框（yaw 绕 z 轴）。"""
    hx, hy, hz = dx / 2, dy / 2, dz / 2
    yaw = np.radians(yaw_deg)
    c, s = np.cos(yaw), np.sin(yaw)
    idx = [(0, 1), (0, 2), (0, 4), (1, 3), (1, 5), (2, 3),
           (2, 6), (3, 7), (4, 5), (4, 6), (5, 7), (6, 7)]
    corners = []
    for sx in (-1, 1):
        for sy in (-1, 1):
            for sz in (-1, 1):
                px, py = sx * hx, sy * hy
                corners.append((cx + px * c - py * s, cy + px * s + py * c, cz + sz * hz))
    for a, b in idx:
        ax.plot([corners[a][0], corners[b][0]],
                [corners[a][1], corners[b][1]],
                [corners[a][2], corners[b][2]], color=BOX_COLOR, lw=1.8)


def load_boxes(path):
    import csv
    out = []
    if not path or not os.path.isfile(path):
        return out
    with open(path, encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            try:
                out.append(tuple(float(row[k]) for k in
                                 ("cx", "cy", "cz", "dx", "dy", "dz", "yaw")))
            except (KeyError, ValueError):
                continue
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="点云 CSV → 感知结果风格渲染图")
    ap.add_argument("csv", nargs="?", default=DEFAULT_CSV)
    ap.add_argument("-o", "--out", default=None)
    ap.add_argument("--stride", type=int, default=3, help="每 N 个点取 1 个")
    ap.add_argument("--start", type=float, default=None,
                    help="起始秒（相对第一行）；默认取文件顶部 START_S = %s" % START_S)
    ap.add_argument("--end", type=float, default=None,
                    help="结束秒；默认取文件顶部 END_S = %s" % END_S)
    ap.add_argument("--duration", type=float, default=None,
                    help="取多长（秒）；给了它就等价于 end = start + duration")
    ap.add_argument("--boxes", default=None, help="3D 框 CSV（cx,cy,cz,dx,dy,dz,yaw）")
    ap.add_argument("--view", default="12,-65", help="视角：仰角,方位角")
    ap.add_argument("--size", type=float, default=1.0, help="点大小")
    args = ap.parse_args(argv)

    # 时间窗优先级：命令行 > 文件顶部常量
    start = START_S if args.start is None else args.start
    if args.duration is not None:
        end = start + args.duration
    elif args.end is not None:
        end = args.end
    else:
        end = END_S

    path = os.path.abspath(args.csv)
    if not os.path.isfile(path):
        print("找不到文件: %s" % path, file=sys.stderr)
        return 2

    ts, x, y, z = load_points(path, max(1, args.stride), start=start, end=end)
    if len(x) == 0:
        print("时间窗 [%s, %s] 内没有点，放宽 --start/--end" % (start, end), file=sys.stderr)
        return 1

    out = args.out or os.path.join(
        OUTPUT_DIR, os.path.splitext(os.path.basename(path))[0] + "_render.png")
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)

    # 用分位数收紧视窗，避免少量远点把画面撑空
    xl = np.percentile(x, [1, 99]); yl = np.percentile(y, [1, 99]); zl = np.percentile(z, [2, 98])
    xr, yr, zr = xl[1] - xl[0], yl[1] - yl[0], max(zl[1] - zl[0], 1.0)

    elev, azim = (float(v) for v in args.view.split(","))
    fig = plt.figure(figsize=(12, 7.5), facecolor=BG)
    ax = fig.add_subplot(111, projection="3d", facecolor=BG)
    ax.set_facecolor(BG)

    ax.scatter(x, y, z, c=z, cmap=POINT_CMAP, vmin=zl[0], vmax=zl[1],
               s=args.size, alpha=0.9, depthshade=False, edgecolors="none")
    for b in load_boxes(args.boxes):
        draw_box(ax, *b)

    ax.view_init(elev=elev, azim=azim)
    ax.set_xlim(*xl)
    ax.set_ylim(*yl)
    ax.set_zlim(zl[0] - 1, zl[1] + 1)
    ax.set_box_aspect((xr, yr, zr * 2.2))
    ax.set_axis_off()
    ax.grid(False)
    for axis in (ax.xaxis, ax.yaxis, ax.zaxis):
        axis.pane.set_visible(False)
        axis.line.set_visible(False)
    fig.subplots_adjust(left=0, right=1, bottom=0, top=1)

    fig.savefig(out, dpi=170, facecolor=BG)
    print("已保存: %s (%.0f KB)  点数 %d  时间窗 [%s, %s] 秒  x[%.0f,%.0f] y[%.0f,%.0f]"
          % (out, os.path.getsize(out) / 1024, len(x), start, end,
             xl[0], xl[1], yl[0], yl[1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
