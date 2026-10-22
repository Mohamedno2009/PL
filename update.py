#!/usr/bin/env python3
# يجلب مباريات الدوري الإنجليزي ويُنشئ matches.json باللغة العربية.
import json
import os
import re
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

API_BASE = os.getenv("FOOTBALL_API_BASE", "https://api.matchgrind.space").rstrip("/")
LEAGUE = "eng.1"
TZ = ZoneInfo("Africa/Cairo")
OUT_FILE = "matches.json"
TIMEOUT = 25

TEAM_AR = {
    "Arsenal": "أرسنال", "Aston Villa": "أستون فيلا",
    "Bournemouth": "بورنموث", "Brentford": "برينتفورد",
    "Brighton": "برايتون", "Brighton and Hove Albion": "برايتون",
    "Burnley": "بيرنلي", "Chelsea": "تشيلسي",
    "Crystal Palace": "كريستال بالاس", "Everton": "إيفرتون",
    "Fulham": "فولهام", "Leeds United": "ليدز يونايتد",
    "Liverpool": "ليفربول", "Manchester City": "مانشستر سيتي",
    "Manchester United": "مانشستر يونايتد",
    "Newcastle United": "نيوكاسل يونايتد",
    "Nottingham Forest": "نوتنغهام فورست",
    "Sunderland": "سندرلاند", "Tottenham Hotspur": "توتنهام",
    "West Ham United": "وست هام يونايتد",
    "Wolverhampton Wanderers": "وولفرهامبتون",
}
STATUS_AR = {
    "scheduled": "لم تبدأ", "not started": "لم تبدأ", "pre": "لم تبدأ",
    "in progress": "جارية", "in_progress": "جارية", "live": "جارية",
    "first half": "الشوط الأول", "first_half": "الشوط الأول",
    "second half": "الشوط الثاني", "second_half": "الشوط الثاني",
    "halftime": "استراحة بين الشوطين", "half time": "استراحة بين الشوطين",
    "break": "استراحة بين الشوطين", "extra time": "وقت إضافي",
    "penalties": "ركلات ترجيح", "full time": "انتهت", "finished": "انتهت",
    "final": "انتهت", "ft": "انتهت", "postponed": "مؤجلة",
    "cancelled": "أُلغيت", "canceled": "أُلغيت", "abandoned": "أُلغيت",
}

def fetch_json(url):
    req = Request(url, headers={"Accept": "application/json", "User-Agent": "PremierLeagueJSON/1.0"})
    with urlopen(req, timeout=TIMEOUT) as response:
        if response.status < 200 or response.status >= 300:
            raise RuntimeError(f"مصدر البيانات أعاد HTTP {response.status}")
        return json.loads(response.read().decode("utf-8"))

def first(obj, *keys):
    if not isinstance(obj, dict):
        return None
    for key in keys:
        val = obj.get(key)
        if val is not None:
            return val
    return None

def name_of(value):
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        return str(first(value, "name", "displayName", "shortName", "teamName") or "").strip()
    return ""

def arabic_team(name):
    return TEAM_AR.get(name, name or "غير معروف")

def list_from_response(data):
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("matches", "fixtures", "events", "results", "data", "items"):
            value = data.get(key)
            if isinstance(value, list):
                return value
            if isinstance(value, dict):
                for nested in ("matches", "fixtures", "events", "items"):
                    if isinstance(value.get(nested), list):
                        return value[nested]
    raise RuntimeError("لم أتعرف على بنية رد API. افتح سجل GitHub Actions لمعرفة التفاصيل.")

def parse_date(value):
    if not value:
        return None
    if isinstance(value, (int, float)):
        # timestamps may be seconds or milliseconds
        value = value / 1000 if value > 10_000_000_000 else value
        return datetime.fromtimestamp(value, timezone.utc)
    s = str(value).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None

def status_of(match, now, kickoff):
    raw = str(first(match, "status", "state", "matchStatus", "statusText") or "").strip().lower()
    detail = first(match, "status", "state")
    if isinstance(detail, dict):
        raw = str(first(detail, "name", "description", "shortName", "type") or raw).lower()
    minute = first(match, "minute", "elapsed", "elapsedMinutes")
    # Some APIs return a status code object/string.
    for key, translated in STATUS_AR.items():
        if raw == key or key in raw:
            return translated, minute, raw
    if kickoff and now < kickoff:
        return "لم تبدأ", minute, raw
    # Do not label a past kickoff as live without an explicit live status.
    if raw:
        return "جارية", minute, raw
    return "تبدأ قريبًا", minute, raw

def score_value(match, side):
    score = first(match, "score", "scores") or {}
    side_obj = first(match, side, "homeTeam" if side == "home" else "awayTeam") or {}
    names = ("homeScore", "home_score", "home") if side == "home" else ("awayScore", "away_score", "away")
    candidates = []
    if isinstance(match, dict):
        candidates.extend([match.get(names[0]), match.get(names[1])])
    if isinstance(score, dict):
        candidates.extend([score.get(names[2]), score.get(names[0]), score.get(names[1])])
        ft = score.get("fullTime") or score.get("full_time") or {}
        if isinstance(ft, dict):
            candidates.append(ft.get(side))
    if isinstance(side_obj, dict):
        candidates.extend([side_obj.get("score"), side_obj.get("goals")])
    for val in candidates:
        if isinstance(val, dict):
            val = first(val, "displayValue", "value", "total")
        if val is not None and str(val).strip() != "":
            try:
                return int(val)
            except (ValueError, TypeError):
                pass
    return None

def venue_name(match):
    venue = first(match, "venue", "stadium", "location") or {}
    if isinstance(venue, str):
        return venue
    return name_of(venue) or first(match, "venueName", "stadiumName") or None

def goals_from_match(match):
    raw = first(match, "goals", "scorers", "events", "plays") or []
    if isinstance(raw, dict):
        raw = raw.get("items", raw.get("events", []))
    if not isinstance(raw, list):
        return []
    result = []
    for event in raw:
        if not isinstance(event, dict):
            continue
        event_type = str(first(event, "type", "kind", "text", "name") or "").lower()
        if "goal" not in event_type and "هدف" not in event_type and not first(event, "scorer", "player", "athlete"):
            continue
        player = first(event, "player", "scorer", "athlete") or {}
        team = first(event, "team", "teamName") or {}
        minute = first(event, "minute", "elapsed", "clock")
        if isinstance(minute, dict):
            minute = first(minute, "minute", "displayValue")
        extra = first(event, "extraMinute", "extra_minute")
        minute_text = str(minute) if minute is not None else None
        if minute_text and extra:
            minute_text += f"+{extra}"
        detail = str(first(event, "detail", "goalType", "text") or "").lower()
        kind = "هدف عكسي" if ("own" in detail or event.get("ownGoal")) else ("ركلة جزاء" if ("penalty" in detail or event.get("penalty")) else "هدف")
        result.append({
            "اللاعب": name_of(player) or (player if isinstance(player, str) else "غير معروف"),
            "الفريق": arabic_team(name_of(team)),
            "الدقيقة": minute_text,
            "نوع_الهدف": kind
        })
    return result

def remaining(kickoff, now, live):
    if live:
        return "المباراة جارية"
    if kickoff is None:
        return None
    seconds = int((kickoff - now).total_seconds())
    if seconds <= 0:
        return "تبدأ قريبًا"
    minutes = seconds // 60
    days, rem = divmod(minutes, 1440)
    hours, mins = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days} يوم")
    if hours:
        parts.append(f"{hours} ساعة")
    if mins or not parts:
        parts.append(f"{mins} دقيقة")
    return " و".join(parts)

def normalize(match, now):
    if not isinstance(match, dict):
        return None
    home = first(match, "homeTeam", "home_team", "home") or {}
    away = first(match, "awayTeam", "away_team", "away") or {}
    home_name, away_name = name_of(home), name_of(away)
    kickoff = parse_date(first(match, "date", "kickoff", "startTime", "start_time", "utcDate", "startDate"))
    if not home_name or not away_name or kickoff is None:
        return None

    state, minute, raw_status = status_of(match, now, kickoff)
    # Include only not-finished matches. Postponed/cancelled are excluded.
    if state in ("انتهت", "أُلغيت", "مؤجلة"):
        return None
    if state == "لم تبدأ" and now >= kickoff:
        state = "تبدأ قريبًا"
    live = state in ("جارية", "الشوط الأول", "الشوط الثاني", "استراحة بين الشوطين", "وقت إضافي", "ركلات ترجيح")
    # If a fixture has kicked off but API does not provide status, be cautious.
    if now >= kickoff and not live and state != "تبدأ قريبًا":
        state = "تبدأ قريبًا"

    local = kickoff.astimezone(TZ)
    mid = first(match, "id", "eventId", "fixtureId", "uid")
    return {
        "معرف_المباراة": str(mid) if mid is not None else "",
        "الفريق_المضيف": arabic_team(home_name),
        "الفريق_الضيف": arabic_team(away_name),
        "التاريخ": local.strftime("%Y-%m-%d"),
        "وقت_البداية": local.strftime("%H:%M"),
        "موعد_البداية": local.isoformat(),
        "حالة_المباراة": state,
        "النتيجة": {
            "أهداف_الفريق_المضيف": score_value(match, "home"),
            "أهداف_الفريق_الضيف": score_value(match, "away")
        },
        "دقيقة_المباراة": minute,
        "مسجلو_الأهداف": goals_from_match(match),
        "الملعب": venue_name(match),
        "الوقت_المتبقي": remaining(kickoff, now, live)
    }

def main():
    now = datetime.now(timezone.utc)
    url = f"{API_BASE}/get/soccer/{LEAGUE}/fixtures?{urlencode({'status': 'all'})}"
    try:
        raw = fetch_json(url)
        source_matches = list_from_response(raw)
        matches = []
        for item in source_matches:
            normalized = normalize(item, now)
            if normalized:
                matches.append(normalized)
        matches.sort(key=lambda x: x["موعد_البداية"])
        output = {
            "اسم_الدوري": "الدوري الإنجليزي الممتاز",
            "اللغة": "العربية",
            "المنطقة_الزمنية": "Africa/Cairo",
            "آخر_تحديث": now.astimezone(TZ).isoformat(),
            "عدد_المباريات": len(matches),
            "المباريات": matches
        }
        with open(OUT_FILE, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
        print(f"تم تحديث {OUT_FILE}: {len(matches)} مباراة غير منتهية.")
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, RuntimeError) as exc:
        print(f"فشل تحديث بيانات المباريات: {exc}", file=sys.stderr)
        # Do not overwrite a previous good JSON with fake/empty data.
        sys.exit(1)

if __name__ == "__main__":
    main()
