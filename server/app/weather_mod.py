"""Studio 的天气模块（独立实现，不依赖 Garden Action 服务）。

为什么不用「调 Garden 的 API」：
  隔离原则 —— 两个服务不应互相依赖。Garden 挂了不能拖累 Studio，
  Studio 也不该吃 Garden 的接口额度。

数据来源：心知天气（Seniverse），凭据从环境变量读取。
  ★ 无密钥时返回「演示数据」并明确标注，绝不伪装成真实天气。
  ★ 失败时降级为演示数据，不让首页崩掉。

缓存策略：10 分钟内复用，避免每次刷新都打 API。
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

from app.cache_layer import CACHE_KEYS, CACHE_TTL, LayeredCache

CST = timezone(timedelta(hours=8))

#: 心知天气的城市名映射（中文名比拼音更稳）
CITY_NAMES = {
    "shanghai": "上海",
    "hangzhou": "杭州",
    "suzhou": "苏州",
    "nanjing": "南京",
    "ningbo": "宁波",
    "wuxi": "无锡",
    "hefei": "合肥",
}

#: 天气现象 → 卡片主题
THEME_MAP = {
    "晴": "sun",
    "多云": "cloud",
    "阴": "cloud",
    "雾": "cloud",
    "霾": "cloud",
}


def _theme_for(condition: str, precip: float) -> str:
    if precip and precip >= 30:
        return "rain"
    if condition in ("雨", "小雨", "中雨", "大雨", "暴雨", "雷阵雨", "阵雨", "雪"):
        return "rain"
    return THEME_MAP.get(condition, "cloud")


class WeatherModule:
    """极简天气服务：取数 + 缓存 + 降级。"""

    def __init__(self) -> None:
        self.key = os.getenv("WEATHER_API_KEY", "").strip()
        self.enabled = os.getenv("WEATHER_LIVE_ENABLED", "false").lower() in ("1", "true", "yes")
        self.city = os.getenv("DEFAULT_CITY_ID", "shanghai").strip() or "shanghai"
        self.timeout = float(os.getenv("WEATHER_TIMEOUT_SECONDS", "5"))
        # TTL 可由环境变量覆盖，默认 10 分钟
        env_ttl = int(os.getenv("WEATHER_CACHE_TTL_MINUTES", "10")) * 60
        self.ttl = env_ttl or CACHE_TTL["weather"]
        self.cache = LayeredCache()
        #: 最近一次取数的来源，供诊断端点读取
        self.last_source = "unknown"

    # ── 对外接口 ──
    def current(self) -> dict[str, Any]:
        key = CACHE_KEYS["weather"](self.city)

        # 没有密钥 / 未启用：走演示数据，短 TTL，绝不污染真实数据槽
        if not (self.enabled and self.key):
            demo, src = self.cache.load(
                key + ":demo",
                CACHE_TTL["demo"],
                lambda: self._demo(),
            )
            self.last_source = "demo"
            return demo if demo is not None else self._demo()

        def loader() -> dict[str, Any] | None:
            return self._fetch_live()

        value, source = self.cache.load(
            key,
            self.ttl,
            loader,
            should_cache=lambda v: bool(v.get("ok")) and not v.get("isDemo"),
        )
        self.last_source = source

        if value is not None:
            out = dict(value)
            # stale 时明确标注：前端可以据此提示"数据可能不是最新"
            if source == "stale":
                out["stale"] = True
                out["notice"] = "天气服务暂时不可用，显示的是上次成功获取的数据。"
            return out

        # 上游失败且无旧值：降级演示，并明确标注
        self.last_source = "fallback-demo"
        return self._demo()

    # ── 诊断 ──
    def diagnostics(self) -> dict[str, Any]:
        return {
            "city": self.city,
            "enabled": self.enabled,
            "hasKey": bool(self.key),
            "ttl": self.ttl,
            "lastSource": self.last_source,
            "cache": self.cache.stats.as_dict(),
        }

    def invalidate(self) -> None:
        self.cache.clear()

    # ── 真实数据 ──
    def _fetch_live(self) -> dict[str, Any] | None:
        city_cn = CITY_NAMES.get(self.city, "上海")
        params = urllib.parse.urlencode({
            "key": self.key,
            "location": city_cn,
            "language": "zh-Hans",
            "unit": "c",
        })
        url = f"https://api.seniverse.com/v3/weather/now.json?{params}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "studio/1.0"})
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                data = json.loads(r.read())

            res = data["results"][0]
            now_block = res["now"]
            loc = res["location"]

            temp = float(now_block["temperature"])
            text = now_block.get("text", "")

            # ★ 心知免费版 weather/now.json 只返回 text/code/temperature，
            #   没有湿度和降水。缺失时返回 None（页面不显示该项），
            #   绝不伪造成 0 —— 那会误导成「湿度 0%」。
            humidity = now_block.get("humidity")
            humidity = float(humidity) if humidity not in (None, "") else None
            precip = now_block.get("precipitation")
            precip = float(precip) if precip not in (None, "") else None

            # 有降水量才推算概率；没有就留空，不猜
            prob = None
            if precip is not None:
                prob = 10.0 if precip <= 0 else min(95.0, 60 + precip * 8)

            return {
                "ok": True,
                "source": "live",
                "isDemo": False,
                "cityName": CITY_NAMES.get(self.city, loc.get("name", "")),
                "condition": text or "未知",
                "temperature": temp,
                "humidity": humidity,
                "precipitationMm": precip,
                "precipProbability": prob,
                "theme": _theme_for(text, prob),
                "observedAt": _to_cst(data["results"][0].get("last_update", "")),
                "notice": "天气数据来自心知天气（Seniverse）。",
            }
        except (urllib.error.URLError, urllib.error.HTTPError, KeyError, ValueError, IndexError, TypeError):
            return None

    # ── 演示数据（明确标注，不冒充真实） ──
    def _demo(self) -> dict[str, Any]:
        return {
            "ok": True,
            "source": "demo",
            "isDemo": True,
            "cityName": CITY_NAMES.get(self.city, "上海"),
            "condition": "示例",
            "temperature": 20.0,
            "humidity": None,
            "precipitationMm": None,
            "precipProbability": None,
            "theme": "cloud",
            "observedAt": datetime.now(CST).strftime("%Y-%m-%dT%H:%M:%S+08:00"),
            "notice": "演示数据：未配置天气 API 密钥，这不是真实天气。",
        }


def _to_cst(value: str) -> str:
    """把心知返回的 ISO 时间转成 +08:00（心知默认给的是带偏移的 ISO）。"""
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.astimezone(CST).strftime("%Y-%m-%dT%H:%M:%S+08:00")
    except (ValueError, AttributeError):
        return datetime.now(CST).strftime("%Y-%m-%dT%H:%M:%S+08:00")


#: 全局单例
weather = WeatherModule()
