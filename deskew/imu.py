#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
【deskew】IMU 预积分点云去畸变（运动补偿）。

问题在哪：
    Livox Mid-360L 是逐点扫描的：一帧 100 ms 内扫完一遍 360°×59°（非重复
    花瓣轨迹），帧内每个点的时间从 0 走到 100 ms。船在摇，所以帧内首尾的点是
    在两个不同姿态下测出来的（0.2°/窗 的船摇，100 ms 内在 70 m 处就是
    ≈0.9 m 的位置差）。把一帧的点直接叠加 = 混用不同时刻的传感器坐标系，
    点云拖影、扭曲——这就是运动畸变（类似相机的果冻效应）。

做了什么（Forster 式 IMU 预积分，只在 IMU 自己的 200 Hz 网格上积一次）：
    1. 把点流切成补偿窗（默认 = 框架周期 0.1 s = Mid-360L 一帧），每窗取一个
       参考时刻 t_ref（默认窗起始）；
    2. 用陀螺把「窗起始 → 窗内每个 IMU 时刻」的相对旋转 ΔR 积成四元数链
       （这就是预积分：只积两时刻之间的增量，不绑全局位姿）；
    3. 逐点查表：点时刻 t_k 落在哪两个 IMU 样本之间 → 插值出 ΔR(t_ref→t_k)；
    4. p' = ΔR·p_k，把点搬到 t_ref 那一刻的传感器系（--trans 再叠加
       ∫∫(ΔR·a − g)dt 的平移项）→ 整窗点云「好像同一瞬间扫出来的」。

输入：LIDAR\\<名>_radar.csv + IMU\\<名>_imu.csv（同名自动配对；两流来自同一
      台 Livox Mid-360L、同一时基，时间同为 epoch 秒；文件名沿用 *_radar.csv）
输出：deskew\\data\\<名>_radar_deskew.csv
          15 列与输入一致；x/y/z 为补偿后坐标，range/azimuth/elevation 按
          新 x/y/z 重算（保持自洽），timestamp = 窗口参考时刻（整窗同一时刻）
      deskew\\data\\<名>_radar_deskew_windows.csv
          逐窗统计（旋转角、点位移），画对比图 / 分析用

用法（优先级：命令行参数 > 文件顶部「输入输出入口」常量 > 自动配对）：
    方式一【手动入口】：改本文件顶部 DEFAULT_INPUT / DEFAULT_IMU / DEFAULT_OUTPUT
        三个常量后直接运行（PyCharm 右键 Run 走的就是这条路；留空 "" 走自动规则）。
        DEFAULT_OUTPUT 可以填完整 CSV 路径，也可以只填目录（自动补文件名）。
    方式二【命令行】：
        python "deskew\\imu.py"                                     # 常量留空时自动配对最新一对
        python "deskew\\imu.py" -i "LIDAR\\6666_radar.csv" --start 20 --duration 15
        python "deskew\\imu.py" -i "LIDAR\\99999_radar.csv" --ref mid    # 参考时刻=窗中点
        python "deskew\\imu.py" -i "LIDAR\\6666_radar.csv" --trans       # 追加平移补偿
        python "deskew\\imu.py" --selftest                             # 合成数据回归测试

假设与限制：
    · 激光与 IMU 同壳体：Mid-360L 内置 IMU 与激光头出厂已标定（外参 ≈ 单位阵），
      两流同一时基（同一 lvx / rosbag），时间同为 epoch 秒
    · 点时间：转出的 CSV 里同一帧的点共用帧时间戳（10 Hz 量化），窗内各点的
      时延按行序近似——Livox 是顺序扫描，行序即扫描序，误差 ≤ 一个点间隔，
      0.1 s 窗下可忽略；若 CSV 带 offset_time 列（Livox 原生字段，单位 ns），
      可改成 t = 帧时间 + offset_time×1e-9 得到真实逐点时刻
    · 平移项速度初值取 0、重力取窗内均值 → 匀速/匀加速平移补不了（没有速度
      观测，IMU-only 的本质限制），能补的是波浪类振荡平移
    · 陀螺偏置不建模；Mid-360L 内置 IMU 零偏约 0.004 rad/s（≈0.23°/s），
      实测确需扣除时用 --gyro-bias
    · yaw 无磁力计不影响：旋转预积分只需要陀螺
"""

from __future__ import annotations

import argparse
import bisect
import csv
import math
import os
import sys
import tempfile
import time

# ================= 输入输出入口（手动改这里） =================
# 处理哪份数据、结果放哪，直接改下面三个常量（留空 "" 走自动规则）。
# 优先级：命令行 -i / --imu / -o > 这三个常量 > 自动配对。
DEFAULT_INPUT = r"E:\pycharm project\pacp——csv\LIDAR\yyy_radar.csv"    # 输入·雷达点云 CSV。例：r"E:\pycharm project\pacp——csv\LIDAR\6666_radar.csv"
DEFAULT_IMU = r"E:\pycharm project\pacp——csv\IMU\yyy_imu.csv"      # 输入·IMU CSV。留空 = 按输入名配对 IMU\<名>_imu.csv
DEFAULT_OUTPUT = r"E:\pycharm project\pacp——csv\deskew\data"   # 输出·可以是完整 CSV 路径，也可以只填
                       # 目录（自动补 <名>_radar_deskew.csv）。留空 = deskew\data\
                       # （逐窗统计自动写成同名的 _windows.csv）

# ---------------- 派生路径（一般不用动） ----------------
_HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(_HERE)
LIDAR_DIR = os.path.join(PROJECT_ROOT, "LIDAR")
IMU_DIR = os.path.join(PROJECT_ROOT, "IMU")
OUTPUT_DIR = os.path.join(_HERE, "data")     # 输出：*_radar_deskew.csv 等

# ============ 设备规格（Livox Mid-360L，改这里即可） ============
SENSOR = "Livox Mid-360L"
FRAME_S = 0.1            # 帧周期（秒）：10 Hz 模式；开 20 Hz 模式时改 0.05
IMU_HZ = 200             # 内置 IMU 采样率（Hz）
POINT_RATE = 200000      # 点频（点/秒）出厂规格；转 CSV 后实测约 5 万/秒
MAX_RANGE = 70.0         # 最大量程（米）：40 m@10% 反射率，极限 70 m
FOV_V = (-7.0, 52.0)     # 垂直视场（度）；水平 360°
WINDOW_S = FRAME_S       # 去畸变窗 = 一帧：一帧内的点才算「同时刻」

FIELDS = ["timestamp", "range", "doppler", "azimuth", "elevation", "power",
          "x", "y", "z", "comp_height", "comp_velocity", "u", "v",
          "label", "instance"]
WIN_FIELDS = ["window", "t_start", "t_ref", "points", "rot_deg",
              "disp_mean_m", "disp_max_m"]

HIST_BIN = 0.005          # 点位移直方图粒度：5 mm
HIST_N = 1000             # 覆盖到 5 m

# ================= 四元数工具（纯标准库，Hamilton 约定） =================


def q_mul(a, b):
    aw, ax, ay, az = a
    bw, bx, by, bz = b
    return (aw * bw - ax * bx - ay * by - az * bz,
            aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw)


def q_conj(q):
    return (q[0], -q[1], -q[2], -q[3])


def q_norm(q):
    n = math.sqrt(q[0] * q[0] + q[1] * q[1] + q[2] * q[2] + q[3] * q[3])
    if n < 1e-12:
        return (1.0, 0.0, 0.0, 0.0)
    return (q[0] / n, q[1] / n, q[2] / n, q[3] / n)


def q_exp(rx, ry, rz):
    """旋转向量（rad）→ 四元数。"""
    th = math.sqrt(rx * rx + ry * ry + rz * rz)
    if th < 1e-12:
        return (1.0, 0.5 * rx, 0.5 * ry, 0.5 * rz)
    s = math.sin(0.5 * th) / th
    return (math.cos(0.5 * th), rx * s, ry * s, rz * s)


def q_rot(q, v):
    """用四元数 q 旋转向量 v（q·v·q⁻¹ 的展开式）。"""
    w, x, y, z = q
    vx, vy, vz = v
    tx = 2.0 * (y * vz - z * vy)
    ty = 2.0 * (z * vx - x * vz)
    tz = 2.0 * (x * vy - y * vx)
    return (vx + w * tx + (y * tz - z * ty),
            vy + w * ty + (z * tx - x * tz),
            vz + w * tz + (x * ty - y * tx))


def q_nlerp(a, b, s):
    d = a[0] * b[0] + a[1] * b[1] + a[2] * b[2] + a[3] * b[3]
    if d < 0.0:
        b = (-b[0], -b[1], -b[2], -b[3])
    return q_norm((a[0] + (b[0] - a[0]) * s,
                   a[1] + (b[1] - a[1]) * s,
                   a[2] + (b[2] - a[2]) * s,
                   a[3] + (b[3] - a[3]) * s))


def q_angle(q):
    """四元数对应的旋转角（rad），小角度下也精确。"""
    return 2.0 * math.atan2(
        math.sqrt(q[1] * q[1] + q[2] * q[2] + q[3] * q[3]), abs(q[0]))


# ================= IMU 表：加载与插值 =================


class ImuData:
    """IMU 表：t（epoch 秒）、w（陀螺 rad/s）、a（加速度 m/s²），均按时间升序。"""

    def __init__(self, t, w, a, gaps):
        self.t, self.w, self.a, self.gaps = t, w, a, gaps

    @classmethod
    def load(cls, path):
        t, w, a = [], [], []
        bad = 0
        with open(path, encoding="utf-8-sig", newline="") as fh:
            reader = csv.reader(fh)
            head = next(reader, None)
            if head is None:
                raise ValueError("IMU 表是空的: %s" % path)
            try:
                it = head.index("timestamp")
                iw = [head.index("angular_velocity_%s" % k) for k in "xyz"]
                ia = [head.index("linear_acceleration_%s" % k) for k in "xyz"]
            except ValueError as exc:
                raise ValueError("IMU 表头缺列（%s），需要 export_imu.py 的 13 列格式" % exc)
            need = max(iw + ia + [it])
            last = None
            for row in reader:
                if len(row) <= need:
                    bad += 1
                    continue
                try:
                    ts = float(row[it])
                    wv = (float(row[iw[0]]), float(row[iw[1]]), float(row[iw[2]]))
                    av = (float(row[ia[0]]), float(row[ia[1]]), float(row[ia[2]]))
                except ValueError:
                    bad += 1
                    continue
                if last is not None and ts <= last:
                    continue                      # 重复/乱序样本：丢弃
                t.append(ts)
                w.append(wv)
                a.append(av)
                last = ts
        if len(t) < 2:
            raise ValueError("IMU 有效样本不足 2 条（%d 条，坏行 %d），没法预积分"
                             % (len(t), bad))
        gaps = 0
        if len(t) > 10:
            dts = sorted(t[i + 1] - t[i] for i in range(len(t) - 1))
            med = dts[len(dts) // 2]
            gaps = sum(1 for i in range(len(t) - 1)
                       if t[i + 1] - t[i] > max(10.0 * med, 0.05))
        return cls(t, w, a, gaps)

    def span(self):
        return self.t[0], self.t[-1]

    def _bracket(self, tv):
        j = bisect.bisect_right(self.t, tv) - 1
        j = min(max(j, 0), len(self.t) - 2)
        dt = self.t[j + 1] - self.t[j]
        s = 0.0 if dt <= 0 else (tv - self.t[j]) / dt
        return j, min(max(s, 0.0), 1.0)

    def omega_at(self, tv):
        j, s = self._bracket(tv)
        w0, w1 = self.w[j], self.w[j + 1]
        return (w0[0] + (w1[0] - w0[0]) * s,
                w0[1] + (w1[1] - w0[1]) * s,
                w0[2] + (w1[2] - w0[2]) * s)

    def accel_at(self, tv):
        j, s = self._bracket(tv)
        a0, a1 = self.a[j], self.a[j + 1]
        return (a0[0] + (a1[0] - a0[0]) * s,
                a0[1] + (a1[1] - a0[1]) * s,
                a0[2] + (a1[2] - a0[2]) * s)


# ================= 预积分 =================


def integrate_window(imu, t_lo, t_hi, with_trans, bias=(0.0, 0.0, 0.0)):
    """在 [t_lo, t_hi] 上把 IMU 预积分成可查表的节点序列。

    返回 (times, quats, disps)：
        quats[i] = ΔR(t_lo → times[i])（四元数，t_lo 处为单位）
        with_trans 时 disps[i] = t_lo 起的位移 ∫∫(ΔR·a − g)dt（速度初值 0，
        g 取窗内 (ΔR·a) 均值 ≈ 重力 + 加速度计偏置）
    节点 = t_lo + 区间内的真实 IMU 样本 + t_hi（虚拟端点用线性插值补 ω/a）。
    """
    if t_hi <= t_lo:
        t_hi = t_lo + 1e-9
    j0 = min(max(bisect.bisect_right(imu.t, t_lo) - 1, 0), len(imu.t) - 1)
    j1 = min(max(bisect.bisect_right(imu.t, t_hi) - 1, 0), len(imu.t) - 1)

    def w_at(tv):
        v = imu.omega_at(tv)
        return (v[0] - bias[0], v[1] - bias[1], v[2] - bias[2])

    times = [t_lo]
    omegas = [w_at(t_lo)]
    accels = [imu.accel_at(t_lo)] if with_trans else None
    for j in range(j0 + 1, j1 + 1):
        times.append(imu.t[j])
        omegas.append((imu.w[j][0] - bias[0], imu.w[j][1] - bias[1],
                       imu.w[j][2] - bias[2]))
        if with_trans:
            accels.append(imu.a[j])
    if times[-1] < t_hi - 1e-12:
        times.append(t_hi)
        omegas.append(w_at(t_hi))
        if with_trans:
            accels.append(imu.accel_at(t_hi))

    # —— 旋转预积分：梯形中点 ω，q_{i+1} = q_i ⊗ exp(ω_mid·dt) ——
    quats = [(1.0, 0.0, 0.0, 0.0)]
    for i in range(len(times) - 1):
        dt = times[i + 1] - times[i]
        w0, w1 = omegas[i], omegas[i + 1]
        quats.append(q_norm(q_mul(
            quats[i],
            q_exp(0.5 * (w0[0] + w1[0]) * dt,
                  0.5 * (w0[1] + w1[1]) * dt,
                  0.5 * (w0[2] + w1[2]) * dt))))

    # —— 平移预积分：a 转到窗起始系、扣重力（窗内均值），二次积分 ——
    disps = None
    if with_trans:
        framed = [q_rot(quats[i], accels[i]) for i in range(len(times))]
        n = len(framed)
        g = (sum(f[0] for f in framed) / n,
             sum(f[1] for f in framed) / n,
             sum(f[2] for f in framed) / n)
        disps = [(0.0, 0.0, 0.0)]
        vx = vy = vz = 0.0
        for i in range(n - 1):
            dt = times[i + 1] - times[i]
            a0, a1 = framed[i], framed[i + 1]
            am = (0.5 * (a0[0] + a1[0]) - g[0],
                  0.5 * (a0[1] + a1[1]) - g[1],
                  0.5 * (a0[2] + a1[2]) - g[2])
            disps.append((disps[-1][0] + vx * dt + 0.5 * am[0] * dt * dt,
                          disps[-1][1] + vy * dt + 0.5 * am[1] * dt * dt,
                          disps[-1][2] + vz * dt + 0.5 * am[2] * dt * dt))
            vx += am[0] * dt
            vy += am[1] * dt
            vz += am[2] * dt
    return times, quats, disps


def _state_at(times, quats, disps, tv):
    """查表 + 插值：返回 (ΔR(t_lo→tv), 位移)。tv 钳到节点范围。"""
    tv = min(max(tv, times[0]), times[-1])
    i = bisect.bisect_right(times, tv) - 1
    i = min(max(i, 0), len(times) - 2)
    dt = times[i + 1] - times[i]
    s = 0.0 if dt <= 0 else (tv - times[i]) / dt
    s = min(max(s, 0.0), 1.0)
    q = q_nlerp(quats[i], quats[i + 1], s)
    if disps is None:
        return q, (0.0, 0.0, 0.0)
    d0, d1 = disps[i], disps[i + 1]
    return q, (d0[0] + (d1[0] - d0[0]) * s,
               d0[1] + (d1[1] - d0[1]) * s,
               d0[2] + (d1[2] - d0[2]) * s)


# ================= 主流程 =================


class _Win:
    __slots__ = ("m", "t_start", "rows")

    def __init__(self, m, t_start):
        self.m, self.t_start, self.rows = m, t_start, []


def convert(radar_csv, imu_csv, out_csv, windows_csv=None,
            window=WINDOW_S, ref_mode="start", with_trans=False,
            gyro_bias=(0.0, 0.0, 0.0), start=0.0, duration=0.0,
            max_points=0, verbose=True):
    """读 radar + imu 两份 CSV，输出去畸变后的 radar CSV 与逐窗统计 CSV。"""
    t_begin = time.perf_counter()
    if window <= 0:
        raise ValueError("补偿窗 --window 必须 > 0")
    imu = ImuData.load(imu_csv)
    imu_lo, imu_hi = imu.span()

    hist = [0] * HIST_N
    n_pts = 0
    sum_d = 0.0
    max_d = 0.0
    uncovered = 0
    win_stats = []                      # (rot_deg, t_lo, n, mean, max)
    out_rows = 0
    bad = 0
    skipped = 0

    os.makedirs(os.path.dirname(out_csv) or ".", exist_ok=True)
    ofh = open(out_csv, "w", newline="", encoding="utf-8-sig")
    owriter = csv.writer(ofh)
    owriter.writerow(FIELDS)
    wfh = None
    if windows_csv:
        os.makedirs(os.path.dirname(windows_csv) or ".", exist_ok=True)
        wfh = open(windows_csv, "w", newline="", encoding="utf-8-sig")
        wwriter = csv.writer(wfh)
        wwriter.writerow(WIN_FIELDS)

    _rot, _state = q_rot, _state_at          # 热循环局部别名

    def flush(win):
        nonlocal n_pts, sum_d, max_d, uncovered, out_rows
        if win is None or not win.rows:
            return
        t_lo = win.t_start
        t_hi = max(r[0] for r in win.rows)
        if ref_mode == "mid":
            t_ref = t_lo + 0.5 * window
        elif ref_mode == "end":
            t_ref = t_lo + window
        else:
            t_ref = t_lo
        # 完全没有 IMU 覆盖的窗：不补偿（保持原坐标、保留原时间戳）
        if t_hi < imu_lo - 0.5 or t_lo > imu_hi + 0.5:
            times = quats = disps = None
        else:
            times, quats, disps = integrate_window(
                imu, t_lo, t_hi, with_trans, gyro_bias)
        if times is None:
            q_ref = (1.0, 0.0, 0.0, 0.0)
            d_ref = (0.0, 0.0, 0.0)
            qc = q_ref
            rot_deg = 0.0
        else:
            q_ref, d_ref = _state(times, quats, disps, t_ref)
            qc = q_conj(q_ref)
            rot_deg = math.degrees(max(q_angle(q_mul(qc, quats[0])),
                                       q_angle(q_mul(qc, quats[-1]))))
        wsum = 0.0
        wmax = 0.0
        wn = 0
        for (ts, x, y, z, dop, pw, ch, cv, u, v, lab, inst) in win.rows:
            if ts < imu_lo or ts > imu_hi:
                uncovered += 1
            if times is None:
                px, py, pz = x, y, z
                ts_out = ts
            else:
                q_k, d_k = _state(times, quats, disps, ts)
                vx, vy, vz = _rot(q_k, (x, y, z))
                if with_trans:
                    vx += d_k[0] - d_ref[0]
                    vy += d_k[1] - d_ref[1]
                    vz += d_k[2] - d_ref[2]
                px, py, pz = _rot(qc, (vx, vy, vz))
                ts_out = t_ref
            owriter.writerow([
                "%.6f" % ts_out,
                "%.4f" % math.sqrt(px * px + py * py + pz * pz),
                dop,
                "%.6f" % math.atan2(py, px),
                "%.6f" % math.atan2(pz, math.hypot(px, py)),
                pw,
                "%.4f" % px, "%.4f" % py, "%.4f" % pz,
                ch, cv, u, v, lab, inst,
            ])
            d = math.sqrt((px - x) ** 2 + (py - y) ** 2 + (pz - z) ** 2)
            hist[min(int(d / HIST_BIN), HIST_N - 1)] += 1
            n_pts += 1
            sum_d += d
            if d > max_d:
                max_d = d
            wsum += d
            if d > wmax:
                wmax = d
            wn += 1
            out_rows += 1
        if wfh is not None:
            wwriter.writerow([win.m, "%.6f" % t_lo, "%.6f" % t_ref, wn,
                              "%.4f" % rot_deg,
                              "%.6f" % (wsum / wn if wn else 0.0),
                              "%.6f" % wmax])
        win_stats.append((rot_deg, t_lo, wn, wsum / wn if wn else 0.0, wmax))

    pending = None
    t_first = None        # 文件首点时间（--start 基准）
    t_keep0 = None        # 保留段首点时间（--duration 基准 + 窗格原点）
    t_last = None
    try:
        with open(radar_csv, encoding="utf-8-sig", newline="") as fh:
            next(fh, None)                       # 表头
            for line in fh:
                line = line.rstrip("\r\n")
                if not line:
                    continue
                parts = line.split(",")
                if len(parts) < 15:
                    bad += 1
                    continue
                try:
                    ts = float(parts[0])
                except ValueError:
                    bad += 1
                    continue
                if t_first is None:
                    t_first = ts
                if ts < t_first + start:
                    skipped += 1
                    continue
                if t_keep0 is None:
                    t_keep0 = ts
                try:
                    x = float(parts[6])
                    y = float(parts[7])
                    z = float(parts[8])
                except ValueError:
                    bad += 1
                    continue
                m = int((ts - t_keep0) // window)
                if pending is not None and m != pending.m:
                    flush(pending)
                    pending = None
                if pending is None:
                    pending = _Win(m, t_keep0 + m * window)
                pending.rows.append((ts, x, y, z,
                                     parts[2], parts[5], parts[9], parts[10],
                                     parts[11], parts[12], parts[13], parts[14]))
                t_last = ts
                if max_points and out_rows >= max_points:
                    break
                if duration and ts - t_keep0 > duration:
                    break
    finally:
        try:
            flush(pending)
        finally:
            ofh.close()
            if wfh is not None:
                wfh.close()

    if verbose:
        secs = time.perf_counter() - t_begin

        def pct(p):
            if n_pts == 0:
                return 0.0
            target = p * n_pts
            cum = 0
            for i, c in enumerate(hist):
                cum += c
                if cum >= target:
                    return (i + 0.5) * HIST_BIN
            return HIST_N * HIST_BIN

        span = (t_last - t_keep0) if (t_last is not None and t_keep0 is not None) else 0.0
        print("去畸变完成：%d 个点 / %d 个补偿窗（%.2f s 数据），耗时 %.1f 秒"
              % (n_pts, len(win_stats), span, secs))
        print("  IMU %d 条，覆盖 [%.3f, %.3f]；点时间在覆盖外的有 %d 个"
              % (len(imu.t), imu_lo, imu_hi, uncovered))
        if imu.gaps:
            print("  注意：IMU 有 %d 处 >10× 中位间隔的断流（按端点保持积分）" % imu.gaps)
        if win_stats:
            rots = [w[0] for w in win_stats]
            top = max(win_stats)
            print("  每窗旋转角：均值 %.4f°，最大 %.4f°（t=%.3f，该窗位移均值 %.2f cm）"
                  % (sum(rots) / len(rots), top[0], top[1], top[3] * 100))
        if n_pts:
            print("  点位移 |Δp|：均值 %.2f cm，p95 ≈ %.2f cm，最大 %.2f cm"
                  % (sum_d / n_pts * 100, pct(0.95) * 100, max_d * 100))
        print("  跳过前段 %d 点（--start %.1f s），坏行 %d" % (skipped, start, bad))
        print("  输出: %s (%.1f MB)" % (out_csv, os.path.getsize(out_csv) / 1024 / 1024))
        if windows_csv:
            print("        %s" % windows_csv)
    return {"points": n_pts, "windows": len(win_stats),
            "disp_mean_m": sum_d / n_pts if n_pts else 0.0,
            "disp_max_m": max_d, "uncovered": uncovered}


# ================= 合成数据回归测试 =================


def _write_imu_csv(path, t0, dur, omega_fn, accel_fn):
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["timestamp", "pitch", "roll", "yaw",
                    "angular_velocity_x", "angular_velocity_y", "angular_velocity_z",
                    "linear_acceleration_x", "linear_acceleration_y",
                    "linear_acceleration_z",
                    "magnetic_field_x", "magnetic_field_y", "magnetic_field_z"])
        t = 0.0
        while t <= dur + 0.011:
            ts = t0 + t
            wv = omega_fn(t)
            av = accel_fn(t)
            w.writerow(["%.6f" % ts, "", "", "",
                        "%.9f" % wv[0], "%.9f" % wv[1], "%.9f" % wv[2],
                        "%.9f" % av[0], "%.9f" % av[1], "%.9f" % av[2],
                        "", "", ""])
            t += 0.005


def _write_radar_csv(path, t0, pts):
    """pts: [(t_k, x, y, z), ...]，按 WaterScenes 15 列落盘。"""
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(FIELDS)
        for (tk, x, y, z) in pts:
            w.writerow(["%.6f" % (t0 + tk),
                        "%.4f" % math.sqrt(x * x + y * y + z * z), "",
                        "%.6f" % math.atan2(y, x),
                        "%.6f" % math.atan2(z, math.hypot(x, y)),
                        0, "%.4f" % x, "%.4f" % y, "%.4f" % z,
                        "", "", "", "", -1, 0])


def _read_xyz(path):
    out = []
    with open(path, encoding="utf-8-sig", newline="") as fh:
        r = csv.reader(fh)
        next(r, None)
        for row in r:
            out.append((float(row[6]), float(row[7]), float(row[8])))
    return out


def _math_check():
    """全精度校验（不过 CSV，不受 %.4f 落盘量化影响）：
    integrate_window / _state_at 的结果与解析解直接对比。"""
    ok_all = True

    def rz(theta, v):
        c, s = math.cos(theta), math.sin(theta)
        return (c * v[0] - s * v[1], s * v[0] + c * v[1], v[2])

    # —— 旋转：常值 ω=(0,0,0.25)，ΔR(t_lo→t) 应= R_z(0.25t) ——
    w = 0.25
    t_imu = [i * 0.005 for i in range(401)]           # 0 ~ 2.0 s
    imu = ImuData([100.0 + t for t in t_imu],
                  [(0.0, 0.0, w)] * len(t_imu),
                  [(0.0, 0.0, 9.80665)] * len(t_imu), 0)
    p = (15.0, 8.0, 1.0)
    times, quats, _ = integrate_window(imu, 100.0, 102.0, False)
    err = 0.0
    for i in range(len(times)):
        got = q_rot(quats[i], p)
        exp = rz(w * (times[i] - 100.0), p)
        err = max(err, math.sqrt(sum((g - e) ** 2 for g, e in zip(got, exp))))
    # 非节点时刻的插值路径
    t_probe = 0.0037
    while t_probe < 2.0:
        q, _ = _state_at(times, quats, None, 100.0 + t_probe)
        got = q_rot(q, p)
        exp = rz(w * t_probe, p)
        err = max(err, math.sqrt(sum((g - e) ** 2 for g, e in zip(got, exp))))
        t_probe += 0.0037
    ok = err < 1e-9
    ok_all = ok_all and ok
    print("  [%s] %-28s 最大误差 %.2e rad·m（阈值 1e-09）"
          % ("通过" if ok else "失败", "旋转预积分 vs 解析解", err))

    # —— 平移：ω=0，a=(2sin(πt),0,g)，位移应= 2t/π − 2sin(πt)/π² ——
    imu2 = ImuData([100.0 + t for t in t_imu],
                   [(0.0, 0.0, 0.0)] * len(t_imu),
                   [(2.0 * math.sin(math.pi * t), 0.0, 9.80665) for t in t_imu],
                   0)
    times, _, disps = integrate_window(imu2, 100.0, 102.0, True)
    err = 0.0
    for i in range(len(times)):
        t = times[i] - 100.0
        exp = 2.0 * t / math.pi - 2.0 * math.sin(math.pi * t) / math.pi ** 2
        err = max(err, abs(disps[i][0] - exp))
    ok = err < 1e-4
    ok_all = ok_all and ok
    print("  [%s] %-28s 最大误差 %.2e m（阈值 1e-04）"
          % ("通过" if ok else "失败", "平移预积分 vs 解析解", err))
    return ok_all


def selftest():
    """合成数据回归测试：已知真实运动 + 静止场景点 → 验证补偿回真实位置。"""
    print("自检（① 数学分：预积分 vs 解析解　② 端到端：合成 CSV → 补偿结果）")
    ok_all = _math_check()
    tmp = tempfile.mkdtemp(prefix="deskew_selftest_")
    imu_csv = os.path.join(tmp, "imu.csv")
    radar_csv = os.path.join(tmp, "radar.csv")
    out_csv = os.path.join(tmp, "out.csv")
    win_csv = os.path.join(tmp, "win.csv")

    T0 = 100.0
    W = 2.0                          # 测试窗放大到 2 s，运动可观测
    G = 9.80665
    world = [(15.0, 8.0, 1.0), (-12.0, 16.0, -0.5), (0.0, -20.0, 2.0), (25.0, -5.0, 0.0)]
    ok_all = True

    def run_case(name, omega_fn, accel_fn, expected_fn, with_trans,
                 ref_mode="start", tol=1e-6):
        nonlocal ok_all
        _write_imu_csv(imu_csv, T0, W, omega_fn, accel_fn)
        # 采样点故意不对齐 IMU 网格（3.7 ms ≠ 5 ms），逼出插值路径
        pts = []
        tk = 0.0
        while tk <= W:
            for pw in world:
                pts.append((tk,) + expected_fn(tk, pw))
            tk += 0.0037
        _write_radar_csv(radar_csv, T0, pts)
        convert(radar_csv, imu_csv, out_csv, win_csv, window=W,
                ref_mode=ref_mode, with_trans=with_trans, verbose=False)
        got = _read_xyz(out_csv)
        # 期望：补偿后应回到「参考时刻系下的真实位置」
        exp = []
        tk = 0.0
        t_ref = T0 + (0.5 * W if ref_mode == "mid" else W if ref_mode == "end" else 0.0)
        while tk <= W:
            for pw in world:
                exp.append(expected_fn(t_ref - T0, pw))
            tk += 0.0037
        err = max(math.sqrt((g[0] - e[0]) ** 2 + (g[1] - e[1]) ** 2 + (g[2] - e[2]) ** 2)
                  for g, e in zip(got, exp))
        ok = err < tol
        ok_all = ok_all and ok
        print("  [%s] %-28s 最大误差 %.2e m（阈值 %.0e）%s"
              % ("通过" if ok else "失败", name, err, tol, "" if ok else "  ← 检查！"))
        return ok

    print("自检（合成端到端：补偿后应回到参考时刻系下的真实位置）")

    # A. 纯旋转：ω=(0,0,0.25) rad/s，测得 p_k = R_z(ωt)ᵀ·p_w → 应回 p_w
    #    阈值 3e-4：data x/y/z 按 %.4f 落盘（量化 0.1 mm），全精度看上面 _math_check
    wA = 0.25
    cA = lambda t, pw: (math.cos(wA * t) * pw[0] + math.sin(wA * t) * pw[1],
                        -math.sin(wA * t) * pw[0] + math.cos(wA * t) * pw[1],
                        pw[2])
    run_case("纯旋转 ωz=0.25, ref=start", lambda t: (0.0, 0.0, wA),
             lambda t: (0.0, 0.0, G), cA, with_trans=False, tol=3e-4)
    run_case("纯旋转 ωz=0.25, ref=mid", lambda t: (0.0, 0.0, wA),
             lambda t: (0.0, 0.0, G), cA, with_trans=False, ref_mode="mid",
             tol=3e-4)

    # B. 纯平移（振荡）：ω=0，a_W = 2·sin(πt) m/s²（窗内均值=0），
    #    平台位移 x(t) = 2t/π − 2sin(πt)/π²，测得 p_k = p_w − x(t) → 应回 p_w
    def xB(t):
        return 2.0 * t / math.pi - 2.0 * math.sin(math.pi * t) / math.pi ** 2
    cB = lambda t, pw: (pw[0] - xB(t), pw[1], pw[2])
    run_case("振荡平移 a=2sin(πt), --trans", lambda t: (0.0, 0.0, 0.0),
             lambda t: (2.0 * math.sin(math.pi * t), 0.0, G), cB,
             with_trans=True, tol=3e-4)

    # C. 静止：应原样不动
    run_case("静止（应零位移）", lambda t: (0.0, 0.0, 0.0),
             lambda t: (0.0, 0.0, G), lambda t, pw: pw,
             with_trans=True, tol=1e-9)

    print("自检%s" % ("全部通过 ✅" if ok_all else "存在失败项 ❌"))
    return ok_all


# ================= 命令行 =================


def default_radar():
    """LIDAR\\ 里最新的、能在 IMU\\ 配到同名 _imu.csv 的 *_radar.csv。"""
    best = None
    try:
        names = os.listdir(LIDAR_DIR)
    except OSError:
        return None
    for name in names:
        if not name.endswith("_radar.csv"):
            continue
        stem = name[:-len("_radar.csv")]
        if os.path.isfile(os.path.join(IMU_DIR, stem + "_imu.csv")):
            path = os.path.join(LIDAR_DIR, name)
            m = os.path.getmtime(path)
            if best is None or m > best[0]:
                best = (m, path)
    return best[1] if best else None


def pair_imu(radar_csv):
    stem = os.path.splitext(os.path.basename(radar_csv))[0]
    if stem.endswith("_radar"):
        stem = stem[:-len("_radar")]
    return os.path.join(IMU_DIR, stem + "_imu.csv")


def _parse_bias(text):
    try:
        vals = [float(v) for v in text.split(",")]
    except ValueError:
        raise argparse.ArgumentTypeError("格式：wx,wy,wz（rad/s），如 0.001,-0.002,0.01")
    if len(vals) != 3:
        raise argparse.ArgumentTypeError("需要 3 个数：wx,wy,wz（rad/s）")
    return tuple(vals)


def _resolve_out(path, stem):
    """输出参数既可以是完整 CSV 路径，也可以是目录（目录则自动补文件名）。"""
    if not path:
        return os.path.join(OUTPUT_DIR, stem + "_deskew.csv")
    if os.path.isdir(path) or path.endswith(("\\", "/")):
        return os.path.join(path, stem + "_deskew.csv")
    return path


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="IMU 预积分点云去畸变（旋转补偿 + 可选平移补偿）")
    ap.add_argument("--selftest", action="store_true",
                    help="跑合成数据回归测试后退出")
    ap.add_argument("-i", "--input", default=None,
                    help="雷达点云 CSV。优先级：本参数 > 顶部常量 DEFAULT_INPUT > 自动配对最新")
    ap.add_argument("--imu", default=None,
                    help="IMU CSV。优先级：本参数 > 顶部常量 DEFAULT_IMU > 按输入名配对")
    ap.add_argument("-o", "--output", default=None,
                    help="输出 CSV 路径或目录。优先级：本参数 > 顶部常量 DEFAULT_OUTPUT > %s\\<名>_radar_deskew.csv" % OUTPUT_DIR)
    ap.add_argument("--window", type=float, default=WINDOW_S, metavar="SEC",
                    help="补偿窗长度（秒），默认 0.1")
    ap.add_argument("--ref", choices=("start", "mid", "end"), default="start",
                    help="参考时刻取窗的哪一端，默认 start")
    ap.add_argument("--trans", action="store_true",
                    help="追加平移补偿（加速度二次积分，速度初值 0，只补振荡项）")
    ap.add_argument("--gyro-bias", type=_parse_bias, default=(0.0, 0.0, 0.0),
                    metavar="WX,WY,WZ",
                    help="扣除的陀螺偏置 rad/s（可用静止段均值估计），默认 0,0,0")
    ap.add_argument("--start", type=float, default=0.0, metavar="SEC",
                    help="跳过前 N 秒（相对文件首点）")
    ap.add_argument("--duration", type=float, default=0.0, metavar="SEC",
                    help="只处理 N 秒")
    ap.add_argument("--max-points", type=int, default=0,
                    help="最多输出多少点（试跑用）")
    args = ap.parse_args(argv)

    if args.selftest:
        return 0 if selftest() else 1

    radar = args.input or DEFAULT_INPUT or default_radar()
    if not radar:
        print("LIDAR\\ 里没有能配到 IMU\\<名>_imu.csv 的 *_radar.csv；也可以在文件顶部「输入输出入口」里直接填 DEFAULT_INPUT", file=sys.stderr)
        return 2
    if not os.path.isfile(radar):
        print("找不到雷达输入: %s" % radar, file=sys.stderr)
        return 2
    imu = args.imu or DEFAULT_IMU or pair_imu(radar)
    if not os.path.isfile(imu):
        print("找不到配对 IMU: %s" % imu, file=sys.stderr)
        print("（规则：<名>_radar.csv ↔ IMU\\<名>_imu.csv；"
              "设备 B 的 w18 抓包没有 IMU 流，做不了去畸变）", file=sys.stderr)
        return 2

    stem = os.path.splitext(os.path.basename(radar))[0]
    out = _resolve_out(args.output or DEFAULT_OUTPUT, stem)
    win = os.path.splitext(out)[0] + "_windows.csv"

    print("输入雷达:", radar)
    print("输入 IMU :", imu)
    print("参数    : 窗=%.3f s, ref=%s, 平移补偿=%s, 偏置=%s"
          % (args.window, args.ref, "开" if args.trans else "关",
             ",".join("%.6f" % v for v in args.gyro_bias)))
    try:
        convert(radar, imu, out, windows_csv=win, window=args.window,
                ref_mode=args.ref, with_trans=args.trans,
                gyro_bias=args.gyro_bias, start=args.start,
                duration=args.duration, max_points=args.max_points)
    except Exception as exc:
        print("去畸变失败: %s" % exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
