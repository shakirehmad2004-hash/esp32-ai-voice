import os
import re
import time
import uuid
import urllib.parse
import xml.etree.ElementTree as ET
import datetime
from typing import Optional
import httpx
import edge_tts
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse

app = FastAPI()

AUDIO_DIR = "/tmp/tts_audio"
os.makedirs(AUDIO_DIR, exist_ok=True)

RAW_KEYS = [
    k.strip()
    for k in (
        os.getenv("GROQ_API_KEY", "") + "," + os.getenv("GROQ_API_KEY_2", "")
    ).split(",")
    if k.strip()
]

GROQ_MODELS = [
    "llama-3.1-8b-instant",
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "qwen/qwen3-32b",
    "openai/gpt-oss-20b",
    "openai/gpt-oss-120b",
]

STATE = {
    "voice": "UR-M",
    "speed": "Norm",
    "volume": 15,
}

VOICE_MAP = {
    "UR-M": "ur-PK-AsadNeural",
    "UR-F": "ur-PK-UzmaNeural",
    "HI-M": "hi-IN-MadhurNeural",
    "HI-F": "hi-IN-SwaraNeural",
    "EN-M": "en-US-GuyNeural",
    "EN-F": "en-US-JennyNeural",
}

SPEED_MAP = {
    "Slow": "-20%",
    "Norm": "+0%",
    "Fast": "+20%",
}

MEEQAT_ANCHORS = [
    (1,  1,  (6, 8),  (7, 37), (12, 35), (15, 55), (17, 36), (19, 1)),
    (1,  15, (6, 10), (7, 36), (12, 41), (16, 8),  (17, 48), (19, 12)),
    (2,  1,  (6, 3),  (7, 29), (12, 45), (16, 23), (18, 4),  (19, 27)),
    (2,  15, (5, 53), (7, 18), (12, 45), (16, 36), (18, 17), (19, 38)),
    (2,  28, (5, 40), (7, 3),  (12, 44), (16, 46), (18, 29), (19, 49)),
    (3,  1,  (5, 38), (7, 1),  (12, 44), (16, 47), (18, 30), (19, 50)),
    (3,  15, (5, 19), (6, 43), (12, 40), (16, 57), (18, 41), (20, 2)),
    (3,  31, (4, 56), (6, 21), (12, 36), (17, 5),  (18, 54), (20, 16)),
    (4,  1,  (4, 55), (6, 19), (12, 35), (17, 5),  (18, 54), (20, 16)),
    (4,  15, (4, 33), (6, 1),  (12, 32), (17, 11), (19, 5),  (20, 30)),
    (4,  30, (4, 13), (5, 44), (12, 28), (17, 17), (19, 16), (20, 45)),
    (5,  1,  (4, 12), (5, 43), (12, 28), (17, 17), (19, 17), (20, 47)),
    (5,  15, (3, 54), (5, 30), (12, 27), (17, 21), (19, 28), (21, 2)),
    (5,  31, (3, 39), (5, 22), (12, 27), (17, 27), (19, 39), (21, 18)),
    (6,  1,  (3, 39), (5, 21), (12, 29), (17, 28), (19, 40), (21, 19)),
    (6,  15, (3, 36), (5, 19), (12, 31), (17, 31), (19, 46), (21, 28)),
    (6,  30, (3, 39), (5, 23), (12, 35), (17, 35), (19, 50), (21, 31)),
    (7,  1,  (3, 39), (5, 23), (12, 35), (17, 35), (19, 50), (21, 31)),
    (7,  15, (3, 50), (5, 30), (12, 37), (17, 35), (19, 46), (21, 25)),
    (7,  31, (4, 6),  (5, 41), (12, 37), (17, 31), (19, 37), (21, 11)),
    (8,  1,  (4, 7),  (5, 42), (12, 37), (17, 32), (19, 36), (21, 9)),
    (8,  15, (4, 22), (5, 52), (12, 36), (17, 23), (19, 22), (20, 51)),
    (8,  31, (4, 36), (6, 3),  (12, 34), (17, 11), (19, 4),  (20, 28)),
    (9,  1,  (4, 39), (6, 4),  (12, 31), (17, 9),  (19, 1),  (20, 24)),
    (9,  15, (4, 50), (6, 12), (12, 26), (16, 54), (18, 43), (20, 5)),
    (9,  30, (5, 2),  (6, 24), (12, 21), (16, 36), (18, 21), (19, 41)),
    (10, 1,  (5, 3),  (6, 25), (12, 21), (16, 35), (18, 19), (19, 40)),
    (10, 15, (5, 14), (6, 35), (12, 17), (16, 19), (18, 1),  (19, 22)),
    (10, 31, (5, 26), (6, 49), (12, 15), (16, 2),  (17, 43), (19, 5)),
    (11, 1,  (5, 27), (6, 50), (12, 15), (16, 2),  (17, 42), (19, 4)),
    (11, 15, (5, 38), (7, 3),  (12, 16), (15, 51), (17, 32), (18, 56)),
    (11, 24, (5, 46), (7, 11), (12, 18), (15, 47), (17, 27), (18, 51)),
    (11, 30, (5, 50), (7, 16), (12, 20), (15, 45), (17, 26), (18, 50)),
    (12, 1,  (5, 51), (7, 17), (12, 20), (15, 45), (17, 26), (18, 50)),
    (12, 15, (6, 0),  (7, 30), (12, 26), (15, 46), (17, 27), (18, 52)),
    (12, 31, (6, 8),  (7, 37), (12, 34), (15, 53), (17, 35), (19, 0)),
]

DAYS_BEFORE = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]

SURAH_NAMES = {
    "fatiha": 1, "fatihah": 1, "baqarah": 2, "baqara": 2, "imran": 3,
    "yasin": 36, "yaseen": 36, "rahman": 55, "waqiah": 56, "mulk": 67,
    "ikhlas": 112, "falaq": 113, "naas": 114, "nas": 114,
}


def get_ist_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=5, minutes=30)))


def fmt_12(mins: int) -> str:
    mins = max(0, min(1439, int(round(mins))))
    h24, m = divmod(mins, 60)
    ap = "AM" if h24 < 12 else "PM"
    h12 = h24 % 12 or 12
    return f"{h12}:{m:02d} {ap}"


def fmt_urdu_time(mins: int) -> str:
    mins = max(0, min(1439, int(round(mins))))
    h24, m = divmod(mins, 60)
    period = "subah" if h24 < 12 else ("dopahar" if h24 < 16 else "shaam")
    h12 = h24 % 12 or 12
    return f"{h12} baj kar {m} minute {period}"


def get_today_meeqat():
    now = get_ist_now()
    target = DAYS_BEFORE[now.month - 1] + now.day
    for i in range(len(MEEQAT_ANCHORS) - 1):
        m1, d1, f1, sr1, z1, a1, mg1, e1 = MEEQAT_ANCHORS[i]
        m2, d2, f2, sr2, z2, a2, mg2, e2 = MEEQAT_ANCHORS[i + 1]
        dy1 = DAYS_BEFORE[m1 - 1] + d1
        dy2 = DAYS_BEFORE[m2 - 1] + d2
        if dy1 <= target <= dy2:
            r = 0.0 if dy2 == dy1 else (target - dy1) / (dy2 - dy1)
            vals = []
            for p1, p2 in [(f1, f2), (sr1, sr2), (z1, z2), (a1, a2), (mg1, mg2), (e1, e2)]:
                t1 = p1[0] * 60 + p1[1]
                t2 = p2[0] * 60 + p2[1]
                vals.append(int(round(t1 + r * (t2 - t1))))
            return vals
    last = MEEQAT_ANCHORS[-1]
    return [p[0] * 60 + p[1] for p in last[2:]]


def extract_surah_and_ayah(text: str) -> tuple[Optional[int], Optional[int]]:
    low = text.lower()
    surah_num = None
    ayah_num = None

    m_surah = re.search(r"(?:surah|sura|سوره|سورہ)\s*(\d{1,3})", low)
    if m_surah:
        surah_num = max(1, min(114, int(m_surah.group(1))))
    else:
        for k, v in SURAH_NAMES.items():
            if k in low:
                surah_num = v
                break

    m_ayah = re.search(r"(?:ayah|ayat|verse|آیت|ایت)\s*(\d{1,3})", low)
    if m_ayah:
        ayah_num = max(1, int(m_ayah.group(1)))
    elif surah_num is not None:
        nums = [int(x) for x in re.findall(r"\b(\d{1,3})\b", low)]
        if len(nums) >= 2:
            ayah_num = nums[1]

    return surah_num, ayah_num


async def fetch_tafseer_fallback(query: str) -> tuple[str, str]:
    surah_num, ayah_num = extract_surah_and_ayah(query)
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            if surah_num and ayah_num:
                url = f"https://api.alquran.cloud/v1/ayah/{surah_num}:{ayah_num}/en.asad"
                r = await client.get(url)
                if r.status_code == 200:
                    data = r.json().get("data", {})
                    txt = (data.get("text") or "").strip()
                    s_name = data.get("surah", {}).get("englishName", f"Surah {surah_num}")
                    if txt:
                        short_t = re.sub(r"\s+", " ", txt)[:180]
                        disp = f"{s_name} {surah_num}:{ayah_num}. {short_t[:80]}"
                        spk = f"{s_name} ayat {ayah_num} ka mafhoom hai: {short_t}"
                        return disp[:120], spk
            elif surah_num:
                url = f"https://api.alquran.cloud/v1/surah/{surah_num}/en.asad"
                r = await client.get(url)
                if r.status_code == 200:
                    data = r.json().get("data", {})
                    s_name = data.get("englishName", f"Surah {surah_num}")
                    rev = data.get("revelationType", "")
                    ay_cnt = data.get("numberOfAyahs", "")
                    first_ay = ""
                    ayahs = data.get("ayahs") or []
                    if ayahs:
                        first_ay = re.sub(r"\s+", " ", (ayahs[0].get("text") or ""))[:140]
                    disp = f"{s_name} ({rev}, {ay_cnt} ayat). {first_ay[:70]}"
                    spk = (
                        f"{s_name} mein kul {ay_cnt} aayat hain aur yeh {rev} hai. "
                        f"Pehli ayat ka mafhoom hai: {first_ay}."
                    )
                    return disp[:120], spk
    except Exception as ex:
        print(f"Tafseer fallback error: {ex}")

    msg = "Kisi bhi Surah ya ayat ki tafseer ke liye Surah aur ayat number bolein, jaise Surah 2 ayat 255 ya Surah 1."
    return msg[:120], msg


async def fetch_weather_fallback() -> tuple[str, str]:
    try:
        url = (
            "https://api.open-meteo.com/v1/forecast"
            "?latitude=34.0837&longitude=74.7973"
            "&current=temperature_2m,weather_code,wind_speed_10m"
            "&timezone=Asia%2FKolkata"
        )
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.get(url)
            if r.status_code == 200:
                cur = r.json().get("current", {})
                temp = cur.get("temperature_2m")
                wind = cur.get("wind_speed_10m")
                if temp is not None:
                    disp = f"Srinagar Temp: {temp}C. Wind: {wind} km/h."
                    spk = f"Srinagar aur Shopian ke aas-paas abhi darja hararat {temp} degree Celsius aur hawa {wind} kilometer fi ghanta hai."
                    return disp, spk
    except Exception as ex:
        print(f"Weather fallback error: {ex}")
    return "Srinagar weather unavailable.", "Abhi mausam ki maloomat haasil nahi ho saki."


async def fetch_news_fallback() -> tuple[str, str]:
    feeds = [
        "https://feeds.bbci.co.uk/news/world/asia/rss.xml",
        "https://www.aljazeera.com/xml/rss/all.xml",
    ]
    for feed_url in feeds:
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                r = await client.get(feed_url)
                if r.status_code == 200:
                    root = ET.fromstring(r.text)
                    titles = []
                    for item in root.findall(".//item")[:2]:
                        t = item.findtext("title")
                        if t:
                            titles.append(re.sub(r"\s+", " ", t).strip())
                    if titles:
                        joined = " | ".join(titles)
                        return joined[:120], f"Taza khabrein: {joined}"
        except Exception as ex:
            print(f"News fallback error ({feed_url}): {ex}")
    return "Live news unavailable.", "Abhi taza khabrein load nahi ho saki."


async def fetch_general_fallback(query: str) -> tuple[str, str]:
    low = query.lower()
    if any(w in low for w in ["tafseer", "tafsir", "ayat", "ayah", "surah", "sura", "quran", "تفسیر", "آیت", "قرآن", "سورہ"]):
        return await fetch_tafseer_fallback(query)

    clean_q = re.sub(r"\b(kya|hai|hain|ka|ki|ke|what|is|are|who|tell|me|batao|bataiye|maloomat)\b", "", query, flags=re.I).strip()
    clean_q = re.sub(r"\s+", " ", clean_q) or query.strip()

    try:
        encoded = urllib.parse.quote(clean_q[:80])
        url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{encoded}"
        async with httpx.AsyncClient(timeout=8.0, headers={"User-Agent": "ESP32VoiceAssistant/1.0"}) as client:
            r = await client.get(url)
            if r.status_code == 200:
                extract = (r.json().get("extract") or "").strip()
                if extract:
                    sents = re.split(r"(?<=[.!?])\s+", extract)
                    short_ans = " ".join(sents[:2]).strip()
                    short_ans = re.sub(r"[^\x20-\x7E]", " ", short_ans)
                    short_ans = re.sub(r"\s+", " ", short_ans).strip()
                    if short_ans:
                        return short_ans[:120], short_ans[:240]
    except Exception as ex:
        print(f"General fallback error: {ex}")

    msg = "AI token limit aa gayi hai, lekin Tafseer, Quran, Mausam, Live News, Namaz timing, aur Time abhi bhi chal rahe hain."
    return msg[:120], msg


async def make_tts(text: str) -> str:
    fid = uuid.uuid4().hex[:10]
    out_path = os.path.join(AUDIO_DIR, f"{fid}.mp3")
    voice = VOICE_MAP.get(STATE["voice"], "ur-PK-AsadNeural")
    rate = SPEED_MAP.get(STATE["speed"], "+0%")
    clean_text = re.sub(r"[*_#`~]+", "", text).strip() or "Ji bataiye."
    comm = edge_tts.Communicate(clean_text, voice=voice, rate=rate)
    await comm.save(out_path)
    now_t = time.time()
    for fn in os.listdir(AUDIO_DIR):
        fp = os.path.join(AUDIO_DIR, fn)
        try:
            if os.path.isfile(fp) and now_t - os.path.getmtime(fp) > 300:
                os.remove(fp)
        except Exception:
            pass
    return f"/audio/{fid}"


async def transcribe_wav(wav_bytes: bytes) -> str:
    if not RAW_KEYS:
        return ""
    for key in RAW_KEYS:
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                files = {"file": ("audio.wav", wav_bytes, "audio/wav")}
                data = {"model": "whisper-large-v3-turbo"}
                resp = await client.post(
                    "https://api.groq.com/openai/v1/audio/transcriptions",
                    headers={"Authorization": f"Bearer {key}"},
                    files=files,
                    data=data,
                )
                if resp.status_code == 200:
                    return (resp.json().get("text") or "").strip()
                print(f"Whisper failed ({resp.status_code}): {resp.text[:160]}")
        except Exception as e:
            print(f"Whisper error: {e}")
    return ""


async def ask_llm(user_text: str) -> tuple[str, str]:
    now = get_ist_now()
    f, sr, z, a, mg, e = get_today_meeqat()
    low = user_text.lower()

    if any(w in low for w in ["namaz", "nimaz", "meeqat", "miqat", "fajr", "zohar", "asar", "maghrib", "esha", "isha", "prayer", "نماز", "میقات"]):
        disp = f"Fajr {fmt_12(f)}. Zohar {fmt_12(z)}. Asar {fmt_12(a)}. Maghrib {fmt_12(mg)}. Esha {fmt_12(e)}."
        spk = (
            f"Aaj Srinagar aur Shopian ke namaz ke auqat hain: "
            f"Fajr {fmt_urdu_time(f)}, Zohar {fmt_urdu_time(z)}, Asar {fmt_urdu_time(a)}, "
            f"Maghrib {fmt_urdu_time(mg)}, aur Esha {fmt_urdu_time(e)}."
        )
        return disp, spk

    if any(w in low for w in ["time", "waqt", "baj", "baje", "وقت", "بج"]) and "namaz" not in low:
        cur_mins = now.hour * 60 + now.minute
        disp = f"Abhi waqt {fmt_12(cur_mins)} hai."
        spk = f"Abhi muqami waqt {fmt_urdu_time(cur_mins)} hai."
        return disp, spk

    if any(w in low for w in ["mausam", "mosam", "weather", "barish", "garmi", "sardi", "temp", "temperature", "موسم"]):
        return await fetch_weather_fallback()

    if any(w in low for w in ["news", "khabar", "khabrein", "headlines", "live news", "خبر", "خبریں"]):
        return await fetch_news_fallback()

    is_tafseer = any(w in low for w in ["tafseer", "tafsir", "ayat", "ayah", "تفسیر", "آیت"])

    if RAW_KEYS:
        if is_tafseer:
            sys_prompt = (
                "You are an Islamic scholar providing clear, accurate Quran Tafseer in simple Roman Urdu/Hindi. "
                "Explain the meaning, context, and key lesson concisely in 2 to 3 short sentences. No markdown or symbols."
            )
        else:
            sys_prompt = (
                f"You are a helpful voice assistant in Srinagar, Kashmir. "
                f"Date: {now.strftime('%d %b %Y')}, Time: {now.strftime('%I:%M %p')}. "
                f"Reply clearly in 2 short sentences using Roman Urdu/Hindi. No markdown or symbols."
            )

        for key in RAW_KEYS:
            for model in GROQ_MODELS:
                try:
                    async with httpx.AsyncClient(timeout=18.0) as client:
                        resp = await client.post(
                            "https://api.groq.com/openai/v1/chat/completions",
                            headers={
                                "Authorization": f"Bearer {key}",
                                "Content-Type": "application/json",
                            },
                            json={
                                "model": model,
                                "messages": [
                                    {"role": "system", "content": sys_prompt},
                                    {"role": "user", "content": user_text},
                                ],
                                "max_tokens": 180 if is_tafseer else 140,
                                "temperature": 0.35 if is_tafseer else 0.4,
                            },
                        )
                        if resp.status_code == 200:
                            ans = resp.json()["choices"][0]["message"]["content"].strip()
                            ans = re.sub(r"<think>.*?</think>", "", ans, flags=re.S).strip()
                            return ans[:120], ans
                        print(f"Model {model} failed ({resp.status_code}): {resp.text[:160]}")
                except Exception as ex:
                    print(f"Model {model} exception: {ex}")

    return await fetch_general_fallback(user_text)


@app.get("/")
async def root():
    return {"status": "ok", "voice": STATE["voice"], "speed": STATE["speed"], "volume": STATE["volume"]}


@app.get("/audio/{fid}")
async def get_audio(fid: str):
    safe_id = re.sub(r"[^a-zA-Z0-9]", "", fid)
    fp = os.path.join(AUDIO_DIR, f"{safe_id}.mp3")
    if os.path.exists(fp):
        return FileResponse(fp, media_type="audio/mpeg")
    return JSONResponse({"error": "not found"}, status_code=404)


@app.post("/chat")
async def chat(request: Request):
    wav_bytes = await request.body()
    heard = await transcribe_wav(wav_bytes)
    print(f"Heard: {heard}")

    if not heard:
        msg = "Awaaz saaf sunai nahi di."
        audio_url = await make_tts(msg)
        return {
            "action": "SPEAK",
            "heard": "",
            "display": msg,
            "audio_url": audio_url,
            "voice": STATE["voice"],
            "speed": STATE["speed"],
            "volume": STATE["volume"],
        }

    low = heard.lower()
    if any(k in low for k in ["stop", "ruko", "band karo", "بند کرو", "رکو"]):
        return {"action": "STOP", "heard": heard, "display": "Stopped.", "voice": STATE["voice"], "speed": STATE["speed"], "volume": STATE["volume"]}

    if any(k in low for k in ["mute", "awaaz band", "آواز بند"]):
        STATE["volume"] = 0
        return {"action": "MUTE", "heard": heard, "display": "Muted.", "voice": STATE["voice"], "speed": STATE["speed"], "volume": 0}

    if "volume up" in low or "awaaz badhao" in low or "آواز بڑھاؤ" in low:
        STATE["volume"] = min(21, STATE["volume"] + 3)
    elif "volume down" in low or "awaaz kam" in low or "آواز کم" in low:
        STATE["volume"] = max(3, STATE["volume"] - 3)

    if "naat" in low or "نعت" in low:
        audio_url = await make_tts("SD card se Naat chala rahe hain.")
        return {
            "action": "SPEAK",
            "heard": heard,
            "display": "Naat (SD Card)",
            "audio_url": audio_url,
            "voice": STATE["voice"],
            "speed": STATE["speed"],
            "volume": STATE["volume"],
        }

    is_tafseer = any(w in low for w in ["tafseer", "tafsir", "ayat", "ayah", "تفسیر", "آیت"])
    surah_num, ayah_num = extract_surah_and_ayah(low)

    if not is_tafseer and (surah_num is not None or "quran" in low or "قرآن" in low):
        num = surah_num or 1
        stream_url = f"https://server8.mp3quran.net/afs/{num:03d}.mp3"
        return {
            "action": "STREAM",
            "heard": heard,
            "title": f"SURAH {num:03d}",
            "url": stream_url,
            "voice": STATE["voice"],
            "speed": STATE["speed"],
            "volume": STATE["volume"],
        }

    disp, spk = await ask_llm(heard)
    audio_url = await make_tts(spk)
    return {
        "action": "SPEAK",
        "heard": heard,
        "display": disp,
        "audio_url": audio_url,
        "voice": STATE["voice"],
        "speed": STATE["speed"],
        "volume": STATE["volume"],
    }
