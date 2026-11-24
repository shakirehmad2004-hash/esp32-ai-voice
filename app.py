import io
import os
import re
import json
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from fastapi import FastAPI, Request, Response
import httpx
import edge_tts
from gtts import gTTS

app = FastAPI()

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()

NAAT_STREAMS = [
    "https://stream.zeno.fm/0r0xa792kwzuv",
    "https://stream.zeno.fm/4c2x9m8z3v8uv",
    "http://stream.zeno.fm/0r0xa792kwzuv",
]

MUSIC_STREAMS = [
    "https://stream.zeno.fm/rqqps63h4k8uv",
    "https://stream.zeno.fm/f3wvbbtr7g8uv",
    "http://stream.zeno.fm/rqqps63h4k8uv",
]

VOICES = {
    "ur_male":   {"tts": "ur-PK-AsadNeural",     "ar": "ar-SA-HamedNeural",   "stt": "ur", "label": "UR-M"},
    "ur_female": {"tts": "ur-PK-UzmaNeural",     "ar": "ar-SA-ZariyahNeural", "stt": "ur", "label": "UR-F"},
    "hi_male":   {"tts": "hi-IN-MadhurNeural",   "ar": "ar-SA-HamedNeural",   "stt": "hi", "label": "HI-M"},
    "hi_female": {"tts": "hi-IN-SwaraNeural",    "ar": "ar-SA-ZariyahNeural", "stt": "hi", "label": "HI-F"},
    "en_male":   {"tts": "en-IN-PrabhatNeural",  "ar": "ar-SA-HamedNeural",   "stt": "en", "label": "EN-M"},
    "en_female": {"tts": "en-IN-NeerjaNeural",   "ar": "ar-SA-ZariyahNeural", "stt": "en", "label": "EN-F"},
}

state = {
    "voice": "ur_male",
    "rate": "+0%",
    "speed_label": "Norm",
    "volume": 13,
    "history": []
}

audio_cache = {}
MAX_CACHE = 6


def get_ist_time() -> str:
    ist = timezone(timedelta(hours=5, minutes=30))
    return datetime.now(ist).strftime("%I:%M %p, %A, %d %B %Y (IST)")


def clean_ascii(text: str) -> str:
    if not text:
        return ""
    repl = {
        "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-", "\u2014": " - ",
        "\u2018": "'", "\u2019": "'", "\u201c": "", "\u201d": "", "\u2026": "...",
        "\u00b7": "-", "\u2022": "-"
    }
    for k, v in repl.items():
        text = text.replace(k, v)
    out = "".join(ch if 32 <= ord(ch) <= 126 else " " for ch in text)
    return re.sub(r"\s+", " ", out).strip()


def weather_code_desc(code: int) -> str:
    mapping = {
        0: "saaf aasman",
        1: "zyada tar saaf",
        2: "halkay badal",
        3: "ghane badal",
        45: "kohra / dhund",
        48: "ghana kohra",
        51: "halki boonda baandi",
        53: "darmiyani boonda baandi",
        61: "halki barish",
        63: "darmiyani barish",
        65: "tez barish",
        71: "halki barfbari",
        73: "darmiyani barfbari",
        75: "tez barfbari",
        80: "barish ke jhonkay",
        95: "garaj chamak ke saath barish",
    }
    return mapping.get(code, f"code {code}")


async def fetch_live_weather(city: str = "Srinagar") -> str:
    q = (city or "Srinagar").strip()
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            geo = await client.get(
                "https://geocoding-api.open-meteo.com/v1/search",
                params={"name": q, "count": 1, "language": "en", "format": "json"}
            )
            gdata = geo.json()
            if not gdata.get("results"):
                geo = await client.get(
                    "https://geocoding-api.open-meteo.com/v1/search",
                    params={"name": "Srinagar", "count": 1, "language": "en", "format": "json"}
                )
                gdata = geo.json()
            if not gdata.get("results"):
                return ""
            loc = gdata["results"][0]
            lat = loc["latitude"]
            lon = loc["longitude"]
            cname = loc.get("name", q)

            w = await client.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": lat,
                    "longitude": lon,
                    "current": "temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m",
                    "timezone": "Asia/Kolkata"
                }
            )
            cur = w.json().get("current", {})
            temp = cur.get("temperature_2m")
            feels = cur.get("apparent_temperature")
            hum = cur.get("relative_humidity_2m")
            wind = cur.get("wind_speed_10m")
            desc = weather_code_desc(int(cur.get("weather_code", 0)))
            return (
                f"Live weather in {cname}: {temp} C (feels like {feels} C), "
                f"condition: {desc}, humidity: {hum}%, wind: {wind} km/h."
            )
    except Exception as e:
        print("Weather fetch error:", repr(e))
        return ""


async def fetch_rss_headlines(url: str, limit: int = 5) -> list[str]:
    items = []
    try:
        async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
            r = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code == 200:
                root = ET.fromstring(r.text)
                for item in root.findall(".//item")[:limit]:
                    title_el = item.find("title")
                    if title_el is not None and title_el.text:
                        t = re.sub(r"\s+", " ", title_el.text).strip()
                        if t:
                            items.append(t)
    except Exception as e:
        print("RSS error:", repr(e))
    return items


async def fetch_live_cricket() -> str:
    lines = []
    try:
        async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
            r = await client.get(
                "https://site.api.espn.com/apis/site/v2/sports/cricket/scoreboard",
                headers={"User-Agent": "Mozilla/5.0"}
            )
            if r.status_code == 200:
                data = r.json()
                for ev in data.get("events", [])[:6]:
                    name = ev.get("name", "")
                    status_txt = ev.get("status", {}).get("type", {}).get("detail", "")
                    comps = ev.get("competitions", [{}])[0].get("competitors", [])
                    scores = []
                    for c in comps:
                        tname = c.get("team", {}).get("shortDisplayName") or c.get("team", {}).get("displayName", "")
                        sc = c.get("score", "")
                        if tname:
                            scores.append(f"{tname} {sc}".strip())
                    summary = f"{name} | {' vs '.join(scores)} | {status_txt}".strip(" |")
                    if summary:
                        lines.append(summary)
    except Exception as e:
        print("ESPN cricket feed error:", repr(e))

    if not lines:
        rss = await fetch_rss_headlines("https://feeds.bbci.co.uk/sport/cricket/rss.xml", 4)
        lines.extend(rss)

    if not lines:
        return "NO_LIVE_CRICKET_FEED"
    return "Live cricket scoreboard feed: " + " || ".join(lines)


async def fetch_live_news() -> str:
    headlines = await fetch_rss_headlines("https://feeds.bbci.co.uk/news/world/asia/india/rss.xml", 4)
    if not headlines:
        headlines = await fetch_rss_headlines("https://feeds.bbci.co.uk/news/world/rss.xml", 4)
    if not headlines:
        return ""
    return "Latest news headlines: " + " | ".join(headlines)


async def pick_working_stream(urls: list[str]) -> str:
    async with httpx.AsyncClient(timeout=6.0, follow_redirects=True) as client:
        for u in urls:
            try:
                r = await client.get(u, headers={"Range": "bytes=0-1024", "User-Agent": "Mozilla/5.0"})
                if r.status_code in (200, 206):
                    return str(r.url)
            except Exception:
                continue
    return urls[0]


async def synth_edge_segment(text: str, voice_name: str, rate: str) -> bytes:
    buf = bytearray()
    comm = edge_tts.Communicate(text=text, voice=voice_name, rate=rate)
    async for chunk in comm.stream():
        if chunk["type"] == "audio":
            buf.extend(chunk["data"])
    return bytes(buf)


async def generate_mp3(speak_text: str, voice_key: str, rate: str) -> bytes:
    vcfg = VOICES.get(voice_key, VOICES["ur_male"])
    parts = re.split(r"(\[AR:[^\]]+\])", speak_text)
    full_mp3 = bytearray()

    try:
        for p in parts:
            p = p.strip()
            if not p:
                continue
            if p.startswith("[AR:") and p.endswith("]"):
                ar_inner = p[4:-1].split("|")[0].strip()
                if ar_inner:
                    seg = await synth_edge_segment(ar_inner, vcfg["ar"], rate)
                    full_mp3.extend(seg)
            else:
                seg = await synth_edge_segment(p, vcfg["tts"], rate)
                full_mp3.extend(seg)

        if len(full_mp3) > 400:
            return bytes(full_mp3)
    except Exception as e:
        print("Edge-TTS error, falling back to gTTS:", repr(e))

    plain = re.sub(r"\[AR:([^\]|]+)(?:\|[^\]]+)?\]", r"\1", speak_text)
    lang_code = vcfg["stt"]
    fp = io.BytesIO()
    gTTS(text=plain, lang=lang_code).write_to_fp(fp)
    return fp.getvalue()


def store_audio(mp3_bytes: bytes) -> str:
    aid = uuid.uuid4().hex[:10]
    audio_cache[aid] = mp3_bytes
    while len(audio_cache) > MAX_CACHE:
        oldest = next(iter(audio_cache))
        audio_cache.pop(oldest, None)
    return f"/audio/{aid}"


async def transcribe_wav(wav_bytes: bytes, stt_lang: str) -> str:
    prompt_text = (
        "Aaj kis cricket ka match hai, live score, weather mausam Srinagar, news, "
        "Quran tafseer, volume set karo, volume badhao, stop, Urdu voice, Hindi voice, English voice."
    )
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}"}
    files = {"file": ("speech.wav", wav_bytes, "audio/wav")}
    data = {
        "model": "whisper-large-v3-turbo",
        "prompt": prompt_text,
        "response_format": "json"
    }
    async with httpx.AsyncClient(timeout=25.0) as client:
        r = await client.post("https://api.groq.com/openai/v1/audio/transcriptions", headers=headers, data=data, files=files)
        if r.status_code != 200:
            print("Whisper Error:", r.status_code, r.text)
            return ""
        return r.json().get("text", "").strip()


async def ask_llm(user_text: str, live_context: str = "") -> dict:
    vkey = state["voice"]
    if vkey.startswith("ur"):
        lang_rule = (
            "Write 'speak' in PURE, NATURAL, EVERYDAY SPOKEN URDU using Urdu script (Nastaliq UTF-8). "
            "Write 'display' in clear, complete ASCII Roman Urdu/English sentences separated by periods (.) so each sentence shows cleanly one after another on OLED."
        )
    elif vkey.startswith("hi"):
        lang_rule = (
            "Write 'speak' in natural everyday Hindi using Devanagari script. "
            "Write 'display' in clear, complete ASCII Roman Hindi sentences separated by periods (.)."
        )
    else:
        lang_rule = (
            "Write both 'speak' and 'display' in clear, natural, complete English sentences separated by periods (.)."
        )

    live_block = f"\nLIVE FETCHED DATA:\n{live_context}\n" if live_context else ""

    system_prompt = f"""You are a highly accurate, direct AI voice assistant in Srinagar, India.
Current local time: {get_ist_time()}.
Active settings: voice={vkey}, volume={state['volume']}/18, speed={state['speed_label']}.
{live_block}
Respond ONLY with a valid JSON object containing exact keys: "cmd", "speak", "display".

1) COMMANDS (set "cmd" ONLY when explicitly requested; otherwise "cmd": ""):
- Quran Tilawat / recitation ONLY: "QURAN:XXX" (3-digit Surah 001-114). NEVER use QURAN for Tafseer (تفسیر), Tarjuma (ترجمہ), meaning, or explanation!
- Naat stream: "RADIO_NAAT"
- Music stream: "RADIO_MUSIC"
- Stop playback: "STOP"
- Volume: "VOL:UP", "VOL:DOWN", "VOL:SET:NN" (0-18), "VOL:MUTE"
- Voice: "VOICE:UR_MALE", "VOICE:UR_FEMALE", "VOICE:HI_MALE", "VOICE:HI_FEMALE", "VOICE:EN_MALE", "VOICE:EN_FEMALE"
- Speed: "SPEED:SLOW", "SPEED:FAST", "SPEED:NORMAL"
- Clear memory: "CLEAR_MEM"

2) STRICT ACCURACY & RELEVANCE RULES:
- {lang_rule}
- Directly answer the EXACT question asked by the user. Never give rankings or unrelated facts when asked about live matches/scores.
- If LIVE FETCHED DATA contains "NO_LIVE_CRICKET_FEED", explicitly say that live match/score feed is not available right now—NEVER guess or invent scores/matches.
- If LIVE FETCHED DATA has actual matches/scores/weather/news, report only what is present in LIVE FETCHED DATA accurately.
- For Quran Tafseer, Islamic topics, science, history, and all other questions: give exact, verified, concise sentences without guessing.
- Keep "display" clean ASCII (no *, #, emojis, or non-ASCII symbols) and split into 2 to 4 clear short sentences separated by '. '."""

    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(state["history"][-10:])
    messages.append({"role": "user", "content": user_text})

    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }

    for model_id in ["llama-3.3-70b-versatile", "openai/gpt-oss-120b", "llama-3.1-8b-instant"]:
        payload = {
            "model": model_id,
            "messages": messages,
            "temperature": 0.05,
            "max_tokens": 1000,
            "response_format": {"type": "json_object"}
        }
        async with httpx.AsyncClient(timeout=25.0) as client:
            r = await client.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=payload)
            if r.status_code == 200:
                content = r.json()["choices"][0]["message"]["content"]
                try:
                    return json.loads(content)
                except Exception:
                    return {"cmd": "", "speak": content, "display": clean_ascii(content)}
            print(f"Model {model_id} failed ({r.status_code}):", r.text)

    return {
        "cmd": "",
        "speak": "معذرت، ابھی سرور مصروف ہے۔ تھوڑی دیر بعد دوبارہ کوشش کیجیے۔",
        "display": "Server busy. Please try again."
    }


@app.get("/")
async def home():
    return {
        "status": "ok",
        "message": "ESP32 AI Human-Voice Server is Running!",
        "groq_key_set": bool(GROQ_API_KEY),
        "voice": state["voice"],
        "time": get_ist_time()
    }


@app.get("/audio/{audio_id}")
async def get_audio(audio_id: str):
    data = audio_cache.get(audio_id)
    if not data:
        return Response(status_code=404)
    return Response(content=data, media_type="audio/mpeg")


@app.post("/chat")
async def chat_endpoint(request: Request):
    if not GROQ_API_KEY:
        return {"action": "ERROR", "display": "GROQ_API_KEY missing"}

    wav_bytes = await request.body()
    if len(wav_bytes) < 2500:
        return {"action": "ERROR", "display": "Audio too short"}

    vcfg = VOICES.get(state["voice"], VOICES["ur_male"])
    user_text = await transcribe_wav(wav_bytes, vcfg["stt"])
    print("Heard:", user_text)

    if not user_text or len(user_text.strip()) < 2:
        mp3 = await generate_mp3("آواز صاف نہیں آئی، براہ کرم دوبارہ بولیں۔", state["voice"], state["rate"])
        return {
            "action": "SPEAK",
            "heard": "",
            "display": "Awaaz saaf nahi aayi. Dobara bolein.",
            "audio_url": store_audio(mp3),
            "voice": vcfg["label"],
            "volume": state["volume"],
            "speed": state["speed_label"]
        }

    low = user_text.lower()
    live_ctx = ""
    if any(k in low for k in ["weather", "mausam", "mosam", "temperature", "barish", "snow", "srinagar", "موسم", "بارش"]):
        city = "Srinagar"
        m = re.search(r"\b(?:in|of|ka|ki|ke)\s+([a-zA-Z]{3,20})\b", user_text)
        if m:
            cand = m.group(1).lower()
            if cand not in {"today", "aaj", "live", "now", "weather", "mausam", "mosam"}:
                city = m.group(1)
        live_ctx = await fetch_live_weather(city)
    elif any(k in low for k in ["cricket", "score", "match", "ipl", "world cup", "کرکٹ", "اسکور", "میچ"]):
        live_ctx = await fetch_live_cricket()
    elif any(k in low for k in ["news", "khabar", "khabrein", "headlines", "latest", "نیوز", "خبر"]):
        live_ctx = await fetch_live_news()

    ai = await ask_llm(user_text, live_ctx)
    cmd = str(ai.get("cmd", "")).strip().upper()
    speak = str(ai.get("speak", "")).strip()
    display = clean_ascii(str(ai.get("display", "")))

    if not cmd:
        vol_match = re.search(r"(?:volume|vol|awaz|awaaz)\s*(?:ko|to|karo|kar do|set)?\s*(\d{1,2})", low)
        if vol_match:
            cmd = f"VOL:SET:{vol_match.group(1)}"

    if cmd.startswith("QURAN:"):
        try:
            surah = int(cmd.split(":")[1])
            if 1 <= surah <= 114:
                return {
                    "action": "STREAM",
                    "title": f"QURAN SURAH {surah}",
                    "url": f"https://server8.mp3quran.net/afs/{surah:03d}.mp3",
                    "voice": vcfg["label"],
                    "volume": state["volume"],
                    "speed": state["speed_label"]
                }
        except Exception:
            pass

    if cmd == "RADIO_NAAT":
        chosen_url = await pick_working_stream(NAAT_STREAMS)
        return {
            "action": "STREAM",
            "title": "NAAT STREAM",
            "url": chosen_url,
            "voice": vcfg["label"],
            "volume": state["volume"],
            "speed": state["speed_label"]
        }

    if cmd == "RADIO_MUSIC":
        chosen_url = await pick_working_stream(MUSIC_STREAMS)
        return {
            "action": "STREAM",
            "title": "MUSIC STREAM",
            "url": chosen_url,
            "voice": vcfg["label"],
            "volume": state["volume"],
            "speed": state["speed_label"]
        }

    if cmd == "STOP":
        return {"action": "STOP", "voice": vcfg["label"], "volume": state["volume"], "speed": state["speed_label"]}

    if cmd == "VOL:UP":
        state["volume"] = min(18, state["volume"] + 3)
        speak = f"آواز بڑھا دی گئی ہے، اب والیوم {state['volume']} پر ہے۔"
        display = f"Volume increased to {state['volume']}/18."
    elif cmd == "VOL:DOWN":
        state["volume"] = max(1, state["volume"] - 3)
        speak = f"آواز کم کر دی گئی ہے، اب والیوم {state['volume']} پر ہے۔"
        display = f"Volume decreased to {state['volume']}/18."
    elif cmd == "VOL:MUTE":
        state["volume"] = 0
        return {"action": "MUTE", "volume": 0, "voice": vcfg["label"], "speed": state["speed_label"]}
    elif cmd.startswith("VOL:SET:"):
        try:
            v = int(cmd.split(":")[2])
            state["volume"] = max(0, min(18, v))
            speak = f"والیوم {state['volume']} سیٹ کر دیا گیا ہے۔"
            display = f"Volume set to {state['volume']}/18."
        except Exception:
            pass

    elif cmd.startswith("VOICE:"):
        vk = cmd.split(":")[1].lower()
        if vk in VOICES:
            state["voice"] = vk
            vcfg = VOICES[vk]
            confirm = {
                "ur_male":   ("اردو مردانہ آواز سیٹ ہو گئی ہے، فرمائیے میں آپ کی کیا مدد کروں؟", "Urdu male voice active. Ask your question."),
                "ur_female": ("اردو زنانہ آواز سیٹ ہو گئی ہے، فرمائیے میں آپ کی کیا مدد کروں؟", "Urdu female voice active. Ask your question."),
                "hi_male":   ("हिंदी आवाज़ सेट हो गई है, बताइए मैं आपकी क्या मदद करूँ?", "Hindi male voice active. Ask your question."),
                "hi_female": ("हिंदी आवाज़ सेट हो गई है, बताइए मैं आपकी क्या मदद करूँ?", "Hindi female voice active. Ask your question."),
                "en_male":   ("English male voice is now active. How can I help you?", "English male voice active. Ask your question."),
                "en_female": ("English female voice is now active. How can I help you?", "English female voice active. Ask your question."),
            }
            speak, display = confirm[vk]

    elif cmd == "SPEED:SLOW":
        state["rate"] = "-18%"
        state["speed_label"] = "Slow"
        speak = "ٹھیک ہے، اب میں آہستہ اور واضح بولوں گا۔"
        display = "Speed set to Slow (-18%)."
    elif cmd == "SPEED:FAST":
        state["rate"] = "+15%"
        state["speed_label"] = "Fast"
        speak = "ٹھیک ہے، اب میں تھوڑا تیز بولوں گا۔"
        display = "Speed set to Fast (+15%)."
    elif cmd == "SPEED:NORMAL":
        state["rate"] = "+0%"
        state["speed_label"] = "Norm"
        speak = "ٹھیک ہے، اب نارمل اسپیڈ سیٹ ہو گئی ہے۔"
        display = "Speed set to Normal."
    elif cmd == "CLEAR_MEM":
        state["history"].clear()
        speak = "پرانی گفتگو صاف کر دی گئی ہے، فرمائیے اب کیا بات کرنی ہے؟"
        display = "Memory cleared. Ask a new question."
    else:
        state["history"].append({"role": "user", "content": user_text})
        state["history"].append({"role": "assistant", "content": display or speak[:240]})
        if len(state["history"]) > 12:
            state["history"] = state["history"][-12:]

    if not speak:
        speak = "معذرت، جواب تیار نہیں ہو سکا۔"
    if not display:
        display = "Speaking..."

    mp3_bytes = await generate_mp3(speak, state["voice"], state["rate"])
    audio_url = store_audio(mp3_bytes)
    vcfg = VOICES[state["voice"]]

    return {
        "action": "SPEAK",
        "heard": clean_ascii(user_text),
        "display": display,
        "audio_url": audio_url,
        "voice": vcfg["label"],
        "volume": state["volume"],
        "speed": state["speed_label"]
    }
