#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
【cluster】点云 DBSCAN 聚类（KD-Tree 加速 + eps 随距离自适应 + 小块浪花剔除）。

问题在哪：
    毫米波雷达的"点"是回波，海面上有大量浪花/飞沫/多径杂波，下游要做目标级的
    事（跟踪、标注、避障）必须先聚成"物体"。但雷达点云密度随距离衰减：
    近处（几米）点间距几厘米，远处（几十米）点间距几十厘米——固定 eps 的
    DBSCAN 要么把近处连成一片、要么把远处目标打散。

怎么做的：
    1. 按 0.1 s 时间窗切分（与去畸变同窗；输入是去畸变产物时窗边界天然对齐），
       每个窗 = 一帧"瞬时"点云；
    2. 每个窗用 scipy cKDTree 建 3D KD-Tree，逐点 radius 查询；
       eps 随距离线性自适应：eps(r) = 0.2 m（近）→ 0.5 m（远）；
    3. DBSCAN（minPts 默认 8，可在 5–10 调）：核心点扩展 + 边界点归簇；
    4. 簇内点数 < min-size（默认 10）的"小块"判为浪花/飞沫（instance=0）；
       --drop-noise 可把噪声/小块点直接从输出里删掉。

输入：deskew\\data\\*_radar_deskew.csv（15 列）或 LIDAR\\*_radar.csv（同样 15 列）
输出：deskew\\data\\<名>_cluster.csv
          15 列不变；instance = 簇号（1..K 全局连续，0 = 噪声/浪花）；
          label 等其余列原样透传
      deskew\\data\\<名>_cluster_windows.csv
          逐窗统计（点数、簇数、剔除小块、噪声、最大簇）

用法（优先级：命令行参数 > 文件顶部「输入输出入口」常量 > 默认值）：
    方式一【手动入口】：改本文件顶部 DEFAULT_INPUT / DEFAULT_OUTPUT 后直接运行
    方式二【命令行】：
        python "deskew\\cluster.py" -i "deskew\\data\\6666_radar_deskew.csv"
        python "deskew\\cluster.py" --eps-near 0.2 --eps-far 0.5 --min-pts 6
        python "deskew\\cluster.py" --drop-noise --min-size 15
        python "deskew\\cluster.py" --selftest                 # 合成数据回归测试

假设与限制：
    · 只做空间聚类，不用 doppler/power（要筛"正在动的目标"可后续加 doppler 门限）
    · 3D 欧氏距离（--dims xy 可切 2D；雷达 z 量化粗时 2D 更稳）
    · 簇号只保证"同一窗内唯一"（跨窗不跟踪：同一物体在不同窗拿到不同簇号）
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import tempfile
import time

import numpy as np
from scipy.spatial import cKDTree

# ================= 输入输出入口（手动改这里） =================
# 处理哪份数据、结果放哪，直接改下面两个常量（留空 "" 走默认规则）。
# 优先级：命令行 -i / -o > 这两个常量 > 默认。
DEFAULT_INPUT = r"E:\pycharm project\pacp——csv\deskew\data\yyy_radar_deskew.csv"
DEFAULT_OUTPUT = r"E:\pycharm project\pacp——csv\cluster\data"   # 输出：可填完整 CSV 路径，也可只填目录（自动补 <名>_cluster.csv）；
                       # 留空 = 与输入同目录

# ---------------- 聚类参数（Livox Mid-360L · 水面） ----------------
EPS_NEAR = 0.15       # 近处 eps（米），R_NEAR 以内
EPS_FAR  = 0.80       # 远处 eps（米），R_FAR 以外
R_NEAR   = 10.0       # eps 起涨距离（米）
R_FAR    = 70.0       # eps 到顶距离（米）
MIN_PTS  = 10         # DBSCAN minPts
MIN_SIZE = 20         # 簇内点数 < 20 → 判浪花/飞沫，丢弃
WINDOW   = 0.1        # Mid-360L 默认 10 Hz 一帧（20 Hz 模式改 0.05）
DIMS     = "xyz"      # Livox 的 z 准，用 3D

# ---------------- 派生路径（一般不用动） ----------------
_HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(_HERE)
OUTPUT_DIR = os.path.join(_HERE, "../deskew/data")

STAT_FIELDS = ["window", "t_start", "points", "clusters", "dropped_small",
               "noise_pts", "clustered_pts", "largest"]


def eps_of(r, eps_near=EPS_NEAR, eps_far=EPS_FAR, r_near=R_NEAR, r_far=R_FAR):
    """eps 随距离自适应：R_NEAR 内用 EPS_NEAR，R_FAR 外 EPS_FAR，中间线性。"""
    r = np.asarray(r, dtype=np.float64)
    span = max(r_far - r_near, 1e-9)
    out = eps_near + (eps_far - eps_near) * (r - r_near) / span
    return np.clip(out, eps_near, eps_far)


def _edges(pts, eps, dims="xyz"):
    """KD-Tree 逐点 radius 查询 + 对称化，返回 (ii, jj, 度数) 的边列表。

    对称化：j ∈ N(i) 要求 d(i,j) ≤ min(eps_i, eps_j)——DBSCAN 的密度可达
    关系因此是对称的，聚类结果与遍历顺序无关。全部向量化（便于百万点级）。
    """
    use = pts if dims == "xyz" else pts[:, :2]
    n = len(use)
    tree = cKDTree(use)
    raw = tree.query_ball_point(use, eps)          # 每点用各自的 eps（cKDTree 支持数组半径）
    counts = np.fromiter((len(r) for r in raw), dtype=np.int64, count=n)
    jj = np.fromiter((j for r in raw for j in r), dtype=np.int64, count=int(counts.sum()))
    ii = np.repeat(np.arange(n, dtype=np.int64), counts)
    diff = use[ii] - use[jj]
    d2 = np.einsum("ij,ij->i", diff, diff)
    keep = d2 <= (eps[jj] ** 2)
    ii, jj = ii[keep], jj[keep]
    deg = np.bincount(ii, minlength=n)             # 含自身（d=0 必然保留）
    return ii, jj, deg


def dbscan(pts, eps, min_pts, dims="xyz"):
    """DBSCAN：返回 (labels, 簇数)。labels: -1=噪声, 0..K-1=簇。

    实现：核心点之间的边构成无向图 → scipy 连通分量（等价于经典 DBSCAN 的
    密度可达传播，但走 C 内核）；边界点附着到相邻核心点所在分量。
    """
    n = len(pts)
    if n == 0:
        return np.zeros(0, dtype=np.int64), 0
    ii, jj, deg = _edges(pts, eps, dims)
    core = deg >= min_pts
    if not core.any():
        return np.full(n, -1, dtype=np.int64), 0

    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components

    m = core[ii] & core[jj]
    g = coo_matrix((np.ones(int(m.sum()), dtype=np.int8), (ii[m], jj[m])), shape=(n, n))
    _, comp = connected_components(g, directed=False)   # 全节点编号，孤立点自成一号

    labels = np.full(n, -1, dtype=np.int64)
    labels[core] = comp[core]
    border = (~core[ii]) & core[jj]                     # 边界点（对称关系下另一侧必是核心）
    if border.any():
        b_i, b_c = ii[border], comp[jj[border]]
        uniq, first = np.unique(b_i, return_index=True)  # 每个边界点取第一个相邻核心点的分量
        labels[uniq] = b_c[first]

    ids = np.unique(labels[labels >= 0])
    out = np.full(n, -1, dtype=np.int64)
    pos = labels >= 0
    out[pos] = np.searchsorted(ids, labels[pos])         # 压缩成 0..K-1
    return out, len(ids)


def drop_small(labels, n_clusters, min_size):
    """小块（簇内点数 < min_size）判为浪花：标签改 -1，返回 (标签, 保留簇数, 剔除簇数)。"""
    if n_clusters == 0:
        return labels, 0, 0
    kept = labels >= 0
    if not kept.any():
        return labels, 0, 0
    counts = np.bincount(labels[kept], minlength=n_clusters)
    small = np.flatnonzero(counts < min_size)
    if len(small):
        labels = labels.copy()
        labels[np.isin(labels, small)] = -1
    n_kept = n_clusters - len(small)
    return labels, n_kept, len(small)


def convert(in_csv, out_csv, stats_csv=None, eps_near=EPS_NEAR, eps_far=EPS_FAR,
            r_near=R_NEAR, r_far=R_FAR, min_pts=MIN_PTS, min_size=MIN_SIZE,
            window=WINDOW, dims=DIMS, drop_noise=False, max_points=0, verbose=True):
    """逐窗聚类并写盘。返回统计 dict。"""
    t_begin = time.time()
    n_pts = n_win = n_clusters = n_noise = n_dropped = n_rows = 0
    largest = 0
    rows = []            # 当前窗的原始行（字符串）
    buf_x = []; buf_y = []; buf_z = []; buf_r = []

    def flush():
        nonlocal n_pts, n_win, n_clusters, n_noise, n_dropped, n_rows, largest
        nonlocal rows, buf_x, buf_y, buf_z, buf_r
        if not rows:
            return
        pts = np.column_stack((buf_x, buf_y, buf_z))
        eps = eps_of(buf_r, eps_near, eps_far, r_near, r_far)
        labels, n_c = dbscan(pts, eps, min_pts, dims)
        labels, n_kept, n_small = drop_small(labels, n_c, min_size)
        # 簇号 → 全局 id（1..K），噪声/小块 → 0
        gmap = {}
        noise_pts = 0
        win_largest = 0
        for k, row in enumerate(rows):
            lab = labels[k]
            if lab < 0:
                inst = 0
                noise_pts += 1
            else:
                gid = gmap.get(lab)
                if gid is None:
                    n_clusters += 1
                    gid = gmap[lab] = n_clusters
                inst = gid
            row[inst_idx] = str(inst)
            if inst or not drop_noise:
                w.writerow(row)
                n_rows += 1
        sizes = {}
        for lab in labels:
            if lab >= 0:
                sizes[lab] = sizes.get(lab, 0) + 1
        win_largest = max(sizes.values()) if sizes else 0
        largest = max(largest, win_largest)
        n_pts += len(rows)
        n_win += 1
        n_noise += noise_pts
        n_dropped += n_small
        if stats_csv:
            sw.writerow([n_win - 1, "%.6f" % t_cur, len(rows), n_kept, n_small,
                         noise_pts, len(rows) - noise_pts, win_largest])
        if verbose and (n_win % 100 == 0):
            el = time.time() - t_begin
            print("  窗 %d：%d 点，累计簇 %d，噪声 %d 点，已用 %.0f s"
                  % (n_win, len(rows), n_clusters, n_noise, el))
        rows = []; buf_x = []; buf_y = []; buf_z = []; buf_r = []

    with open(in_csv, encoding="utf-8-sig", newline="") as fh_in:
        rd = csv.reader(fh_in)
        header = next(rd)
        try:
            i_t = header.index("timestamp")
            i_r = header.index("range")
            i_x = header.index("x")
            i_y = header.index("y")
            i_z = header.index("z")
            inst_idx = header.index("instance")
        except ValueError:
            i_t, i_r, i_x, i_y, i_z, inst_idx = 0, 1, 6, 7, 8, 14
        need = max(i_t, i_r, i_x, i_y, i_z, inst_idx)

        os.makedirs(os.path.dirname(os.path.abspath(out_csv)) or ".", exist_ok=True)
        win = os.path.splitext(out_csv)[0] + "_windows.csv"
        stats_csv = stats_csv or win
        fh_out = open(out_csv, "w", encoding="utf-8-sig", newline="")
        w = csv.writer(fh_out)
        w.writerow(header)
        fh_stat = open(stats_csv, "w", encoding="utf-8-sig", newline="")
        sw = csv.writer(fh_stat)
        sw.writerow(STAT_FIELDS)

        t0 = None
        t_cur = 0.0
        cur = None
        bad = 0
        try:
            for line_no, row in enumerate(rd):
                if len(row) <= need:
                    bad += 1
                    continue
                try:
                    t = float(row[i_t])
                    x = float(row[i_x]); y = float(row[i_y]); z = float(row[i_z])
                    r = float(row[i_r])
                except ValueError:
                    bad += 1
                    continue
                if t0 is None:
                    t0 = t
                # round 而不是 floor：去畸变时间戳只有 6 位小数，
                # floor((t-t0)/w) 会把 m*0.1 的微小误差判成上一窗（相邻窗被合并）
                wid = int(round((t - t0) / window))
                if cur is None:
                    cur = wid
                elif wid != cur:
                    flush()
                    cur = wid
                t_cur = t
                rows.append(row)
                buf_x.append(x); buf_y.append(y); buf_z.append(z); buf_r.append(r)
                if max_points and n_rows + len(rows) >= max_points:
                    flush()
                    break
            flush()
        finally:
            fh_out.close()
            fh_stat.close()
    if verbose:
        el = time.time() - t_begin
        print("聚类完成：%d 个点 / %d 个时间窗（%d 簇，噪声/剔除 %d 点），耗时 %.1f 秒"
              % (n_pts, n_win, n_clusters, n_noise, el))
        print("  最大簇 %d 点，小块剔除 %d 个，坏行 %d" % (largest, n_dropped, bad))
        print("  输出: %s (%.1f MB)" % (out_csv, os.path.getsize(out_csv) / 1024 / 1024))
        print("        %s" % stats_csv)
    return {"points": n_pts, "windows": n_win, "clusters": n_clusters,
            "noise": n_noise, "dropped_small": n_dropped, "largest": largest}


# ================= 合成数据回归测试 =================


def selftest():
    ok = True
    rng = np.random.default_rng(7)

    def blob(cx, cy, cz, n, sigma):
        return np.column_stack([rng.normal(cx, sigma, n), rng.normal(cy, sigma, n),
                                rng.normal(cz, sigma * 0.3, n)])

    near = blob(8.0, 0.0, 0.5, 60, 0.06)      # 近处（~8 m）紧凑
    mid = blob(-20.0, 15.0, 0.6, 80, 0.12)    # 中部（~25 m）
    far = blob(0.0, 45.0, 1.0, 120, 0.60)     # 远处（~45 m）稀疏：固定 0.2 会打散
    small = blob(20.0, 5.0, 0.4, 9, 0.03)     # 小块（9 点：≥minPts 但 <min_size，应判浪花）
    noise = np.column_stack([rng.uniform(-40, 40, 60), rng.uniform(-10, 55, 60),
                             rng.uniform(-1, 2, 60)])
    pts = np.vstack([near, mid, far, small, noise])
    n_near, n_mid, n_far, n_small = len(near), len(mid), len(far), len(small)

    # ① 自适应 eps 下：3 个簇 + 小块被剔
    r = np.linalg.norm(pts, axis=1)
    eps = eps_of(r)
    labels, n_c = dbscan(pts, eps, MIN_PTS)
    labels, n_kept, n_small_dropped = drop_small(labels, n_c, MIN_SIZE)
    sizes = sorted(np.bincount(labels[labels >= 0]).tolist(), reverse=True)
    print("  [合成] 自适应 eps：%d 簇（剔除小块 %d 个），簇大小 %s（期望 ~[120,80,60]）"
          % (n_kept, n_small_dropped, sizes))
    if n_kept != 3 or n_small_dropped != 1:
        print("    ✗ 簇数不对"); ok = False
    if sizes and (sizes[0] < 0.85 * n_far or sizes[-1] < 0.85 * n_near):
        print("    ✗ 簇大小明显偏小"); ok = False

    # ② 固定 eps=0.2：远处稀疏簇应被打散（说明自适应是必要的）
    labels2, n_c2 = dbscan(pts, np.full(len(pts), EPS_NEAR), MIN_PTS)
    labels2, n_kept2, _ = drop_small(labels2, n_c2, MIN_SIZE)
    print("  [合成] 固定 eps=0.2：%d 簇（期望 < 3：远簇已散）" % n_kept2)
    if n_kept2 >= n_kept:
        print("    ✗ 固定 eps 没打散远簇，测试无效"); ok = False

    # ③ 端到端：合成 CSV → convert → 读回检查 instance（全部点在同一个窗外）
    tmp = tempfile.mkdtemp(prefix="cluster_st")
    try:
        cin = os.path.join(tmp, "syn_radar.csv")
        ts0 = 1794292807.0
        with open(cin, "w", encoding="utf-8-sig", newline="") as fh:
            wr = csv.writer(fh)
            wr.writerow(["timestamp", "range", "doppler", "azimuth", "elevation",
                         "power", "x", "y", "z", "comp_height", "comp_velocity",
                         "u", "v", "label", "instance"])
            for p in pts:
                wr.writerow(["%.6f" % ts0, "%.4f" % float(np.linalg.norm(p)),
                             "", "0", "0", "100",
                             "%.4f" % p[0], "%.4f" % p[1], "%.4f" % p[2],
                             "", "", "", "", "-1", "0"])
        cout = os.path.join(tmp, "syn_radar_cluster.csv")
        st = convert(cin, cout, verbose=False)
        with open(cout, encoding="utf-8-sig", newline="") as fh:
            rr = csv.reader(fh)
            hd = next(rr)
            ids = [int(row[hd.index("instance")]) for row in rr]
        uniq = sorted(set(ids))
        print("  [端到端] 输出 %d 行，窗数 %d，instance 取值 %s"
              % (len(ids), st["windows"], uniq[:8]))
        if st["windows"] != 1:
            print("    ✗ 窗数应为 1（所有点同一时间戳）"); ok = False
        if 0 not in uniq or len([u for u in uniq if u > 0]) != 3:
            print("    ✗ instance 不符合预期（应有 0 和 3 类簇）"); ok = False
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    print("自检%s" % ("全部通过 ✅" if ok else "未通过 ❌"))
    return ok


def _resolve_out(path, in_csv):
    """输出：CSV 路径 / 目录（自动补 <名>_cluster.csv）/ 空（与输入同目录）。"""
    stem = os.path.splitext(os.path.basename(in_csv))[0]
    if not path:
        return os.path.join(os.path.dirname(os.path.abspath(in_csv)), stem + "_cluster.csv")
    if os.path.isdir(path) or path.endswith(("\\", "/")):
        return os.path.join(path, stem + "_cluster.csv")
    return path


def main(argv=None):
    ap = argparse.ArgumentParser(description="点云 DBSCAN 聚类（KD-Tree + eps 随距离自适应）")
    ap.add_argument("--selftest", action="store_true", help="跑合成数据回归测试后退出")
    ap.add_argument("-i", "--input", default=None, help="输入点云 CSV（15 列）")
    ap.add_argument("-o", "--output", default=None, help="输出 CSV 或目录")
    ap.add_argument("--eps-near", type=float, default=EPS_NEAR, help="近处 eps，默认 %.2f m" % EPS_NEAR)
    ap.add_argument("--eps-far", type=float, default=EPS_FAR, help="远处 eps，默认 %.2f m" % EPS_FAR)
    ap.add_argument("--r-near", type=float, default=R_NEAR, help="eps 起涨距离，默认 %.0f m" % R_NEAR)
    ap.add_argument("--r-far", type=float, default=R_FAR, help="eps 到顶距离，默认 %.0f m" % R_FAR)
    ap.add_argument("--min-pts", type=int, default=MIN_PTS, help="DBSCAN minPts，默认 %d" % MIN_PTS)
    ap.add_argument("--min-size", type=int, default=MIN_SIZE,
                    help="簇内点数 < 它判为浪花剔除，默认 %d" % MIN_SIZE)
    ap.add_argument("--window", type=float, default=WINDOW, help="时间窗，默认 %.1f s" % WINDOW)
    ap.add_argument("--dims", choices=("xyz", "xy"), default=DIMS, help="聚类维度，默认 %s" % DIMS)
    ap.add_argument("--drop-noise", action="store_true",
                    help="噪声/小块点不写入输出（默认保留并置 instance=0）")
    ap.add_argument("--max-points", type=int, default=0, help="最多处理多少点（试跑用）")
    args = ap.parse_args(argv)

    if args.selftest:
        return 0 if selftest() else 1

    in_csv = args.input or DEFAULT_INPUT
    if not in_csv:
        print("没有输入：请在 -i 或文件顶部 DEFAULT_INPUT 指定点云 CSV", file=sys.stderr)
        return 2
    if not os.path.isfile(in_csv):
        print("找不到输入: %s" % in_csv, file=sys.stderr)
        return 2
    out_csv = _resolve_out(args.output or DEFAULT_OUTPUT, in_csv)

    print("输入雷达:", in_csv)
    print("参数    : eps=%.2f→%.2f m（%.0f→%.0f m），minPts=%d，小块<%d 剔除，窗=%.2f s，%s"
          % (args.eps_near, args.eps_far, args.r_near, args.r_far,
             args.min_pts, args.min_size, args.window, args.dims))
    try:
        convert(in_csv, out_csv, eps_near=args.eps_near, eps_far=args.eps_far,
                r_near=args.r_near, r_far=args.r_far, min_pts=args.min_pts,
                min_size=args.min_size, window=args.window, dims=args.dims,
                drop_noise=args.drop_noise, max_points=args.max_points)
    except Exception as exc:
        print("聚类失败: %s" % exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())