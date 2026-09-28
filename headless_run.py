"""
Twitch Drops Miner - Headless Memory Optimizer & Status Tracker
用于无头 Linux 服务器运行，不修改官方源码（随时兼容 git pull）：
1. 拦截 ImageCache.get：禁止下载和解码几百张活动图标并缓存到堆内存，返回 1x1 虚拟图片；
2. 拦截 InventoryOverview.add_campaign：跳过上千个冗余 Tkinter 控件树的创建；
3. 实时捕获挂机进度并输出到 status.json（所有日志与展示统一采用 UTC+8 东八区时间）；
4. 精准关联每个掉宝的具体游戏名称，清晰标注累计领取次数，杜绝数量歧义；
5. 日志降噪优化：过滤 135 行活动列表刷屏，挂机进度调整为每 10 分钟输出一次。
"""
import os
import re
import sys
import time
import json
import runpy
from pathlib import Path
from datetime import datetime, timezone, timedelta

# 东八区时区定义
CST = timezone(timedelta(hours=8))

def now_cst_str() -> str:
    return datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S")

def parse_iso_to_cst(iso_str: str) -> tuple[datetime | None, str]:
    if not iso_str:
        return None, ""
    try:
        # 支持各种变长微秒（如 .32Z, .077Z）
        m = re.match(r"^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})(?:\.(\d+))?Z?([+-]\d{2}:?\d{2})?$", iso_str)
        if m:
            date_part, time_part, frac, tz_part = m.groups()
            dt_base = datetime.strptime(f"{date_part} {time_part}", "%Y-%m-%d %H:%M:%S")
            dt_utc = dt_base.replace(tzinfo=timezone.utc)
            dt_cst = dt_utc.astimezone(CST)
            return dt_cst, dt_cst.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        pass
    return None, iso_str

# 提前导入并 Patch 相关模块
import cache
from PIL import Image as Image_module
from PIL.ImageTk import PhotoImage
import gui
import twitch
import inventory

# 1. 拦截图片缓存与下载
_dummy_photo = None

async def _fake_image_get(self, url, size=None):
    global _dummy_photo
    if _dummy_photo is None:
        _dummy_photo = PhotoImage(master=self._root, image=Image_module.new("RGBA", (1, 1), (0, 0, 0, 0)))
    return _dummy_photo

cache.ImageCache.get = _fake_image_get

# 2. 拦截活动界面繁琐控件创建
async def _fake_add_campaign(self, campaign):
    return

gui.InventoryOverview.add_campaign = _fake_add_campaign

# 3. 掉宝历史捕获与格式化
_cached_claims_info = {}

def _infer_game_name(event: dict, benefit_map: dict, priority_games: list[str]) -> str:
    eid = event.get("id", "")
    ename = event.get("name", "")
    link = event.get("requiredAccountLink", "")

    # 1. 优先从活动 benefit 字典精确命中
    if eid in benefit_map:
        return benefit_map[eid]

    # 2. 智能特征推导
    low_link = link.lower()
    low_id = eid.lower()
    low_name = ename.lower()

    if "albion" in low_link or "albion" in low_id or "dragonfire" in low_name:
        return "Albion Online"
    if "ea.com" in low_link or "apex" in low_name or "apex" in low_id:
        return "Apex Legends"
    if "ubi" in low_link or "r6s" in low_id or "rainbow" in low_id or "esports pack" in low_name:
        return "Rainbow Six Siege"
    if "minecraft" in low_link or "minecraft" in low_id or "clanker" in low_name or "coin here" in low_name:
        return "Minecraft"

    # 3. 尝试与用户配置的跟踪游戏模糊匹配
    for g in priority_games:
        if g.lower() in low_name or g.lower() in low_id:
            return g

    return "未知游戏"

def _parse_game_events(game_events: list, ongoing_camps: list, priority_games: list[str]):
    if not game_events:
        return None, []

    # 建立 benefit_id -> game_name 字典
    b_map = {}
    for camp in ongoing_camps:
        gname = camp.get("game", {}).get("name") if camp.get("game") else None
        if not gname:
            continue
        for d in camp.get("timeBasedDrops") or []:
            for b_edge in d.get("benefitEdges") or []:
                bid = b_edge.get("benefit", {}).get("id")
                if bid:
                    b_map[bid] = gname

    sorted_events = sorted(
        [e for e in game_events if e.get("lastAwardedAt")],
        key=lambda x: x["lastAwardedAt"],
        reverse=True
    )
    if not sorted_events:
        return None, []

    recent_list = []
    for it in sorted_events[:5]:
        raw_t = it.get("lastAwardedAt", "")
        _, cst_str = parse_iso_to_cst(raw_t)
        gname = _infer_game_name(it, b_map, priority_games)
        recent_list.append({
            "game": gname,
            "name": it.get("name", "未知奖励"),
            "awarded_at": cst_str,
            "raw_time": raw_t,
            "total_count": it.get("totalCount", 1)
        })

    latest_item = sorted_events[0]
    raw_latest_t = latest_item.get("lastAwardedAt", "")
    _, latest_cst_str = parse_iso_to_cst(raw_latest_t)
    latest_gname = _infer_game_name(latest_item, b_map, priority_games)

    latest_claim = {
        "game": latest_gname,
        "name": latest_item.get("name", "未知奖励"),
        "awarded_at": latest_cst_str,
        "raw_time": raw_latest_t,
        "total_count": latest_item.get("totalCount", 1)
    }
    return latest_claim, recent_list

# Hook gql_request 捕获 Inventory 数据
_orig_gql_request = twitch.Twitch.gql_request
async def _hooked_gql_request(self, operations):
    result = await _orig_gql_request(self, operations)
    try:
        res_list = [result] if isinstance(result, dict) else (result if isinstance(result, list) else [])
        for item in res_list:
            if isinstance(item, dict) and "data" in item:
                user = item["data"].get("currentUser")
                if user and isinstance(user, dict) and "inventory" in user:
                    inv = user["inventory"]
                    events = inv.get("gameEventDrops", [])
                    ongoing = inv.get("dropCampaignsInProgress", []) or []
                    p_games = list(getattr(self.settings, "priority", [])) if hasattr(self, "settings") else []
                    if events:
                        lat, rec = _parse_game_events(events, ongoing, p_games)
                        _cached_claims_info["latest_claim"] = lat
                        _cached_claims_info["recent_claims"] = rec
                        if hasattr(self, "gui"):
                            _write_status_file(self.gui)
    except Exception:
        pass
    return result

twitch.Twitch.gql_request = _hooked_gql_request

# Hook BaseDrop.claim 捕获实时领取事件
_orig_base_drop_claim = inventory.BaseDrop.claim
async def _hooked_base_drop_claim(self):
    res = await _orig_base_drop_claim(self)
    if res:
        try:
            now_str = now_cst_str()
            game_name = getattr(self.campaign.game, "name", "未知游戏")
            reward_name = self.rewards_text()
            _cached_claims_info["latest_claim"] = {
                "game": game_name,
                "name": reward_name,
                "awarded_at": now_str,
                "raw_time": datetime.now(timezone.utc).isoformat(),
                "total_count": 1
            }
            rec = _cached_claims_info.get("recent_claims", [])
            rec.insert(0, {
                "game": game_name,
                "name": reward_name,
                "awarded_at": now_str,
                "raw_time": datetime.now(timezone.utc).isoformat(),
                "total_count": 1
            })
            _cached_claims_info["recent_claims"] = rec[:5]
            if hasattr(self, "_twitch") and hasattr(self._twitch, "gui"):
                _write_status_file(self._twitch.gui)
        except Exception:
            pass
    return res

inventory.BaseDrop.claim = _hooked_base_drop_claim

# 4. 状态与进度追踪器 -> 实时落盘 status.json
STATUS_FILE = Path("status.json")
_last_progress_log_time = 0.0
_last_logged_completed = False

def _write_status_file(manager, drop=None, status_text=None, clear_drop=False):
    global _last_progress_log_time, _last_logged_completed
    try:
        status_data = {
            "pid": os.getpid(),
            "updated_at": now_cst_str(),
            "status_text": status_text or "",
            "priority_mode": "",
            "tracked_games": [],
            "watching": None,
            "drop": None,
            "latest_claim": _cached_claims_info.get("latest_claim"),
            "recent_claims": _cached_claims_info.get("recent_claims", []),
        }

        # 继承历史字段
        if STATUS_FILE.exists():
            try:
                with open(STATUS_FILE, "r", encoding="utf-8") as f:
                    old = json.load(f)
                    if not status_text:
                        status_data["status_text"] = old.get("status_text", "")
                    if drop is None and not clear_drop:
                        status_data["drop"] = old.get("drop")
                    if not status_data["watching"]:
                        status_data["watching"] = old.get("watching")
                    if not status_data["latest_claim"]:
                        status_data["latest_claim"] = old.get("latest_claim")
                    if not status_data["recent_claims"]:
                        status_data["recent_claims"] = old.get("recent_claims", [])
                    if not status_data["tracked_games"]:
                        status_data["tracked_games"] = old.get("tracked_games", [])
                    if not status_data["priority_mode"]:
                        status_data["priority_mode"] = old.get("priority_mode", "")
            except Exception:
                pass

        # 主播信息与配置信息
        twitch_inst = getattr(manager, "_twitch", None)
        if twitch_inst:
            if hasattr(twitch_inst, "settings"):
                st = twitch_inst.settings
                status_data["tracked_games"] = list(getattr(st, "priority", []))
                mode = getattr(st, "priority_mode", 0)
                mode_val = mode.value if hasattr(mode, "value") else int(mode)
                mode_names = {
                    0: "仅限指定游戏 (PRIORITY_ONLY)",
                    1: "即将结束优先 (ENDING_SOONEST)",
                    2: "可用性最低优先 (LOW_AVBL_FIRST)"
                }
                status_data["priority_mode"] = mode_names.get(mode_val, str(mode))

            if hasattr(twitch_inst, "watching_channel"):
                channel = twitch_inst.watching_channel.get_with_default(None)
                if channel:
                    stream = getattr(channel, "stream", None)
                    viewers = getattr(stream, "viewers", 0) if stream else 0
                    status_data["watching"] = {
                        "name": channel.name,
                        "url": getattr(channel, "url", ""),
                        "viewers": viewers,
                    }
                else:
                    status_data["watching"] = None

        # 进度信息
        if clear_drop:
            status_data["drop"] = None
            _last_logged_completed = False
        elif drop is not None:
            camp = getattr(drop, "campaign", None)
            game_name = camp.game.name if camp and hasattr(camp, "game") else ""
            camp_name = camp.name if camp else ""
            rewards = drop.rewards_text() if hasattr(drop, "rewards_text") else ""
            progress_pct = round(drop.progress * 100, 1) if hasattr(drop, "progress") else 0.0

            status_data["drop"] = {
                "game": game_name,
                "campaign": camp_name,
                "rewards": rewards,
                "progress_percent": progress_pct,
                "current_minutes": getattr(drop, "current_minutes", 0),
                "required_minutes": getattr(drop, "required_minutes", 0),
                "remaining_minutes": getattr(drop, "remaining_minutes", 0),
                "claimed": getattr(drop, "is_claimed", False),
            }

            now_ts = time.time()
            is_completed = getattr(drop, "is_claimed", False) or getattr(drop, "progress", 0.0) >= 1.0

            should_log = False
            if now_ts - _last_progress_log_time >= 600:
                should_log = True
            elif is_completed and not _last_logged_completed:
                should_log = True
                _last_logged_completed = True

            if should_log:
                _last_progress_log_time = now_ts
                stamp = now_cst_str()
                ch_name = status_data["watching"]["name"] if status_data["watching"] else "未知"
                print(
                    f"[{stamp}] [实时进度] 🎮 {game_name} | 🎁 {rewards} | "
                    f"⏳ {drop.current_minutes}/{drop.required_minutes}m ({progress_pct}%) | "
                    f"剩余: {drop.remaining_minutes}m | 📺 主播: {ch_name}",
                    flush=True
                )

        tmp_file = STATUS_FILE.with_suffix(".tmp")
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(status_data, f, ensure_ascii=False, indent=2)
        tmp_file.replace(STATUS_FILE)
    except Exception:
        pass


# 5. Hook GUI 关键方法
_orig_status_bar_init = gui.StatusBar.__init__
def _hooked_status_bar_init(self, manager, master):
    self._manager = manager
    return _orig_status_bar_init(self, manager, master)

gui.StatusBar.__init__ = _hooked_status_bar_init

_orig_gui_print = gui.GUIManager.print
def _hooked_gui_print(self, message: str):
    stamp = now_cst_str()
    print(f"[{stamp}] {message}", flush=True)
    return _orig_gui_print(self, message)

gui.GUIManager.print = _hooked_gui_print

_orig_display_drop = gui.GUIManager.display_drop
def _hooked_display_drop(self, drop, *, countdown=True, subone=False):
    _write_status_file(self, drop=drop)
    return _orig_display_drop(self, drop, countdown=countdown, subone=subone)

gui.GUIManager.display_drop = _hooked_display_drop

_orig_clear_drop = gui.GUIManager.clear_drop
def _hooked_clear_drop(self):
    _write_status_file(self, clear_drop=True)
    return _orig_clear_drop(self)

gui.GUIManager.clear_drop = _hooked_clear_drop

_last_adding_logged = False

_orig_status_update = gui.StatusBar.update
def _hooked_status_update(self, text: str):
    global _last_adding_logged
    if "Adding campaigns" in text or "添加至库存" in text:
        if not _last_adding_logged:
            _last_adding_logged = True
            stamp = now_cst_str()
            print(f"[{stamp}] [状态] 正在同步掉宝活动列表...", flush=True)
    else:
        _last_adding_logged = False
        stamp = now_cst_str()
        print(f"[{stamp}] [状态] {text}", flush=True)

    mgr = getattr(self, "_manager", None)
    if mgr:
        _write_status_file(mgr, status_text=text)
    return _orig_status_update(self, text)

gui.StatusBar.update = _hooked_status_update

_orig_set_watching = gui.ChannelList.set_watching
def _hooked_set_watching(self, channel):
    ret = _orig_set_watching(self, channel)
    _write_status_file(self._manager)
    return ret

gui.ChannelList.set_watching = _hooked_set_watching

_orig_clear_watching = gui.ChannelList.clear_watching
def _hooked_clear_watching(self):
    ret = _orig_clear_watching(self)
    _write_status_file(self._manager)
    return ret

gui.ChannelList.clear_watching = _hooked_clear_watching

if __name__ == "__main__":
    runpy.run_path("main.py", run_name="__main__")
