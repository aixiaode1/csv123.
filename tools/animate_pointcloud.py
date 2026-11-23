#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
把点云 CSV 做成“实时运动”的动画：按时间滑动窗口逐帧渲染，输出 MP4（无 imageio 时
自动改为 GIF，流式写）。

输入：LIDAR\\*_radar.csv 或 deskew\\data\\*_radar_deskew.csv（15 列 WaterScenes 格式）
输出：photo\\<名>_anim.mp4（或 .gif）

用法：
    python animate_pointcloud.py                          # 用文件顶部的时间窗常量
    python animate_pointcloud.py --start 0 --end 6        # 指定起止秒
    python animate_pointcloud.py "LIDAR\\yyy_radar.csv" --start 100 --end 130 --fps 20
    python animate_pointcloud.py --window 0.3 --fps 10    # 每帧显示 0.3 秒的数据
    python animate_pointcloud.py --dpi 80 --stride 2      # 降分辨率/抽点，更快更小

【2026-11-23 优化】（相对初版）
    · 逐帧流式写盘：不再把所有帧攒在内存里——150 s 全量 ~2250 帧 × 2.7 MB 曾要
      ~6 GB 内存，现在恒定 O(1)；
    · CSV 快速读取：跳过段只解析时间戳（不拆整行）、去掉 DictReader，
      6666 全量 753 万行的加载快数倍；
    · 每帧取点用 searchsorted 二分切片，代替对全数组的布尔掩码（O(log n)/帧）；
    · GIF 回退改用 matplotlib PillowWriter 流式写（不再累积 PIL 图像列表）；
    · 时间戳排序自检（乱序时一次性排序，searchsorted 才安全）；
    · 进度 + ETA 打印、--dpi 控制分辨率、结束释放图像。

【2026-11-23 二次优化 · 消除卡顿】
    · 按真实时间戳切帧：去畸变数据只有 10 Hz（时间戳按 0.1 s 量化），
      15 fps + 0.2 s 窗会出约 1/3 重复帧（帧间像素差≈0）且有整窗空档；
      现在一帧 = N 个完整数据窗（N = round(拖尾/数据间隔)），帧率默认等于
      数据帧率，画面每次前进恰好一个窗；
    · 输出链改为 imageio → OpenCV → GIF 三级回退（没装 imageio 也能出 mp4）；
    · fps 高于数据帧率时给出提示。
"""

from __future__ import annotations

import argparse
import os
import sys
import time

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# ---------------- 路径配置（输入 + 输出） ----------------
DEFAULT_CSV = r"E:\pycharm project\pacp——csv\cluster\data\yyy_radar_deskew_cluster.csv"
OUTPUT_DIR = r"E:\pycharm project\pacp——csv\photo"

# ---------------- 时间窗 & 动画参数（改这里即可） ----------------
START_S = 0          # 起始秒
END_S = 20         # 结束秒；None = 读到文件末
WINDOW_S = 0.20        # 每帧显示多长时间内的点（"拖尾"长度）
FPS = 15               # 帧率
STRIDE = 1             # 每 N 个文件行取 1 个
DPI = 100              # 渲染分辨率（帧尺寸 = 12×7.5 英寸 × DPI）

BG = "#05070f"
POINT_CMAP = "turbo"


def load(path, start, end, stride):
    """读 [start, end] 秒内的点。

    跳过段只解析行首时间戳（不 split 整行、不用 DictReader），所以在动辄几百万行
    的文件上找一段数据也只要几秒。返回 (t, x, y, z, 坏行数)，t 已换成相对秒。
    """
    ts, xs, ys, zs = [], [], [], []
    t0 = None
    bad = 0
    with open(path, encoding="utf-8-sig", newline="") as fh:
        header = fh.readline().rstrip("\r\n").split(",")
        try:
            ci = header.index("timestamp")
            cx, cy, cz = (header.index(c) for c in ("x", "y", "z"))
        except ValueError:
            ci, cx, cy, cz = 0, 6, 7, 8          # 兜底：WaterScenes 15 列默认位
        fast = (ci == 0)                          # 时间戳在第 0 列才能"只看行首"
        need = max(ci, cx, cy, cz)
        i = -1                                    # 文件行号（stride 按它算）
        for line in fh:
            i += 1
            if fast:
                k = line.find(",")
                if k < 0:
                    bad += 1
                    continue
                try:
                    t = float(line[:k])
                except ValueError:
                    bad += 1
                    continue
            else:
                p = line.split(",")
                if len(p) <= need:
                    bad += 1
                    continue
                try:
                    t = float(p[ci])
                except ValueError:
                    bad += 1
                    continue
            if t0 is None:
                t0 = t
            rel = t - t0
            if rel < start:
                continue
            if end is not None and rel > end:
                break                              # 时间升序，后面不会再有了
            if stride > 1 and i % stride:
                continue
            if fast:                               # 到这里才拆整行
                p = line.split(",")
                if len(p) <= need:
                    bad += 1
                    continue
            try:
                x = float(p[cx]); y = float(p[cy]); z = float(p[cz])
            except ValueError:
                bad += 1
                continue
            ts.append(rel); xs.append(x); ys.append(y); zs.append(z)

    t = np.asarray(ts); x = np.asarray(xs); y = np.asarray(ys); z = np.asarray(zs)
    if len(t) > 1 and (np.diff(t) < 0).any():      # searchsorted 的安全前提
        order = np.argsort(t, kind="stable")
        print("  （时间戳乱序，已排序）")
        t, x, y, z = t[order], x[order], y[order], z[order]
    return t, x, y, z, bad


def main(argv=None):
    ap = argparse.ArgumentParser(description="点云 CSV → 实时运动动画")
    ap.add_argument("csv", nargs="?", default=DEFAULT_CSV)
    ap.add_argument("-o", "--out", default=None)
    ap.add_argument("--start", type=float, default=None)
    ap.add_argument("--end", type=float, default=None)
    ap.add_argument("--window", type=float, default=None,
                    help="拖尾长度（秒），自动吸附到数据帧的整数倍")
    ap.add_argument("--fps", type=int, default=None,
                    help="输出帧率，默认 = 数据帧率（去畸变数据是 10 Hz）")
    ap.add_argument("--stride", type=int, default=None)
    ap.add_argument("--dpi", type=int, default=None, help="渲染分辨率，默认 %d" % DPI)
    ap.add_argument("--view", default="12,-65")
    args = ap.parse_args(argv)

    start = START_S if args.start is None else args.start
    end = END_S if args.end is None else args.end
    window = WINDOW_S if args.window is None else args.window
    fps = FPS if args.fps is None else args.fps
    stride = STRIDE if args.stride is None else args.stride
    dpi = DPI if args.dpi is None else args.dpi

    path = os.path.abspath(args.csv)
    if not os.path.isfile(path):
        print("找不到文件: %s" % path, file=sys.stderr)
        return 2

    t_load = time.time()
    ts, x, y, z, bad = load(path, start, end, max(1, stride))
    if len(ts) == 0:
        print("时间窗 [%s, %s] 内没有点" % (start, end), file=sys.stderr)
        return 1
    span = float(ts.max())
    t_first = float(ts[0])
    print("加载 %d 个点（%.1f s 数据，坏行 %d），耗时 %.1f s"
          % (len(ts), span - t_first, bad, time.time() - t_load))

    out_path = args.out or os.path.join(
        OUTPUT_DIR, os.path.splitext(os.path.basename(path))[0] + "_anim.mp4")
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)

    # 全程固定的坐标范围，画面才不会跳
    xl = np.percentile(x, [1, 99]); yl = np.percentile(y, [1, 99]); zl = np.percentile(z, [2, 98])
    elev, azim = (float(v) for v in args.view.split(","))

    fig = plt.figure(figsize=(12, 7.5), facecolor=BG, dpi=dpi)
    ax = fig.add_subplot(111, projection="3d", facecolor=BG)
    ax.set_facecolor(BG)
    ax.view_init(elev=elev, azim=azim)
    ax.set_xlim(*xl); ax.set_ylim(*yl); ax.set_zlim(zl[0] - 1, zl[1] + 1)
    ax.set_box_aspect((xl[1] - xl[0], yl[1] - yl[0], max(zl[1] - zl[0], 1) * 2.2))
    ax.set_axis_off()
    for axis in (ax.xaxis, ax.yaxis, ax.zaxis):
        axis.pane.set_visible(False)
        axis.line.set_visible(False)
    fig.subplots_adjust(left=0, right=1, bottom=0, top=0.96)

    title = fig.text(0.5, 0.97, "", ha="center", va="top", color="#d0d8f0", fontsize=13)
    sc = ax.scatter([], [], [], c=[], cmap=POINT_CMAP, vmin=zl[0], vmax=zl[1],
                    s=1.2, depthshade=False, edgecolors="none")

    # ---- 切帧：区分「一窗一个时间戳」与「每点一个时间戳」两种数据 ----
    uniq = np.unique(ts)
    per_ts = len(ts) / max(len(uniq), 1)          # 每个时间戳平均带多少点
    if per_ts >= 3.0:                             # ① 去畸变产物：时间戳按窗量化
        dt = float(np.median(np.diff(uniq))) if len(uniq) > 1 else 0.1
        n_win = max(1, int(round(window / dt)))   # 拖尾吸附到整数个窗
        starts = np.searchsorted(ts, uniq, "left").tolist() + [len(ts)]
        frames = [(float(uniq[k]), starts[k], starts[k + n_win])
                  for k in range(len(uniq) - n_win)]
        if args.fps is None:                      # 默认按数据自身节奏出片
            fps = max(1, int(round(1.0 / dt)))
        print("数据 %d 个时间戳（间隔 %.3f s → %.0f Hz）；拖尾 %.2f s = %d 窗/帧；"
              "输出 %d 帧 @ %d fps" % (len(uniq), dt, 1.0 / dt, n_win * dt, n_win, len(frames), fps))
        if fps > 1.0 / dt + 1e-6:
            print("  注意：--fps %d 高于数据帧率 %.0f Hz，会出现重复帧（看着卡顿），建议 %d"
                  % (fps, 1.0 / dt, int(round(1.0 / dt))))
    else:                                         # ② 逐点时间戳（原始雷达点云）：滑窗切帧
        step = 1.0 / fps
        frame_t = np.arange(t_first, max(span - window, t_first) + step, step)
        frames = [(float(t), int(np.searchsorted(ts, t, "left")),
                   int(np.searchsorted(ts, t + window, "left"))) for t in frame_t]
        print("数据为逐点时间戳（%.0f 点/秒）；按 %.2f s 滑窗切帧 → %d 帧 @ %d fps"
              % (len(ts) / max(span - t_first, 1e-9), window, len(frames), fps))
    if not frames:
        print("数据太短，凑不出一帧", file=sys.stderr)
        return 1
    n = len(frames) - 1

    t_wall = time.time()
    prog = {"last": -1}

    def draw_frame(k):
        """第 k 帧：按预计算的 [t, i0, i1] 切片，不扫全数组。"""
        t, i0, i1 = frames[k]
        sc._offsets3d = (x[i0:i1], y[i0:i1], z[i0:i1])
        sc.set_array(z[i0:i1])
        title.set_text("t = %6.2f s      %d points" % (t, i1 - i0))
        if (k % 100 == 0 or k == n) and k != prog["last"]:   # FuncAnimation 会多探一帧
            prog["last"] = k
            el = time.time() - t_wall
            eta = el / (k + 1) * (n - k)
            print("  帧 %d/%d  t=%.2f s  %d 点  已用 %.0f s  剩余 ~%.0f s"
                  % (k + 1, n + 1, t, i1 - i0, el, eta))

    frame_count = n + 1
    out = None
    try:                                            # ① imageio（+ffmpeg）→ MP4
        import imageio.v2 as imageio
        with imageio.get_writer(out_path, fps=fps, macro_block_size=None) as w:
            for k in range(frame_count):
                draw_frame(k)
                fig.canvas.draw()
                w.append_data(np.asarray(fig.canvas.buffer_rgba())[:, :, :3].copy())
        out = out_path
    except Exception as exc:
        print("（imageio 不可用：%s）" % exc)
        if out_path and os.path.exists(out_path):
            try: os.remove(out_path)                # 清掉半截的 mp4
            except OSError:
                pass
        try:                                        # ② OpenCV → MP4
            import cv2
            out = os.path.splitext(out_path)[0] + ".mp4"
            vw = cv2.VideoWriter(out, cv2.VideoWriter_fourcc(*"mp4v"), fps,
                                 (int(12 * dpi), int(7.5 * dpi)))
            for k in range(frame_count):
                draw_frame(k)
                fig.canvas.draw()
                rgb = np.asarray(fig.canvas.buffer_rgba())[:, :, :3]
                vw.write(cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
            vw.release()
            if not os.path.exists(out) or os.path.getsize(out) == 0:
                raise IOError("VideoWriter 没写出内容")
        except Exception as exc2:                   # ③ 退化为 GIF（PillowWriter 流式）
            print("（cv2 也不可用：%s → 改存 GIF）" % exc2)
            out = os.path.splitext(out_path)[0] + ".gif"
            from matplotlib import animation
            anim = animation.FuncAnimation(fig, draw_frame, frames=frame_count)
            anim.save(out, writer=animation.PillowWriter(fps=fps))
            del anim
            if frame_count > 500:
                print("  提示：帧数较多 GIF 会很大，可用 --dpi 80 / --window 0.1 / --stride 2 缩小")

    plt.close(fig)
    print("已保存: %s (%.1f MB)  %d 帧  渲染 %.0f s" % (
        out, os.path.getsize(out) / 1024 / 1024, frame_count, time.time() - t_wall))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
