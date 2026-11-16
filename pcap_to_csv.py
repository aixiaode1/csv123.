#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
pcap / pcapng  ->  CSV  转换脚本（纯标准库，不需要 pip install 任何东西）
输出为udp格式
在 PyCharm 里两种用法：

1) 零配置：右键本文件 -> Run 'pcap_to_csv'
   脚本读取 DEFAULT_PCAP 指向的抓包文件，结果统一写到 OUTPUT_DIR
   （E:\\dsh-output\\20131206-1-w18.csv）

2) 在 PyCharm 底部 Terminal 里带参数运行：
   python pcap_to_csv.py "E:\\20231206-1-w18\\20131206-1-w18.pcap"
   python pcap_to_csv.py "....pcap" -o "E:\\dsh-output\\out.csv" --payload
   python pcap_to_csv.py "....pcap" --split 1000000        # 按行数切分成多个 CSV（Excel 友好）
   python pcap_to_csv.py "....pcap" --summary flows.csv    # 额外输出五元组汇总表

主表列：
   No, Time, EpochTime, SrcMAC, DstMAC, SrcIP, SrcPort, DstIP, DstPort,
   Protocol, TCPFlags, Length, Info     （加 --payload 时多一列 PayloadHex）
汇总表（默认总会生成一份 <名>_flows.csv）：
   SrcIP, SrcPort, DstIP, DstPort, Protocol, Packets, Bytes, StartTime, EndTime, Duration

CSV 使用 utf-8-sig 编码，双击用 Excel 打开不乱码。
"""

from __future__ import annotations

import argparse
import collections
import csv
import os
import struct
import sys
import time
from datetime import datetime, timedelta, timezone

# ------------------------------------------------------------------ 基本配置
DEFAULT_PCAP = r"E:\lidardata\99999.pcapng"
OUTPUT_DIR = r"E:\pycharm project\pacp——csv"                      # 所有成果统一输出到这里（不要放到 C 盘）
DEFAULT_TZ = timezone(timedelta(hours=8))          # 北京时间；要 UTC 就改成 timezone.utc

FIELDS = [
    "No", "Time", "EpochTime",
    "SrcMAC", "DstMAC",
    "SrcIP", "SrcPort", "DstIP", "DstPort",
    "Protocol", "TCPFlags", "Length", "Info",
]
SUMMARY_FIELDS = [
    "SrcIP", "SrcPort", "DstIP", "DstPort", "Protocol",
    "Packets", "Bytes", "StartTime", "EndTime", "Duration",
]

# pcap 文件头魔数 -> (字节序, 时间戳小数部分的分母)
PCAP_MAGICS = {
    b"\xd4\xc3\xb2\xa1": ("<", 1_000_000),        # 小端, 微秒
    b"\xa1\xb2\xc3\xd4": (">", 1_000_000),        # 大端, 微秒
    b"\x4d\x3c\xb2\xa1": ("<", 1_000_000_000),    # 小端, 纳秒
    b"\xa1\xb2\x3c\x4d": (">", 1_000_000_000),    # 大端, 纳秒
}
PCAPNG_MAGIC = b"\x0a\x0d\x0d\x0a"

# 链路类型（linktype / DLT）
LINKTYPE_ETHERNET = 1
LINKTYPE_RAW = 101
LINKTYPE_RAW_BSD = 12
LINKTYPE_LINUX_SLL = 113
LINKTYPE_LINUX_SLL2 = 276
LINKTYPE_NULL = 0
LINKTYPE_LOOP = 108


# ------------------------------------------------------------------ 小工具
def format_mac(raw: bytes) -> str:
    return ":".join("%02x" % b for b in raw)


def format_ipv6(raw: bytes) -> str:
    try:
        import socket
        return socket.inet_ntop(socket.AF_INET6, raw)
    except Exception:
        return ":".join(raw[i:i + 2].hex() for i in range(0, 16, 2))


TCP_FLAG_NAMES = (
    (0x001, "FIN"), (0x002, "SYN"), (0x004, "RST"), (0x008, "PSH"),
    (0x010, "ACK"), (0x020, "URG"), (0x040, "ECE"), (0x080, "CWR"),
    (0x100, "NS"),
)


def tcp_flags_str(flags: int) -> str:
    names = [name for bit, name in TCP_FLAG_NAMES if flags & bit]
    return "+".join(names) if names else "-"


def _parse_tz(text: str):
    text = (text or "").strip()
    if text.upper() in ("UTC", "Z"):
        return timezone.utc
    if text and text[0] in "+-":
        sign = 1 if text[0] == "+" else -1
        body = text[1:]
        hh, _, mm = body.partition(":")
        return timezone(sign * timedelta(hours=int(hh), minutes=int(mm or 0)))
    return DEFAULT_TZ


def _fmt_time(ts, tz):
    if ts is None:
        return ""
    return datetime.fromtimestamp(ts, tz).strftime("%Y-%m-%d %H:%M:%S.%f")


# ------------------------------------------------------------------ 报文解码
def _decode_arp(data: bytes, off: int, out: dict) -> None:
    out["Protocol"] = "ARP"
    if len(data) < off + 8:
        out["Info"] = "short arp"
        return
    opcode = struct.unpack(">H", data[off + 6:off + 8])[0]
    if len(data) >= off + 28:
        sip = ".".join(str(b) for b in data[off + 14:off + 18])
        tip = ".".join(str(b) for b in data[off + 24:off + 28])
        out["SrcIP"] = sip
        out["DstIP"] = tip
        verb = {1: "request", 2: "reply"}.get(opcode, "op=%d" % opcode)
        out["Info"] = "ARP %s %s -> %s" % (verb, sip, tip)
    else:
        out["Info"] = "ARP op=%d" % opcode


def _decode_tcp(data: bytes, off: int, out: dict, l4_len: int, want_payload: bool) -> None:
    out["Protocol"] = "TCP"
    if len(data) < off + 14:
        out["Info"] = "short tcp"
        return
    sport, dport = struct.unpack(">HH", data[off:off + 4])
    doff_flags = struct.unpack(">H", data[off + 12:off + 14])[0]
    doff = (doff_flags >> 12) * 4
    if doff < 20:
        doff = 20
    flags = doff_flags & 0x01FF
    out["SrcPort"] = sport
    out["DstPort"] = dport
    out["TCPFlags"] = tcp_flags_str(flags)
    payload_len = l4_len - doff
    if payload_len < 0:
        payload_len = max(0, len(data) - off - doff)
    out["Info"] = "TCP %s len=%d" % (out["TCPFlags"], payload_len)
    if want_payload and payload_len > 0:
        out["payload_hex"] = data[off + doff:off + doff + payload_len][:256].hex()


def _decode_udp(data: bytes, off: int, out: dict, l4_len: int, want_payload: bool) -> None:
    out["Protocol"] = "UDP"
    if len(data) < off + 8:
        out["Info"] = "short udp"
        return
    sport, dport, ulen = struct.unpack(">HHH", data[off:off + 6])
    payload_len = ulen - 8 if ulen >= 8 else max(0, l4_len - 8)
    out["SrcPort"] = sport
    out["DstPort"] = dport
    out["Info"] = "UDP len=%d" % payload_len
    if want_payload and payload_len > 0:
        out["payload_hex"] = data[off + 8:off + 8 + payload_len][:256].hex()


def _decode_icmp(data: bytes, off: int, out: dict) -> None:
    out["Protocol"] = "ICMP"
    if len(data) < off + 4:
        out["Info"] = "short icmp"
        return
    out["Info"] = "ICMP type=%d code=%d" % (data[off], data[off + 1])


def _decode_l4(data: bytes, off: int, proto: int, out: dict, l4_len: int, want_payload: bool) -> None:
    """proto: IP 层的协议号"""
    if proto == 6:
        _decode_tcp(data, off, out, l4_len, want_payload)
    elif proto == 17:
        _decode_udp(data, off, out, l4_len, want_payload)
    elif proto in (1, 58):
        _decode_icmp(data, off, out)
    elif proto == 2:
        out["Protocol"] = "IGMP"
        out["Info"] = "IGMP"
    elif proto == 132:
        out["Protocol"] = "SCTP"
        if len(data) >= off + 4:
            out["SrcPort"], out["DstPort"] = struct.unpack(">HH", data[off:off + 4])
        out["Info"] = "SCTP"
    else:
        out["Protocol"] = "IP_%d" % proto
        out["Info"] = "IP protocol %d len=%d" % (proto, max(0, l4_len))


def _decode_ipv4(data: bytes, off: int, out: dict, want_payload: bool) -> None:
    if len(data) < off + 20:
        out["Protocol"] = "IPv4"
        out["Info"] = "short ipv4"
        return
    ihl = (data[off] & 0x0F) * 4
    if ihl < 20:
        out["Protocol"] = "IPv4"
        out["Info"] = "bad ihl"
        return
    total_len = struct.unpack(">H", data[off + 2:off + 4])[0]
    frag = struct.unpack(">H", data[off + 6:off + 8])[0]
    proto = data[off + 9]
    out["SrcIP"] = ".".join(str(b) for b in data[off + 12:off + 16])
    out["DstIP"] = ".".join(str(b) for b in data[off + 16:off + 20])
    if frag & 0x1FFF:
        out["Protocol"] = "IPv4"
        out["Info"] = "IPv4 fragment offset=%d" % (frag & 0x1FFF)
        return
    l4_len = total_len - ihl
    if l4_len <= 0:
        l4_len = max(0, len(data) - off - ihl)
    _decode_l4(data, off + ihl, proto, out, l4_len, want_payload)


def _decode_ipv6(data: bytes, off: int, out: dict, want_payload: bool) -> None:
    if len(data) < off + 40:
        out["Protocol"] = "IPv6"
        out["Info"] = "short ipv6"
        return
    payload_len = struct.unpack(">H", data[off + 4:off + 6])[0]
    nh = data[off + 6]
    out["SrcIP"] = format_ipv6(data[off + 8:off + 24])
    out["DstIP"] = format_ipv6(data[off + 24:off + 40])
    pos = off + 40
    for _ in range(8):                                  # 跳过扩展头
        if nh in (0, 43, 60):                           # Hop-by-Hop / Routing / DestOpt
            if len(data) < pos + 8:
                break
            nh_next = data[pos]
            pos += (data[pos + 1] + 1) * 8
            nh = nh_next
        elif nh == 51:                                  # AH
            if len(data) < pos + 8:
                break
            nh_next = data[pos]
            pos += (data[pos + 1] + 2) * 4
            nh = nh_next
        elif nh == 44:                                  # Fragment
            if len(data) < pos + 8:
                break
            nh = data[pos]
            out["Protocol"] = "IPv6"
            out["Info"] = "IPv6 fragment"
            pos += 8
            break
        else:
            break
    l4_len = payload_len - (pos - off - 40)
    if l4_len <= 0:
        l4_len = max(0, len(data) - pos)
    _decode_l4(data, pos, nh, out, l4_len, want_payload)


def _decode_ethertype(etype: int, data: bytes, off: int, out: dict, want_payload: bool) -> None:
    if etype == 0x0800:
        _decode_ipv4(data, off, out, want_payload)
    elif etype == 0x86DD:
        _decode_ipv6(data, off, out, want_payload)
    elif etype == 0x0806:
        _decode_arp(data, off, out)
    else:
        out["Protocol"] = "ETH_0x%04X" % etype
        out["Info"] = "ethertype 0x%04X len=%d" % (etype, max(0, len(data) - off))


def _decode_ethernet(data: bytes, out: dict, want_payload: bool) -> None:
    if len(data) < 14:
        out["Info"] = "short ethernet frame"
        return
    out["DstMAC"] = format_mac(data[0:6])
    out["SrcMAC"] = format_mac(data[6:12])
    off = 12
    etype = struct.unpack(">H", data[off:off + 2])[0]
    guard = 0
    while etype in (0x8100, 0x88A8, 0x9100, 0x9200) and guard < 4:   # VLAN 标签
        if len(data) < off + 6:
            out["Info"] = "truncated vlan tag"
            return
        etype = struct.unpack(">H", data[off + 4:off + 6])[0]
        off += 4
        guard += 1
    _decode_ethertype(etype, data, off + 2, out, want_payload)


def decode_packet(linktype: int, data: bytes, want_payload: bool = False) -> dict:
    """把一条链路层报文解析成 CSV 需要的字段字典；异常就地吞掉，不中断转换。"""
    out = {
        "SrcMAC": "", "DstMAC": "", "SrcIP": "", "DstIP": "",
        "SrcPort": "", "DstPort": "", "Protocol": "?",
        "TCPFlags": "", "Info": "",
    }
    try:
        if linktype == LINKTYPE_ETHERNET:
            _decode_ethernet(data, out, want_payload)
        elif linktype in (LINKTYPE_RAW, LINKTYPE_RAW_BSD):
            if not data:
                return out
            version = data[0] >> 4
            if version == 4:
                _decode_ipv4(data, 0, out, want_payload)
            elif version == 6:
                _decode_ipv6(data, 0, out, want_payload)
            else:
                out["Protocol"] = "RAW"
                out["Info"] = "raw ip version %d" % version
        elif linktype == LINKTYPE_LINUX_SLL:
            if len(data) >= 16:
                _decode_ethertype(struct.unpack(">H", data[14:16])[0], data, 16, out, want_payload)
        elif linktype == LINKTYPE_LINUX_SLL2:
            if len(data) >= 20:
                _decode_ethertype(struct.unpack(">H", data[0:2])[0], data, 20, out, want_payload)
        elif linktype in (LINKTYPE_NULL, LINKTYPE_LOOP):
            if len(data) >= 4:
                family = struct.unpack("<I", data[0:4])[0]
                if family == 2:
                    _decode_ipv4(data, 4, out, want_payload)
                elif family in (10, 23, 24, 28, 30):
                    _decode_ipv6(data, 4, out, want_payload)
                else:
                    out["Protocol"] = "LOOP"
                    out["Info"] = "loopback family=%d" % family
        else:
            out["Protocol"] = "LINKTYPE_%d" % linktype
            out["Info"] = "unsupported linktype %d" % linktype
    except Exception as exc:                            # 畸形包不影响整体转换
        out["Info"] = "decode error: %s" % exc
    return out


# ------------------------------------------------------------------ 读取 pcap / pcapng
def _iter_pcap(fh, magic: bytes):
    endian, divisor = PCAP_MAGICS[magic]
    rest = fh.read(20)
    if len(rest) < 20:
        return
    linktype = struct.unpack(endian + "I", rest[16:20])[0]
    while True:
        hdr = fh.read(16)
        if len(hdr) < 16:
            return
        ts_sec, ts_frac, incl_len, orig_len = struct.unpack(endian + "IIII", hdr)
        if incl_len > 0x04000000:
            raise ValueError("suspicious capture length: %d" % incl_len)
        data = fh.read(incl_len)
        if len(data) < incl_len:
            return
        yield ts_sec + ts_frac / divisor, linktype, incl_len, orig_len, data


def _iter_pcapng(fh):
    endian = "<"
    interfaces = []                                  # [(linktype, 时间戳分母)]
    while True:
        head = fh.read(8)
        if len(head) < 8:
            return
        if head[0:4] == PCAPNG_MAGIC:                # Section Header Block
            bom = fh.read(4)
            if len(bom) < 4:
                return
            if bom == b"\x1a\x2b\x3c\x4d":
                endian = ">"
            elif bom == b"\x4d\x3c\x2b\x1a":
                endian = "<"
            else:
                raise ValueError("bad pcapng byte-order magic: %r" % bom)
            blen = struct.unpack(endian + "I", head[4:8])[0]
            if blen < 28 or blen > 0x0FFFFFFF:
                raise ValueError("bad pcapng section length: %d" % blen)
            fh.read(blen - 12)
            interfaces = []
            continue
        btype, blen = struct.unpack(endian + "II", head)
        if blen < 12 or blen > 0x0FFFFFFF:
            raise ValueError("bad pcapng block length: %d" % blen)
        rest = fh.read(blen - 8)
        if len(rest) < blen - 8:
            return
        body = rest[:-4]                             # 去掉尾部重复的 length
        if btype == 0x00000001:                      # Interface Description Block
            if len(body) < 8:
                interfaces.append((1, 1_000_000))
                continue
            linktype = struct.unpack(endian + "H", body[0:2])[0]
            divisor = 1_000_000
            pos = 8
            while pos + 4 <= len(body):
                code, olen = struct.unpack(endian + "HH", body[pos:pos + 4])
                pos += 4
                if code == 0:
                    break
                value = body[pos:pos + olen]
                if code == 9 and olen >= 1:          # if_tsresol
                    v = value[0]
                    divisor = (2 ** (v & 0x7F)) if (v & 0x80) else (10 ** v)
                pos += (olen + 3) // 4 * 4
            interfaces.append((linktype, divisor))
        elif btype == 0x00000006:                    # Enhanced Packet Block
            if len(body) < 20:
                continue
            iface, ts_high, ts_low, cap_len, orig_len = struct.unpack(endian + "IIIII", body[0:20])
            raw = body[20:20 + cap_len]
            linktype, divisor = interfaces[iface] if iface < len(interfaces) else (1, 1_000_000)
            yield ((ts_high << 32) | ts_low) / divisor, linktype, cap_len, orig_len, raw
        elif btype == 0x00000003:                    # Simple Packet Block
            if len(body) < 4:
                continue
            orig_len = struct.unpack(endian + "I", body[0:4])[0]
            raw = body[4:]
            yield None, (interfaces[0][0] if interfaces else 1), len(raw), orig_len, raw
        elif btype == 0x00000002:                    # 旧式 Packet Block
            if len(body) < 20:
                continue
            iface = struct.unpack(endian + "H", body[0:2])[0]
            ts_high, ts_low, cap_len, orig_len = struct.unpack(endian + "IIII", body[4:20])
            raw = body[20:20 + cap_len]
            linktype, divisor = interfaces[iface] if iface < len(interfaces) else (1, 1_000_000)
            yield ((ts_high << 32) | ts_low) / divisor, linktype, cap_len, orig_len, raw


def iter_packets(path: str):
    """逐条产出 (时间戳, linktype, 抓包长度, 原始长度, 字节)"""
    with open(path, "rb") as fh:
        magic = fh.read(4)
        if magic == PCAPNG_MAGIC:
            fh.seek(0)                 # 关键：pcapng 解析函数要从第 0 字节读块头
            yield from _iter_pcapng(fh)
        elif magic in PCAP_MAGICS:
            yield from _iter_pcap(fh, magic)
        else:
            raise ValueError("不是 pcap/pcapng 文件（文件头为 %r），可先用 Wireshark 另存为 .pcap" % magic)


# ------------------------------------------------------------------ 主流程
def convert(pcap_path, csv_path, tz=DEFAULT_TZ, want_payload=False,
            split_rows=0, summary_path=None, verbose=True):
    """把 pcap 转成 CSV；split_rows>0 时按行数切分成多个文件；summary_path 非空时额外输出汇总表。"""
    fields = FIELDS + (["PayloadHex"] if want_payload else [])
    stem, ext = os.path.splitext(csv_path)
    if not ext:
        ext = ".csv"
        stem = csv_path

    counter = collections.Counter()
    flows = {}                        # (src, sport, dst, dport, proto) -> [包数, 字节数, 首ts, 末ts]
    total = 0
    first_ts = last_ts = None
    started = time.perf_counter()

    state = {"part": 0, "rows": 0, "fh": None, "writer": None, "files": []}

    def open_part():
        if state["fh"] is not None:
            state["fh"].close()
        if split_rows:
            state["part"] += 1
            name = "%s_part%02d%s" % (stem, state["part"], ext)
        else:
            state["part"] = 1
            name = stem + ext
        fh = open(name, "w", newline="", encoding="utf-8-sig")
        state["fh"] = fh
        state["writer"] = csv.writer(fh)
        state["writer"].writerow(fields)
        state["rows"] = 0
        state["files"].append(name)
        return name

    open_part()
    try:
        for ts, linktype, cap_len, orig_len, raw in iter_packets(pcap_path):
            total += 1
            if split_rows and state["rows"] >= split_rows:
                open_part()
            info = decode_packet(linktype, raw, want_payload)
            counter[info["Protocol"]] += 1
            if ts is None:
                time_text, epoch_text = "", ""
            else:
                time_text = _fmt_time(ts, tz)
                epoch_text = "%.6f" % ts
                if first_ts is None:
                    first_ts = ts
                last_ts = ts
            row = [
                total, time_text, epoch_text,
                info["SrcMAC"], info["DstMAC"],
                info["SrcIP"], info["SrcPort"],
                info["DstIP"], info["DstPort"],
                info["Protocol"], info["TCPFlags"],
                orig_len, info["Info"],
            ]
            if want_payload:
                row.append(info.get("payload_hex", ""))
            state["writer"].writerow(row)
            state["rows"] += 1

            if summary_path:
                key = (info["SrcIP"], info["SrcPort"], info["DstIP"], info["DstPort"], info["Protocol"])
                rec = flows.get(key)
                if rec is None:
                    flows[key] = [1, orig_len, ts, ts]
                else:
                    rec[0] += 1
                    rec[1] += orig_len
                    if ts is not None:
                        if rec[2] is None or ts < rec[2]:
                            rec[2] = ts
                        if rec[3] is None or ts > rec[3]:
                            rec[3] = ts
    finally:
        if state["fh"] is not None:
            state["fh"].close()

    if summary_path:
        with open(summary_path, "w", newline="", encoding="utf-8-sig") as fh:
            writer = csv.writer(fh)
            writer.writerow(SUMMARY_FIELDS)
            for key, (packets, nbytes, start, end) in sorted(flows.items(), key=lambda kv: -kv[1][0]):
                duration = (end - start) if (start is not None and end is not None) else ""
                if duration != "":
                    duration = "%.6f" % duration
                writer.writerow([
                    key[0], key[1], key[2], key[3], key[4],
                    packets, nbytes, _fmt_time(start, tz), _fmt_time(end, tz), duration,
                ])

    if verbose:
        cost = time.perf_counter() - started
        print("转换完成，共 %d 个数据包，耗时 %.2f 秒" % (total, cost))
        for name in state["files"]:
            print("  输出: %s (%.1f MB)" % (name, os.path.getsize(name) / 1024 / 1024))
        if summary_path:
            print("  汇总: %s (%d 条流)" % (summary_path, len(flows)))
        if first_ts is not None:
            print("  时间范围: %s ~ %s" % (_fmt_time(first_ts, tz)[:-3], _fmt_time(last_ts, tz)[:-3]))
        print("  协议分布: " + ", ".join("%s=%d" % item for item in counter.most_common(8)))
    return total


def main(argv=None):
    parser = argparse.ArgumentParser(description="把 pcap/pcapng 抓包文件转换成 CSV")
    parser.add_argument("pcap", nargs="?", default=DEFAULT_PCAP,
                        help="抓包文件路径，默认 %s" % DEFAULT_PCAP)
    parser.add_argument("-o", "--output", default=None,
                        help="输出 CSV 路径，默认放到 %s 下（与抓包文件同名）" % OUTPUT_DIR)
    parser.add_argument("--payload", action="store_true", help="额外输出 TCP/UDP 载荷十六进制")
    parser.add_argument("--split", type=int, default=0, metavar="ROWS",
                        help="每个文件最多多少行数据后切分（例如 1000000，便于 Excel 打开）")
    parser.add_argument("--summary", default=None, metavar="CSV", help="额外输出五元组流汇总表")
    parser.add_argument("--tz", default="+08:00", help="CSV 时间戳时区，默认 +08:00；可写 +00:00 或 UTC")
    args = parser.parse_args(argv)

    pcap_path = os.path.abspath(args.pcap)
    if not os.path.isfile(pcap_path):
        print("找不到抓包文件: %s" % pcap_path, file=sys.stderr)
        return 2
    if args.output:
        csv_path = args.output
    else:
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        stem = os.path.splitext(os.path.basename(pcap_path))[0]
        csv_path = os.path.join(OUTPUT_DIR, stem + ".csv")
    summary_path = args.summary
    if summary_path is None:
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        summary_path = os.path.splitext(csv_path)[0] + "_flows.csv"

    try:
        convert(pcap_path, csv_path, tz=_parse_tz(args.tz), want_payload=args.payload,
                split_rows=args.split, summary_path=summary_path)
    except Exception as exc:
        print("转换失败: %s" % exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
