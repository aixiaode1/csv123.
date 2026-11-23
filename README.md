# pacp——csv 项目说明

把抓包文件（`.pcap` / `.pcapng`）里的传感器数据转换成 CSV。项目提供两套视角：

| 视角 | 脚本 | 一行代表 | 用途 |
|---|---|---|---|
| **网络视角**（逐包） | `pcap_to_csv.py` | 一个 UDP/TCP 包 | 看流量、五元组、包长、时序 |
| **点云视角**（逐点） | `pcap_to_points.py` / `pcap_to_points_w18.py` | 一个雷达/激光点 | 喂给点云算法、对齐 WaterScenes 数据集格式 |

两套脚本都只用 Python 标准库（`struct` / `csv`），不依赖 scapy、numpy，PyCharm 里右键 Run 即可。

- Python 虚拟环境：`.venv`（Python 3.14.5）
- 原始抓包：`E:\lidardata\`
- 输出目录：本项目目录（脚本里的 `OUTPUT_DIR` 常量）

---

## 1. 目录结构

```
E:\pycharm project\pacp——csv\
├── udp_common.py              # 【新】公共层：pcap/pcapng 解析 + UDP 提取
├── export_all.py              # 【新】① 导出全部 UDP 数据（流水线源头）
├── export_radar.py            # 【新】② 雷达点云，读 ① 的 CSV
├── export_imu.py              # 【新】③ IMU，读 ① 的 CSV，对齐 WaterScenes 格式
├── pcap_udp_export.py         # 旧：导出全部 UDP（功能已被 ① 覆盖）
├── pcap_to_points.py          # 旧：逐点转换，设备 A（端口 56300）
├── pcap_to_points_w18.py      # 旧：逐点转换，设备 B（端口 57000）
├── main.py                    # PyCharm 默认模板，无用
├── 说明.ipynb                 # 早期笔记（列定义草稿）
├── 流程与数据流.md             # 【新】链路专篇：流程 / 数据流 / 载荷结构图
├── tools\                     # 【新】画图脚本：结构图、IMU 图、点云分布
│   ├── draw_payload_map.py
│   ├── draw_imu_map.py
│   └── plot_payload.py
├── deskew\                    # 【新】IMU 预积分去畸变（读 LIDAR\ + IMU\ 的 CSV）
│   ├── imu.py                 #   主脚本：旋转预积分 + 可选平移（纯标准库，--selftest 自检）
│   ├── plot_deskew.py         #   去畸变前后对比六联图（numpy/matplotlib）
│   ├── cluster.py             #   点云 DBSCAN 聚类（KD-Tree，eps 随距离自适应；需 scipy）
│   ├── 原理与实现.md          #   专篇：公式推导、实现细节、四层验证体系
│   └── data\                  #   产物：*_radar_deskew.csv、*_cluster.csv 等（不入库）
├── photo\                     # 文档引用的图片（A-file / B-file / imu / points-A / points-B）
├── all\                       # ① 全量导出（*_all.csv，不入库）
├── LIDAR\                     # 雷达点云产物（*_radar.csv，不入库）
├── IMU\                       # IMU 产物（*_imu.csv，不入库）
├── .venv\                     # Python 3.14.5 虚拟环境
└── .idea\                     # PyCharm 工程配置
```

> `pcap_to_csv.py` 已被 `database/export_all.py` 取代（3.1 / 4.1 节保留其原理说明，供查历史）。

---

## 2. 快速开始

### 2.1 在 PyCharm 里

1. 右键 `pcap_to_points.py` → **Run**
2. 不传参时读脚本里的 `DEFAULT_PCAP`，写到 `OUTPUT_DIR`

### 2.2 命令行

```bat
:: 逐包表
python pcap_to_csv.py "E:\lidardata\99999.pcapng"
python pcap_to_csv.py "E:\lidardata\99999.pcapng" --payload --split 1000000

:: 逐点表（设备 A）
python pcap_to_points.py "E:\lidardata\99999.pcapng" --duration 10
python pcap_to_points.py "E:\lidardata\yyy.pcapng" --split 1000000

:: 逐点表（设备 B）
python pcap_to_points_w18.py "E:\lidardata\20131206-1-w18.pcap" --duration 10
```

---

## 3. 脚本参数

### 3.1 `pcap_to_csv.py`（逐包）

| 参数 | 说明 |
|---|---|
| `pcap` | 抓包路径，默认 `E:\lidardata\99999.pcapng` |
| `-o, --output` | 输出 CSV，默认 `OUTPUT_DIR\<名>.csv` |
| `--payload` | 多一列 `PayloadHex`（TCP/UDP 载荷前 256 字节） |
| `--split N` | 每 N 行切一个文件（Excel 友好） |
| `--summary CSV` | 另出五元组汇总表（默认总会生成 `<名>_flows.csv`） |
| `--tz` | 时间戳时区，默认 `+08:00`，可写 `UTC` |

**输出列**

```
No, Time, EpochTime, SrcMAC, DstMAC, SrcIP, SrcPort, DstIP, DstPort,
Protocol, TCPFlags, Length, Info          （--payload 时追加 PayloadHex）
```

汇总表列：`SrcIP, SrcPort, DstIP, DstPort, Protocol, Packets, Bytes, StartTime, EndTime, Duration`

### 3.2 `pcap_to_points.py` / `pcap_to_points_w18.py`（逐点）

| 参数 | 说明 |
|---|---|
| `pcap` | 抓包路径 |
| `-o, --output` | 输出 CSV |
| `--start N` | 从第 N 秒开始 |
| `--duration N` | 取 N 秒 |
| `--split N` | 每 N 个点切一个文件 |
| `--keep-zero` | 保留无回波空点（默认丢弃 `x=y=z=0`） |
| `--stride N` | 每 N 个点取 1 个（仅 w18 版支持，降采样用） |
| `--power half\|raw` | power 列格式（仅 w18 版支持） |

**输出列**（与 WaterScenes `radar/*.csv` 完全一致）

```
timestamp, range, doppler, azimuth, elevation, power, x, y, z,
comp_height, comp_velocity, u, v, label, instance
```

| 列 | 来源 | 说明 |
|---|---|---|
| `timestamp` | 抓包时间 + 点内偏移 | epoch 秒，6 位小数 |
| `x, y, z` | 点结构 int32，单位 mm | 输出为**米**，传感器坐标系 |
| `range` | `sqrt(x²+y²+z²)` | 米 |
| `azimuth` | `atan2(y, x)` | 弧度 |
| `elevation` | `atan2(z, sqrt(x²+y²))` | 弧度 |
| `power` | 设备 A：反射率 u8；设备 B：float16 强度(dB) | |
| `doppler` | — | **留空**，激光/该雷达无多普勒输出 |
| `comp_height` / `comp_velocity` | — | **留空**，需要水面基准与多普勒 |
| `u, v` | — | **留空**，需要相机内外参与时间同步 |
| `label` / `instance` | — | 固定 `-1` / `0`（未标注，与 WaterScenes 未标注点写法一致） |

> 注意：WaterScenes 里的 `x,y,z` 是平台/世界坐标系，`range/azimuth/elevation` 是雷达原始极坐标，两者不能互相反推（实测 `|(x,y,z)| ≠ range`）。本项目的 `x,y,z` 与 `range/az/el` 在**同一传感器坐标系**下自洽。

---

## 4. 转换流程

三个脚本骨架一致：**容器层切出记录 → 协议层解出字段 → 输出层摊平成行**，全程 `yield` 流式处理，内存占用与文件大小无关。

```
pcap / pcapng
   │  ① 容器层：魔数 → 记录边界（pcap 16 B 记录头 / pcapng 块循环）
   ▼
[(ts, linktype, 帧字节), ...]
   │  ② 协议层：Ethernet → IPv4/IPv6 → UDP → 载荷 → 字段
   ▼
[{五元组...} 或 {x, y, z, 强度...}]
   │  ③ 输出层：固定列序，utf-8-sig 写 CSV
   ▼
*.csv
```

### 4.1 `pcap_to_csv.py`（逐包视角）

**Step 1 · 识别容器**：读前 4 字节魔数。四种 pcap 变体（大小端 × 微秒/纳秒）走 `_iter_pcap`；`0a 0d 0d 0a` 走 `_iter_pcapng`。pcap 分支从 24 B 全局头后 4 字节取链路类型；pcapng 分支必须**先用 SHB 的 Byte-Order Magic 定端序**再解析块长，并从 IDB 的 `if_tsresol` 读时间戳精度。

**Step 2 · 切记录**：

- pcap：循环读 16 B 记录头（`ts_sec, ts_frac, incl_len, orig_len`）+ `incl_len` 字节数据；`incl_len > 64 MB` 直接报错，短读直接停
- pcapng：块循环，`body = rest[:-4]` 去掉尾部重复的长度；EPB 里 `ts = ((ts_high << 32) | ts_low) / tsresol`

**Step 3 · 逐层解码**（每层先做长度校验，不够长就返回）：

```
Ethernet 14 B ─┬─ 0x8100/0x88A8 → 剥 4 B VLAN 标签（循环，最多 4 层）
               ├─ 0x0800 → IPv4：IHL 定首部长度；分片偏移 ≠ 0 只标"分片"不硬解端口
               ├─ 0x86DD → IPv6：40 B 固定头 + 逐个跳过 Hop-by-Hop/Routing/AH/Fragment
               └─ 0x0806 → ARP：取 op、发送方/目标 IP
传输层：TCP（首部长度取高 4 位，标志取低 9 位）/ UDP / ICMP / SCTP / 其它 IP proto
```

**Step 4 · 组行写出**：13 列固定顺序；时间给两份（`Time` 是可读的 +08:00 字符串，`EpochTime` 是浮点秒），`Length` 用 `orig_len`（线上真实长度）而不是抓包长度。

**Step 5 · 同循环聚合流**：一边写行一边更新 `flows[(src, sport, dst, dport, proto)]`，结束时写 `<名>_flows.csv`——避免为了几行汇总再读一遍 2 GB。

### 4.2 `pcap_to_points.py`（设备 A 逐点）

```
读记录（复用 Step 1、2）
   │ ① 帧过滤：IPv4 + UDP 且 56300 → 56301
   ▼
UDP 载荷 1380 B
   │ ② 载荷校验：前缀 00 64 05、长度 1380、[5:7] == 96
   ▼
跳过 36 B 头 → 96 点 × 14 B
   │ ③ 每点：int32 x/y/z（mm → m）；尾 2 B = u8 反射率 + u8 tag
   ▼
④ 点内时间：抓包时间 + i × 5 µs（设备固定 480 µs/包）
   ▼
⑤ 派生 range / azimuth / elevation → 15 列写出（空点默认丢弃）
```

### 4.3 `pcap_to_points_w18.py`（设备 B 逐点）

骨架同 4.2，有四处必须改动：

| 环节 | 设备 A（`pcap_to_points.py`） | 设备 B（`pcap_to_points_w18.py`） |
|---|---|---|
| ① 帧过滤 | 端口写死 `56300 → 56301` | **不按端口**，只看载荷长度 + 魔数 + 点数域 |
| ② 载荷校验 | 前缀 / 长度 / 点数 | 相同，因此对任意端口都生效 |
| ③ 点尾字段 | u8 反射率 + u8 tag | **float16 强度（dB）**，`--power raw` 可看原始 uint16 |
| ④ 点内时间 | 固定 5 µs | **动态**：`(本包设备时间戳 − 上包) / 96`，与抓包间隔取小者防跳变，首包用 208320 ns 兜底 |

**为什么 ④ 必须动态**：该设备相邻包的设备时间戳差在 205~226 µs 之间浮动，不是恒定 480 µs；用固定值会让点内时间持续漂移。

### 4.4 三个脚本共同的设计取舍

| 取舍 | 原因 |
|---|---|
| 全部 `yield` 流式处理 | 2 GB 抓包也只占几十 MB 内存 |
| 只依赖 `struct` / `csv` | PyCharm 里零配置，不用装 scapy、numpy |
| 每层长度校验 + 短读即停 | 被截断的抓包不会卡死，也不会抛异常 |
| 单包异常就地吞掉 | 2 GB 文件不能因为一个畸形包前功尽弃 |
| `utf-8-sig` + `newline=""` | Excel 双击不乱码，行尾不会多出 `\r` |
| 默认丢弃 `x=y=z=0` 空点 | 空点占 62%~74%，保留会让文件膨胀约 3 倍 |
| `--split` / `--stride` | 绕开 Excel 104 万行上限、控制文件体积 |

---

## 5. 数据格式

### 5.1 抓包容器

| 文件头魔数 | 格式 | 说明 |
|---|---|---|
| `d4 c3 b2 a1` | pcap 小端微秒 | 最常见 |
| `a1 b2 c3 d4` | pcap 大端微秒 | |
| `4d 3c b2 a1` / `a1 b2 3c 4d` | pcap 小端/大端纳秒 | |
| `0a 0d 0d 0a` | pcapng | Section Header / Interface Description / Enhanced Packet 块 |

pcap：24 字节全局头 + 每包 16 字节记录头（`ts_sec, ts_frac, incl_len, orig_len`）。
pcapng：块结构；**字节序要先用 Section Header 的 Byte-Order Magic `0x1A2B3C4D` 判定**，`if_tsresol` 决定时间戳精度。

### 5.2 点云 UDP 载荷（两台设备相同骨架）

```
载荷 1380 B = 36 B 头 + 96 点 × 14 B
```

头部字段：

| 偏移 | 类型 | 含义 |
|---|---|---|
| `[0]` | u8 | 固定 `0x00` |
| `[1:3]` | u16 LE | 载荷长度 `0x0564` = 1380 |
| `[5:7]` | u16 LE | 点数 = 96 |
| `[7:9]` | u16 LE | 包序号（逐包 +1） |
| `[28:36]` | u64 LE | 设备时间戳（纳秒） |

点结构（14 字节）：

| 偏移 | 类型 | 含义 |
|---|---|---|
| `0:4` | int32 LE | x（毫米） |
| `4:8` | int32 LE | y（毫米） |
| `8:12` | int32 LE | z（毫米） |
| `12:14` | 设备相关 | 设备 A：`u8 反射率 + u8 tag`；设备 B：`float16 强度(dB)` |

### 5.3 两台设备的差异

| 项目 | 设备 A（`99999` / `6666` / `7777` / `8888888` / `yyy`） | 设备 B（`20131206-1-w18`） |
|---|---|---|
| UDP 端口 | 56300 → 56301 | **57000 → 57000** |
| 点内时间 | 固定 5 µs/点（480 µs/包，200 kHz） | **动态**：设备时间戳差 ÷ 96 ≈ 2.2 µs |
| 每点尾部 2 B | 反射率 u8 + tag u8（tag 常见 0/128/192） | **float16 强度**，范围 2.0~28.0 dB |
| 空点占比 | ≈ 74% | ≈ 62% |
| 对应脚本 | `pcap_to_points.py` | `pcap_to_points_w18.py` |

> `pcap_to_points.py` 里端口是写死的 `56300→56301`，喂设备 B 的抓包会**一个点都读不出来**（不报错、输出只有表头）——这就是 `20131206-1-w18.pcap` 当初"不行"的原因。`pcap_to_points_w18.py` 改为按载荷特征识别（长度 1380 + 前缀 `00 64 05` + 点数域 = 96），与端口无关。

---

## 6. 已生成的产物

数据源均在 `E:\lidardata\`：

| 输出文件 | 大小 | 数据行数 | 源抓包 | 生成脚本 |
|---|---|---|---|---|
| `LIDAR/99999_points.csv` | 1.16 GB | 15,152,298 | `99999.pcapng` (862.9 MB) | `pcap_to_points.py` |
| `LIDAR/6666_points.csv` | 0.58 GB | 7,539,564 | `6666.pcapng` (465.2 MB) | `pcap_to_points.py` |
| `LIDAR/7777_points.csv` | 0.13 GB | 1,676,706 | `7777.pcapng` (290.1 MB) | `pcap_to_points.py` |
| `LIDAR/8888888_points.csv` | 0.34 GB | 4,406,202 | `8888888.pcapng` (703.2 MB) | `pcap_to_points.py` |
| `LIDAR/yyy_points.csv` | 2.30 GB | 30,182,435 | `yyy.pcapng` (1.95 GB) | `pcap_to_points.py` |
| `LIDAR/20131206-1-w18_points.csv` | 1.71 GB | 21,676,374 | `20131206-1-w18.pcap` (2.07 GB) | `pcap_to_points_w18.py` |
| **合计** | **≈ 6.2 GB** | **80,633,579（约 8063 万点）** | | |

> `99999.csv`（原逐包表，92 MB）与 `20131206-1-w18.csv`（逐包表，208.7 MB）在 `E:\dsh-output\`，本目录只放逐点数据。

---

## 7. 常见问题

**Q1：Excel 打不开 / 只显示 104 万行**
Excel 单表上限 1,048,576 行，本项目每个 CSV 都远超。用 `--split 1000000` 切片，或用 pandas / DuckDB 读：

```python
import pandas as pd

df = pd.read_csv(r"LIDAR/99999_points.csv")
```

**Q2：跑完只有表头，没有数据**
99% 是协议不匹配：端口、点数域、载荷长度对不上。先用 `pcap_to_csv.py` 看一眼流（`_flows.csv`），确认端口，再对照第 5.2 / 5.3 节改常量。

**Q3：报 `bad pcapng block length: 439041101`**
历史 bug：读完 4 字节魔数后没复位文件指针，导致块头读偏 4 字节，把字节序标记 `0x1A2B3C4D`（=439041101）当成了长度。已修复（判定为 pcapng 后 `fh.seek(0)`）。

**Q4：CSV 用记事本打开是乱码**
文件是 `utf-8-sig`（带 BOM），Excel 双击正常；若用记事本另存，别转成 GBK，否则 pandas 读取要指定 `encoding="gbk"`。

**Q5：时间戳怎么不是均匀的 / 多个包同一时间**
`20131206-1-w18` 那份抓包时间戳精度粗：约 104 万个包与前一包时间戳完全相同（平均每个时间戳 3.6 个包）。做时序分析时以设备时间戳（头 `[28:36]`）为准更可靠，脚本已用它计算点内偏移。

**Q6：PyCharm 变卡、索引很慢**
把 `.csv` 从索引里排除：右键 CSV → *Mark as Plain Text*；或在 `Settings → Editor → File Types` 里把 `*.csv` 从可识别类型移除。

---

## 8. 推送到 GitHub

仓库：`https://github.com/aixiaode1/csv123.`
分支：`master`，已与 `origin/master` 关联。

### 8.1 日常三行

```powershell
cd 'E:\pycharm project\pacp——csv'
git add -A
git commit -m "改了什么，一句话说清"
git push
```

### 8.2 提交前必看

```powershell
git status --short                 # 改了/新增了哪些文件
git ls-tree -r -l HEAD             # 已跟踪文件及大小，确认没有 GB 级文件
```

`.gitignore` 已排除 `*.csv` / `*.pcap` / `*.pcapng` / `*.lvx2` / `*.zip` / `.venv/` / `__pycache__/`，数据文件不会进仓库。

### 8.3 第一次推送失败的原因（踩坑记录）

- **现象**：`RPC failed; HTTP 408`、`send-pack: unexpected disconnect`、`the remote end hung up unexpectedly`
- **原因**：`Initial commit` 把 5 个逐点 CSV 一起提交了，合计 **10.5 GB**（最大的 `LIDAR/20131206-1-w18_points.csv` 5.8 GB）。GitHub 单文件上限 100 MB、仓库建议 2 GB 以内，必然上传超时。
- **处理**：

```powershell
git rm --cached -- "*.csv"                        # 从索引移除，磁盘文件保留
git add .gitignore
git commit --amend -m "Initial commit: ..."       # 重写那次提交
git reflog expire --expire=now --all
git gc --prune=now                               # 清掉 10.5 GB 历史对象
git push -u origin master
```

`.git` 体积从 3.47 GiB 降到 24 KiB。

### 8.4 报错对照表

| 报错 | 原因 | 处理 |
|---|---|---|
| `HTTP 408` / `remote end hung up` | 推送包过大或网络中断 | 用 8.2 查大文件，排除后重推 |
| `GH001: Large files detected` / 单文件 >100 MB | 超过 GitHub 硬限制 | `git rm --cached` + amend + gc，或改用 Git LFS |
| `Support for password authentication was removed` | 用了账号密码 | 改用 Personal Access Token 或 SSH |
| `Updates were rejected because the remote contains work` | 远端有新提交 | `git pull --rebase` 后再 `git push` |
| `fatal: not a git repository` | 目录不对 | `cd` 到 `E:\pycharm project\pacp——csv` |

### 8.5 换成 SSH（可选，避免反复认证）

```powershell
git remote set-url origin git@github.com:aixiaode1/csv123..git
```

> 仓库名末尾带了一个点（`csv123.`），所以 URL 里是 `csv123..git`；若之后在 GitHub 上改名，记得同步 `git remote set-url`。
> 数据文件（CSV / pcap）不要进 git：放网盘、Release 附件或学校服务器，仓库只保存代码与文档。

---

## 9. 三脚本流水线（导出 → 雷达 / IMU）

重构后的主线：**只解析一次 pcap**，后续所有加工都基于第 ① 步的 CSV。

```
pcap / pcapng
     │
     │ ① export_all.py
     ▼
  <名>_all.csv            ← 每包一行：No, Time, EpochTime, 五元组, UDPLen, PayloadHex(完整)
     │
     ├── ② export_radar.py  ──►  <名>_radar.csv    (WaterScenes radar 15 列，逐点)
     └── ③ export_imu.py    ──►  <名>_imu.csv      (WaterScenes imu 13 列，逐样本)
```

### 9.1 ① `database/export_all.py` —— 全量数据

| 项 | 说明 |
|---|---|
| 输入 | pcap / pcapng（默认 `E:\lidardata\99999.pcapng`） |
| 输出 | `E:\pycharm project\pacp——csv\all\<名>_all.csv`（`ALL_DIR` 常量控制） |
| 列 | `No, Time, EpochTime, SrcIP, SrcPort, DstIP, DstPort, UDPLen, PayloadHex` |
| 关键参数 | `--hex 0`（默认完整载荷，下游需要）/ `--hex 32` 缩小文件、`--start` `--duration` `--max-packets` |

### 9.2 ② `database/export_radar.py` —— 雷达点云

| 项 | 说明 |
|---|---|
| 输入 | ① 的 `*_all.csv`（`-i` 指定，缺省自动取最新） |
| 输出 | `E:\pycharm project\pacp——csv\LIDAR\<名>_radar.csv`（专用目录，脚本内 `LIDAR_DIR` 常量控制） |
| 识别方式 | 不写死端口：载荷 1380 B + 前缀 `00 64 05` + 点数域 96 |
| 点尾解析 | 端口 57000 → float16 强度；其它 → u8 反射率 + tag（自动判断） |
| 关键参数 | `--keep-zero`（保留空点）、`--split N`、`--max-points N` |

### 9.3 ③ `database/export_imu.py` —— IMU

| 项 | 说明 |
|---|---|
| 输入 | ① 的 `*_all.csv` |
| 输出 | `E:\pycharm project\pacp——csv\IMU\<名>_imu.csv`（专用目录，脚本内 `IMU_DIR` 常量控制） |
| 识别方式 | 载荷 60 B 且 `[1:3]` 长度域 = 60 |
| 关键参数 | `--gyro-deg`（角速度换 deg/s）、`--flat-attitude`（不反解姿态）、`--max-rows` |

IMU 输出列与 WaterScenes 的对应关系：

| 列 | 来源 | 说明 |
|---|---|---|
| `timestamp` | 抓包时间 | epoch 秒，6 位小数 |
| `pitch` / `roll` | 由加速度反解 | 单位度，静止可用；`--flat-attitude` 可关闭 |
| `yaw` | — | 留空（需要磁力计） |
| `angular_velocity_x/y/z` | 载荷 `[36:48]` | rad/s（`--gyro-deg` 可转 deg/s） |
| `linear_acceleration_x/y/z` | 载荷 `[48:60]` × 9.80665 | **m/s²**，与 WaterScenes 的 ≈9.84 同量级 |
| `magnetic_field_x/y/z` | — | 留空（本设备无磁力计） |

### 9.4 跑一遍

```bat
python export_all.py "E:\lidardata\99999.pcapng"
python export_radar.py
python export_imu.py
```

实测（99999 前 6 秒）：13,701 个 UDP 包 → `_all.csv` 34.2 MB → 雷达 318,994 点 / 25.0 MB，IMU 1,201 条 / 200 Hz。

---

## 10. IMU 预积分去畸变（`deskew\`）

> 本节是要点版；**公式推导、实现细节（带行号）、四层验证体系**详见专篇
> [deskew\原理与实现.md](<deskew\原理与实现.md>)。

**问题**：雷达是逐点顺序扫描的（设备 A 每点 5 µs、每包 96 点），扫描期间载体（船）
在动，每个点的 `x,y,z` 是在「打到这个点那一刻」的传感器坐标系里测出来的——整段
直接叠加 = 把不同时刻的坐标系混在一起，点云拖影、扭曲（运动畸变，类似相机的果冻效应）。

**做法**（Forster 式 IMU 预积分，`deskew\imu.py` 纯标准库实现）：

```
点流按补偿窗切分（--window，默认 0.1 s），每窗取参考时刻 t_ref（--ref start|mid|end）
   │
   ▼ 陀螺在 IMU 自己的 200 Hz 网格上积成相对旋转链 ΔR(t_ref→t)   ← 只积增量，不绑全局位姿
   │
   ▼ 逐点查表：点时刻 t_k 落在两个 IMU 样本之间 → 插值出 ΔR(t_ref→t_k)
   │
   ▼ p' = ΔR·p_k（--trans 再叠加 ∫∫(ΔR·a − g)dt 平移项）
   │
   ▼ 整窗点云 =「好像同一瞬间扫出来的」
```

### 10.1 输入输出

| 项 | 说明 |
|---|---|
| 输入 | `LIDAR\<名>_radar.csv` + `IMU\<名>_imu.csv`，同名自动配对（两流来自同一抓包，时间同源同钟） |
| 输出 1 | `deskew\data\<名>_radar_deskew.csv`：15 列与输入完全一致；`x,y,z` 为补偿后坐标，`range/az/el` 按新坐标重算，`timestamp` = 窗口参考时刻（整窗同一时刻） |
| 输出 2 | `deskew\data\<名>_radar_deskew_windows.csv`：逐窗统计（旋转角、点位移均值/最大值），分析与画图用 |

### 10.2 用法

```bat
:: 方式一【手动入口】：改 deskew\imu.py 顶部「输入输出入口」三个常量
::   （DEFAULT_INPUT / DEFAULT_IMU / DEFAULT_OUTPUT，留空 = 自动；
::    DEFAULT_OUTPUT 可填完整 CSV 路径，也可只填目录），直接运行
python "deskew\imu.py"                                          :: 常量留空时自动配对最新一对

:: 方式二【命令行，优先级高于常量】
python "deskew\imu.py" --selftest                             :: 合成数据回归测试
python "deskew\imu.py" -i "LIDAR\99999_radar.csv"             :: 自动配对同名 IMU
python "deskew\imu.py" -i "LIDAR\6666_radar.csv"              :: 全量去畸变（不截段）
python "deskew\imu.py" -i "LIDAR\6666_radar.csv" --trans --ref mid
python "deskew\plot_deskew.py" -i "LIDAR\6666_radar.csv"      :: 前后对比六联图 → photo\
```

| 参数 | 说明 |
|---|---|
| `--window SEC` | 补偿窗长度，默认 0.1 s |
| `--ref` | 参考时刻取窗的哪端：`start`（默认）/ `mid` / `end` |
| `--trans` | 追加平移补偿（加速度二次积分、速度初值 0、重力取窗内均值，只补振荡项） |
| `--gyro-bias WX,WY,WZ` | 扣除陀螺偏置（rad/s，可用静止段均值估计） |
| `--start` / `--duration` / `--max-points` | 截段 / 限点数，试跑用 |
| `-i` / `--imu` / `-o` | 显式指定雷达 / IMU 输入、输出；优先级：命令行 > 文件顶部常量 > 自动 |

### 10.3 验证（2026-11-23 实测）

| 验证 | 结果 |
|---|---|
| `--selftest` 合成数据（已知真值） | 旋转预积分 vs 解析解：误差 1.3e-10；平移预积分 2.9e-5 m（200 Hz 梯形离散误差量级）；端到端 4 例全过 |
| 静止对照 `99999`（6 s 全量，318,994 点） | 每窗旋转 0.10°（= 陀螺均值 0.018 rad/s × 0.1 s ✓），点位移均值 1.2 cm |
| `6666` 全量（150.2 s，7,539,564 点，1503 窗，79 s 跑完） | 每窗旋转均值 0.199°、最大 0.751°；点位移均值 3.85 cm、p95 13 cm、最大 90.8 cm；IMU 覆盖外仅 256 点（0.003%，原样保留） |
| 运动段 `6666` 20–35 s（1,034,810 点，151 窗） | 每窗旋转均值 0.62°、最大 0.75°；点位移均值 9.3 cm、p95 24 cm、最大 91 cm |
| 物理自洽 | 点位移实测 9.32 cm vs 理论 (θ/2)×range = 9.64 cm（平均距离 17.8 m），差 3% |
| `--trans` 在本数据段的贡献 | 与纯旋转逐点差均值 0.18 mm——旋转是主项，平移项要在更长窗/更晃时才有意义 |

对比图：`photo\6666_radar_deskew_cmp.png`——① 处理段俯视（灰=原始，红=去畸变）
② 旋转最猛的窗·去畸变前（点按窗内时延 Δt 着色，「时间彩虹」= 拖影）
③ 同窗·去畸变后（同一视野色标）④ 每窗旋转角/位移 ⑤ 旋转-位移相关（r=0.75）
⑥ 位移分布。

### 10.4 假设与限制

1. **外参 = 单位阵**：IMU 与雷达同壳体、轴对齐（两流来自同一设备同一抓包，时间同源）；真要对齐世界系还需 yaw/标定
2. **平移项**速度初值取 0、重力取窗内均值：匀速/匀加速平移补不了（没有速度观测，IMU-only 的本质限制），只补振荡项
3. **陀螺偏置**不建模（预积分理论里偏置可修，但需要外部观测来估计），可用 `--gyro-bias` 手工扣除
4. w18（设备 B）抓包没有 IMU 流，做不了去畸变
5. `power/label/instance` 等列原样透传，补偿只动 `x,y,z` 及其派生列

---

## 11. 点云聚类（`deskew\cluster.py`）

把去畸变后的点云按 0.1 s 时间窗切成"帧"，每帧用 DBSCAN 聚成物体，输出带
`instance` 簇号的新 CSV。规格：**KD-Tree 加速、eps 随距离自适应（近处 0.2 m →
远处 0.5 m）、minPts 5–10、小块按浪花/飞沫剔除**。

### 11.1 输入输出

| 项 | 内容 |
|---|---|
| 输入 | `deskew\data\<名>_radar_deskew.csv`（或任意 15 列雷达 CSV，含原始 `LIDAR\*_radar.csv`） |
| 输出 | `deskew\data\<名>_cluster.csv`：15 列不变，`instance` = 簇号（1..K 全局连续，0 = 噪声/浪花），其余列原样透传 |
| 统计 | `deskew\data\<名>_cluster_windows.csv`：逐窗点数、簇数、剔除小块数、噪声点数、最大簇 |

### 11.2 用法

方式一：改 `deskew\cluster.py` 顶部的 `DEFAULT_INPUT` / `DEFAULT_OUTPUT` 后直接运行
（`DEFAULT_OUTPUT` 可填 CSV 或目录；留空 = 与输入同目录）。

方式二：命令行

```bat
python "deskew\cluster.py" -i "deskew\data\6666_radar_deskew.csv"   :: 默认参数 = 规格值
python "deskew\cluster.py" --min-pts 5 --min-size 15                :: 放宽/收紧
python "deskew\cluster.py" --dims xy                               :: 2D 聚类（z 量化粗时更稳）
python "deskew\cluster.py" --drop-noise                            :: 噪声点不写入（物理剔除）
python "deskew\cluster.py" --selftest                              :: 合成数据回归测试
```

| 参数 | 默认 | 含义 |
|---|---|---|
| `--eps-near` / `--eps-far` | 0.2 / 0.5 m | 近处 / 远处 eps |
| `--r-near` / `--r-far` | 5 / 50 m | eps 起涨 / 到顶距离（中间线性插值） |
| `--min-pts` | 8 | DBSCAN minPts（规格 5–10） |
| `--min-size` | 10 | 簇内点数 < 它 → 判浪花（instance=0） |
| `--window` | 0.1 s | 时间窗（与去畸变一致，同窗点为一帧） |
| `--dims` | xyz | `xy` 切 2D |
| `--drop-noise` | 关 | 噪声 / 小块点不写入输出 |

### 11.3 为什么这样设计

- **eps 随距离自适应**：雷达点间距 ≈ 角分辨率 × 距离，随距离线性增长。固定 eps 在近处连片、
  远处打散。实现为 `eps(r) = clip(0.2 + 0.3·(r−5)/45, 0.2, 0.5)`，逐点用各自半径做
  KD-Tree 半径查询，再按 `d ≤ min(eps_i, eps_j)` 对称化——聚类结果与遍历顺序无关。
- **KD-Tree**：`scipy.spatial.cKDTree.query_ball_point` 支持**数组半径**（每点自己的 eps）。
- **DBSCAN 内核**：核心点之间的边建稀疏图，用 `scipy.sparse.csgraph.connected_components`
  求连通分量（等价于经典 DBSCAN 的密度可达传播，但走 C 内核，百万点秒级），
  边界点附着到相邻核心点所在分量。
- **小块剔除**：簇内点数 < `--min-size` 判为浪花/飞沫（`instance=0`，点保留；加
  `--drop-noise` 才物理删除）。
- **窗号用 round 不用 floor**：去畸变时间戳只保留 6 位小数，`floor((t−t0)/0.1)` 会把
  `m×0.1` 的微小误差判成上一窗（1503 窗会被误并成 1203 窗）。

### 11.4 验证（2026-11-23 实测）

`--selftest`（合成三簇 + 噪声 + 小块）：自适应 eps → 3 簇 `[112, 80, 60]` 且小块被剔除；
固定 eps=0.2 → 远簇被打散（只剩 2 簇），证明自适应是必要的；端到端 instance = {0,1,2,3}。

| 数据 | 点数 | 时间窗 | 簇 | 噪声/剔除 | 最大簇 | 耗时 |
|---|---|---|---|---|---|---|
| 7777 全量（93.7 s） | 1,676,706 | 938 | 2,534 | 92.4% | 150 | 18 s |
| 6666 全量（150.2 s） | 7,539,564 | 1,503 | 9,554 | 96.4% | 162 | 95 s |

参数敏感性（7777）：`--min-pts 5` → 6,750 簇 / 87.0% 噪声；`--min-pts 10` → 1,737 簇 / 93.8%；
`--dims xy` → 16,980 簇 / 67.0% 噪声（2D 会把海面杂波连成更多簇）。

**噪声率高是数据本身的稀疏性，不是参数错**：单窗内 47.5% 的点在 0.3 m 内没有任何邻居，
只有 5.2% 的点邻居数 ≥ minPts——毫米波回波本来就是稀疏散点，DBSCAN 只把真正密集的回波团
（浮标/船）聚成簇，其余判噪声正是"浪花/飞沫剔除"要做的事。

### 11.5 假设与限制

1. **只做空间聚类**：不用 doppler/power/反射率（要筛运动目标可后续加 doppler 门限）
2. **簇号不跨窗跟踪**：同一物体在不同窗是不同簇号（instance 只保证窗内唯一）
3. **3D 欧氏距离**：`--dims xy` 可切 2D——雷达 z 量化粗时 2D 更稳，但会把垂直分层的结构并在一起
4. **依赖 scipy**（KD-Tree）：已装进 `.venv`（scipy 1.18.1）；纯标准库环境跑不了（①–④ 仍是纯标准库）

---

## 12. 变更记录

| 日期 | 变更 |
|---|---|
| 2026-11-16 | 新增 `pcap_to_csv.py`，pcap/pcapng → 逐包 CSV + 五元组汇总 |
| 2026-11-16 | 修复 pcapng 解析指针未复位导致的 4 字节错位 bug |
| 2026-11-16 | 新增 `pcap_to_points.py`，设备 A 逐点数据 → WaterScenes 风格 CSV |
| 2026-11-16 | 新增 `pcap_to_points_w18.py`，支持设备 B（端口 57000、float16 强度、动态点间隔） |
| 2026-11-16 | 文档补充三个脚本的转换流程（第 4 节） |
| 2026-11-16 | 排除数据文件后推送成功，补充 GitHub 推送流程（第 8 节） |
| 2026-11-16 | 重构为三脚本流水线：`export_all` → `export_radar` / `export_imu`（第 9 节） |
| 2026-11-23 | 新增 `deskew\imu.py`：IMU 预积分点云去畸变（旋转 + 可选平移，含 `--selftest` 合成数据自检）；`plot_deskew.py` 生成前后对比六联图（第 10 节）；产物统一输出到 `deskew\data\` |
| 2026-11-23 | `deskew\imu.py` 新增「输入输出入口」常量（`DEFAULT_INPUT`/`DEFAULT_IMU`/`DEFAULT_OUTPUT`，命令行优先级更高）；目录更名 `去畸变\`→`deskew\`、其 `输出\`→`data\` |
| 2026-11-23 | 新增 `deskew\cluster.py`：点云 DBSCAN 聚类（KD-Tree、eps 随距离 0.2→0.5 m 自适应、minPts 8、小块浪花剔除，`--selftest` 自检；第 11 节）；`DEFAULT_OUTPUT` 支持目录写法 |
| 2026-11-23 | 修复 `deskew\imu.py` 的 `DEFAULT_OUTPUT` 填目录会崩溃（现在文件/目录两种写法都支持）；IMU 体检：5 份数据 200 Hz 无断流、陀螺 x/y 零偏 ≈ −0.0037/−0.0042 rad/s |
