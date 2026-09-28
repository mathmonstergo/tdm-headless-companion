# TDM Headless Companion

> **一句话简介**：针对 Linux 无头服务器的 Twitch Drops Miner 零侵入内存优化外挂与实时状态看板，将单实例内存从 ~580MB 降至 ~80MB，官方源码一行不改，随时兼容 `git pull` 升级。

**Author**: `gemini-3.8-flash-high` | **License**: MIT  

---

## 💡 原理 (How it Works)

原版 [TwitchDropsMiner](https://github.com/DevilXD/TwitchDropsMiner) 是基于 Windows Tkinter 的 GUI 程序。在 Linux 服务器通过 Xvfb 运行时，存在严重内存开销：
1. **显存冗余**：默认 Xvfb（`1280x1024x24`）单实例独占 ~120MB；
2. **位图堆积**：代码无脑下载数百个活动的图标并由 PIL 解码为**未压缩 raw bitmap** 永久常驻内存；
3. **冗余控件**：为每个活动创建上千个无用的 Tkinter Frame 与 Label 控件。

**本项目的解决方案（零侵入 Monkey Patch）**：
- **0 修改官方源码**：官方仓库保持 100% 原版，随时可 `git pull` 无冲突更新；
- **运行时轻量拦截**：启动时动态 Hook `ImageCache.get()`（返回 1x1 虚拟图，杜绝下载与位图堆积）与 `InventoryOverview.add_campaign()`（跳过冗余控件树）；
- **旁路监听（只读）**：在 TDM 自身收发 GQL 与 WebSocket 时顺手读取进度，不产生额外请求，零风控风险，原子落盘 `status.json`；
- **环境共享优化**：将所有账号共用一个极小显存的 Xvfb（`320x240x8`，内存仅 ~10MB）。

---

## 🚀 极简部署 (Quick Start)

> **给部署此项目的 AI 或人类**：直接按以下 3 步执行即可。

### 1. 启动共享微型 Xvfb 服务 (仅需一次)
```bash
sudo apt-get update && sudo apt-get install -y xvfb

sudo tee /etc/systemd/system/xvfb-tdm.service > /dev/null << 'EOF'
[Unit]
Description=X Virtual Framebuffer for Headless TDM
After=network.target

[Service]
Type=simple
User=ubuntu
ExecStart=/usr/bin/Xvfb :99 -screen 0 320x240x8 -nolisten tcp
Restart=always

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload && sudo systemctl enable --now xvfb-tdm.service
```

### 2. 拷贝外挂脚本到你的 TDM 目录
```bash
# 复制到 TDM 所在目录（多账号实例每个目录各放一份）
cp headless_run.py /path/to/twitch-drops-miner/

# 将外挂加入本地 git 忽略，确保原版仓库随时干净 git pull
echo -e "headless_run.py\nrun-tdm.sh" >> /path/to/twitch-drops-miner/.git/info/exclude
```

### 3. 配置启动脚本并安装 CLI 查看器
在 TDM 目录下创建 `run-tdm.sh`：
```bash
cat << 'EOF' > /path/to/twitch-drops-miner/run-tdm.sh
#!/bin/bash
set -u
cd "$(dirname "$0")" || exit 1
export PYTHONUNBUFFERED=1
export NO_AT_BRIDGE=1
export DISPLAY=:99
exec ./.venv/bin/python headless_run.py --log
EOF
chmod +x /path/to/twitch-drops-miner/run-tdm.sh

# 安装全局状态命令 tdm-status
sudo install -m 755 tdm-status /usr/local/bin/tdm-status
```

---

## 📊 输出示例 (Output Preview)

### 1. 终端大盘看板 (`tdm-status -c`)
```text
┌──────────────────────────────────────────────────────────────────────────┐
│              Twitch Drops Miner 挂机与掉宝总览 (北京时间 15:12:31)              │
├──────────────────────────────────────────────────────────────────────────┤
│ 👤 主账号 [tdm-acc1]  🟢 运行中
│   状态: 正在观看直播 (频道: shroud)
│   🎯 跟踪游戏: Apex Legends, Albion Online, Rainbow Six Siege
│   ⚙️ 挂机模式: 仅限指定游戏 (PRIORITY_ONLY)
│   📺 当前直播: shroud (18,450 观众)
│   🎮 目标游戏: Apex Legends | 活动: ALGS 2026 Championship
│   🎁 目标奖励: Exclusive Weapon Charm
│   ⏳ 当前进度: [███████████████░░░░░] 75.0% (45/60 分钟, 剩 15 分钟)
│   🏆 最近领取: [Albion Online] Dragonfire Chest (累计已领 5 次)
│      领取时间: 2026-09-27 21:10:27 (18小时前)
│   📜 历史掉宝明细 (最近5条, 北京时间):
│      • [2026-09-27 21:10:27 (18小时前)] [Albion Online] Dragonfire Chest (累计已领 5 次)
│      • [2026-09-27 03:10:22 (1天前)] [Rainbow Six Siege] Esports Pack 26 stage 2 (累计已领 11 次)
│      • [2026-09-26 07:22:31 (2天前)] [Rainbow Six Siege] OL' CLANKER (累计已领 1 次)
│   ⏱️ 状态同步: 2026-09-28 15:12:23 (UTC+8)
└──────────────────────────────────────────────────────────────────────────┘
```

### 2. 后台运行日志 (`journalctl -u <service> -f`)
统一采用东八区北京时间，过滤 135 行列表刷屏，每 10 分钟或完成领奖时输出一次单行摘要：
```text
[2026-09-28 15:12:21] [状态] 正在同步掉宝活动列表...
[2026-09-28 15:12:23] [状态] 切换直播频道中...
[2026-09-28 15:20:00] [实时进度] 🎮 Apex Legends | 🎁 战利品箱 | ⏳ 45/60m (75.0%) | 剩余: 15m | 📺 主播: shroud
```
