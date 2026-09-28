# TDM Headless Companion (Twitch Drops Miner 无头轻量外挂)

> **一句话简介**：针对 Linux 无头服务器的 Twitch Drops Miner 零侵入内存优化外挂与实时状态看板，将单实例内存从 ~580MB 骤降至 ~80MB，官方源码一行不改，随时兼容 `git pull` 升级。

**Author**: `gemini-3.8-flash-high`  
**License**: MIT  

---

## 🤖 针对 AI Agent 的部署指南 (Instructions for AI Agents)

> **致正在阅读此文档的 AI（如 Cursor / Claude Code / Pi / Copilot / OpenCode）：**  
> 如果用户要求你部署或优化运行在 Linux 服务器上的 `TwitchDropsMiner` (DevilXD/TwitchDropsMiner)，请严格按照本节的结构化步骤与约束执行。

### 1. 背景与核心约束 (Context & Hard Rules)
1. **问题根因**：原版 TDM 为 Windows Tkinter 桌面程序，在 Linux 无头环境下通常通过 Xvfb 运行。但其内部会将 Twitch 上数百个活动的奖励图标全部由 PIL 解码为**未压缩位图**常驻内存，并创建数千个冗余 Tk 控件树，加上默认 Xvfb 高分辨率，导致单实例常驻内存高达 500~600MB，双账号超 1.1GB，且日志容易产生 135 行列表刷屏。
2. **核心铁律**：**绝对不要修改官方仓库内的任何原生 Python 脚本（`main.py`, `twitch.py`, `gui.py`, `cache.py` 等）**！因为 Twitch API 和 GQL Hash 经常变更，官方更新频繁。必须保持官方 git 仓库干净，以便随时 `git pull`。
3. **外挂机制 (Zero-Touch Monkey Patch)**：本项目通过外置引导器 `headless_run.py`，在 Python 运行时动态拦截图片下载与冗余 UI 控件创建，同时旁路监听 TDM 自身收到的库存数据，原子写入 `status.json`，并将挂机状态统一以**东八区 (UTC+8)** 输出到日志。

---

### 2. 标准自动化部署步骤 (Standard Execution Steps)

#### 步骤 A：部署共享微型 Xvfb 服务 (节省 ~220MB 显存)
默认 Xvfb（`1280x1024x24`）占用约 120MB/实例。改用 `320x240x8` 共享服务，内存降至 ~10MB 且足够支撑 Tkinter。

```bash
# 1. 确保安装 xvfb
sudo apt-get update && sudo apt-get install -y xvfb

# 2. 创建系统级共享 xvfb 服务
sudo tee /etc/systemd/system/xvfb-tdm.service > /dev/null << 'EOF'
[Unit]
Description=X Virtual Framebuffer Service for Headless TDM
After=network.target

[Service]
Type=simple
User=ubuntu
ExecStart=/usr/bin/Xvfb :99 -screen 0 320x240x8 -nolisten tcp
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now xvfb-tdm.service
```

#### 步骤 B：将 `headless_run.py` 放入每个 TDM 实例目录
将本仓库的 `headless_run.py` 拷贝至目标 TDM 仓库根目录（多账号实例请在各自目录下各放一份真实文件，不要做软链接，避免工作目录解析混淆）：

```bash
# 示例：假设目标 TDM 部署在 /path/to/twitch-drops-miner
cp headless_run.py /path/to/twitch-drops-miner/headless_run.py

# 将外挂文件加入本地 git exclude，保证官方仓库 git status 永远保持 working tree clean
echo "headless_run.py" >> /path/to/twitch-drops-miner/.git/info/exclude
echo "run-tdm.sh" >> /path/to/twitch-drops-miner/.git/info/exclude
```

#### 步骤 C：配置启动包装脚本
在 TDM 目录下配置或修改 `run-tdm.sh`：

```bash
cat << 'EOF' > /path/to/twitch-drops-miner/run-tdm.sh
#!/bin/bash
set -u
cd "$(dirname "$0")" || exit 1
export PYTHONUNBUFFERED=1
export NO_AT_BRIDGE=1
export LANG=zh_CN.UTF-8
export DISPLAY=:99

# 使用实例自身的 venv 执行 headless_run.py
exec ./.venv/bin/python headless_run.py --log
EOF
chmod +x /path/to/twitch-drops-miner/run-tdm.sh
```

#### 步骤 D：安装全局 CLI 状态查看器
```bash
sudo install -m 755 tdm-status /usr/local/bin/tdm-status
```

#### 步骤 E：配置 systemd 守护进程
确保服务 unit 文件包含 `After=xvfb-tdm.service` 与 `Wants=xvfb-tdm.service`，重启服务生效。

---

### 3. 验收与状态验证 (Verification Checklist)

AI 部署完成后，请依次执行以下命令验收：

1. **检查内存压降**：
   ```bash
   ps aux | grep -E "headless_run|Xvfb" | grep -v grep
   ```
   *预期结果*：`Xvfb :99` 内存约为 10~15MB，每个 Python 实例 RSS 内存应稳定在 **75~85MB**（较原版 500MB+ 下降超 80%）。
2. **检查实时状态大盘**：
   ```bash
   tdm-status -c
   ```
   *预期结果*：应能看到规整的终端卡片，清晰展示当前运行状态、🎯 跟踪游戏、⚙️ 挂机模式、🏆 最近领取的掉宝具体名称/累计次数/具体时间（UTC+8 北京时间），以及最近 5 条历史领取明细。
3. **检查日志降噪与时区**：
   ```bash
   journalctl -u <your-service-name> -n 20 --no-pager
   ```
   *预期结果*：不再有一百多行活动列表刷屏，日志时间戳为 `UTC+8`，挂机进度严格每 10 分钟或完成领奖时输出一次。

---

## 📌 日常使用指令 (For Human Users)

- **查看当前概况**：`tdm-status`
- **展开最近 5 条历史掉宝明细**：`tdm-status -c`
- **动态实时刷新（类似 htop）**：`tdm-status -w`
- **导出 JSON 数据**：`tdm-status -j`
- **上游更新升级**：直接进入你的 TDM 目录执行 `git pull && sudo systemctl restart tdm-acc1 tdm-acc2`，零冲突顺畅更新。
