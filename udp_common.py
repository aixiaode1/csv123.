#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
公共层：pcap / pcapng 容器解析 + UDP 提取 + 产物定位 + all.csv 读取。
被 export_all.py / export_radar.py / export_imu.py 共同 import（本文件自身不产出文件）。

输入：无（被 import，不直接执行）
输出：无（只提供函数与路径常量）

路径常量：
    ALL_DIR     = <项目目录>\\all        ① 全量导出目录
    OUTPUT_DIR  = E:\\dsh-output          旧产物目录，仅作兼容回退

对外函数：
    iter_records(path)   逐条产出 (抓包时间, 链路类型, 帧字节)
    udp_of(frame)        取 (src_ip, sport, dst_ip, dport, payload)
    iter_all_rows(path)  逐行读 ① 的 CSV，产出 {列名: 值}
    default_all_csv()    找最新的 *_all.csv

只依赖标准库；三个导出脚本共用本文件，保证解析口径完全一致。
"""

from __future__ import annotations

import csv
import glob
import os
import struct

# ---------------- 路径配置 ----------------
OUTPUT_DIR = r"E:\dsh-output"                                # 旧产物目录，仅作兼容回退
ALL_DIR = r"E:\pycharm project\pacp——csv\all"                          # ① 的输出目录（与 export_all.py 一致）

PCAP_MAGICS = {
    b"\xd4\xc3\xb2\xa1": ("<", 1e6), b"\xa1\xb2\xc3\xd4": (">", 1e6),
    b"\x4d\x3c\xb2\xa1": ("<", 1e9), b"\xa1\xb2\x3c\x4d": (">", 1e9),
}
PCAPNG_MAGIC = b"\x0a\x0d\x0d\x0a"


# ------------------------------------------------------------------ 容器层
def iter_records(path):
    """逐条产出 (抓包时间, 链路类型, 原始帧字节)。支持 pcap 与 pcapng。"""
    with open(path, "rb") as fh:
        magic = fh.read(4)
        if magic == PCAPNG_MAGIC:
            fh.seek(0)                      # 关键：pcapng 从第 0 字节读块头
            yield from _pcapng(fh)
        elif magic in PCAP_MAGICS:
            yield from _pcap(fh, magic)
        else:
            raise ValueError("不是 pcap/pcapng 文件（文件头 %r）" % magic)


def _pcap(fh, magic):
    endian, div = PCAP_MAGICS[magic]
    rest = fh.read(20)
    if len(rest) < 20:
        return
    linktype = struct.unpack(endian + "I", rest[16:20])[0]
    while True:
        hdr = fh.read(16)
        if len(hdr) < 16:
            return
        sec, frac, incl, _orig = struct.unpack(endian + "IIII", hdr)
        if incl > 0x04000000:
            raise ValueError("异常抓包长度: %d" % incl)
        data = fh.read(incl)
        if len(data) < incl:
            return
        yield sec + frac / div, linktype, data


def _pcapng(fh):
    endian, interfaces = "<", []
    while True:
        head = fh.read(8)
        if len(head) < 8:
            return
        if head[0:4] == PCAPNG_MAGIC:                       # Section Header
            bom = fh.read(4)
            if len(bom) < 4:
                return
            endian = ">" if bom == b"\x1a\x2b\x3c\x4d" else "<"
            blen = struct.unpack(endian + "I", head[4:8])[0]
            fh.read(blen - 12)
            interfaces = []
            continue
        btype, blen = struct.unpack(endian + "II", head)
        if blen < 12 or blen > 0x0FFFFFFF:
            raise ValueError("pcapng 块长度异常: %d" % blen)
        rest = fh.read(blen - 8)
        if len(rest) < blen - 8:
            return
        body = rest[:-4]
        if btype == 1:                                      # Interface Description
            linktype = struct.unpack(endian + "H", body[0:2])[0]
            divisor, pos = 10 ** 6, 8
            while pos + 4 <= len(body):
                code, olen = struct.unpack(endian + "HH", body[pos:pos + 4])
                pos += 4
                if code == 0:
                    break
                if code == 9 and olen >= 1:                 # if_tsresol
                    v = body[pos]
                    divisor = (2 ** (v & 0x7F)) if (v & 0x80) else (10 ** v)
                pos += (olen + 3) // 4 * 4
            interfaces.append((linktype, divisor))
        elif btype == 6:                                    # Enhanced Packet
            if len(body) < 20:
                continue
            iface, hi, lo, cap, _orig = struct.unpack(endian + "IIIII", body[:20])
            linktype, div = interfaces[iface] if iface < len(interfaces) else (1, 10 ** 6)
            yield ((hi << 32) | lo) / div, linktype, body[20:20 + cap]


# ------------------------------------------------------------------ 协议层
def _ipv6_str(raw):
    try:
        import socket
        return socket.inet_ntop(socket.AF_INET6, raw)
    except Exception:
        return ":".join(raw[i:i + 2].hex() for i in range(0, 16, 2))


def udp_of(frame, linktype=1):
    """从链路层帧里取出 (src_ip, sport, dst_ip, dport, payload)；非 IPv4/IPv6 的 UDP 返回 None。"""
    if linktype == 1:                                       # Ethernet
        if len(frame) < 14:
            return None
        off, etype = 12, struct.unpack(">H", frame[12:14])[0]
        while etype in (0x8100, 0x88A8, 0x9100) and len(frame) > off + 6:
            off += 4
            etype = struct.unpack(">H", frame[off:off + 2])[0]
        ip = off + 2
    elif linktype in (101, 12):                             # raw IP
        ip = 0
        etype = 0x0800 if frame and (frame[0] >> 4) == 4 else 0x86DD
    else:
        return None
    if ip >= len(frame):
        return None

    if etype == 0x0800:                                     # IPv4
        if len(frame) < ip + 20 or (frame[ip] >> 4) != 4:
            return None
        ihl = (frame[ip] & 0x0F) * 4
        if frame[ip + 9] != 17 or struct.unpack(">H", frame[ip + 6:ip + 8])[0] & 0x1FFF:
            return None
        src = ".".join(str(b) for b in frame[ip + 12:ip + 16])
        dst = ".".join(str(b) for b in frame[ip + 16:ip + 20])
        udp = ip + ihl
    elif etype == 0x86DD:                                   # IPv6
        if len(frame) < ip + 40:
            return None
        nh = frame[ip + 6]
        src = _ipv6_str(frame[ip + 8:ip + 24])
        dst = _ipv6_str(frame[ip + 24:ip + 40])
        udp = ip + 40
        for _ in range(8):                                  # 跳过扩展头
            if nh in (0, 43, 60):
                if len(frame) < udp + 8:
                    return None
                nh, udp = frame[udp], udp + (frame[udp + 1] + 1) * 8
            elif nh == 51:
                if len(frame) < udp + 8:
                    return None
                nh, udp = frame[udp], udp + (frame[udp + 1] + 2) * 4
            elif nh == 44:
                return None
            else:
                break
        if nh != 17:
            return None
    else:
        return None

    if len(frame) < udp + 8:
        return None
    sport, dport, ulen = struct.unpack(">HHH", frame[udp:udp + 6])
    payload = frame[udp + 8:udp + 8 + max(0, ulen - 8)] if ulen >= 8 else frame[udp + 8:]
    return src, sport, dst, dport, payload


# ------------------------------------------------------------------ 产物定位


def default_all_csv(output_dir=None):
    """找最新的 *_all.csv：先看项目 all\\ 目录，再兼容旧的 E:\\dsh-output。"""
    dirs = [output_dir] if output_dir else [ALL_DIR, OUTPUT_DIR]
    files = []
    for d in dirs:
        files.extend(glob.glob(os.path.join(d, "*_all.csv")))
    if not files:
        raise SystemExit("没找到 *_all.csv，请先运行 export_all.py")
    return max(files, key=os.path.getmtime)


def iter_all_rows(csv_path):
    """逐行读 ① 的 CSV，产出 {列名: 值} 的 dict（②③ 共用，省掉各自的表头处理）。"""
    with open(csv_path, encoding="utf-8-sig", newline="") as fh:
        reader = csv.reader(fh)
        try:
            head = next(reader)
        except StopIteration:
            return
        for row in reader:
            if len(row) < len(head):
                continue
            yield dict(zip(head, row))
