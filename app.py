import io
import os
import re
import json
import uuid
import asyncio
from datetime import datetime, timezone, timedelta
from fastapi import FastAPI, Request, Response
import httpx
import edge_tts
from gtts import gTTS

app = FastAPI()

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()

NAAT_RADIO_URL = "http://stream.zeno.fm/0r0xa792kwzuv"
MUSIC_RADIO_URL = "http://stream.zeno.fm/rqqps63h4k8uv"

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
MAX_CACHE = 5


def get_ist_time() -> str:
    ist = timezone(timedelta(hours=5, minutes=30))
    return datetime.now(ist).strftime("%I:%M %p, %A, %d %B %Y (IST)")


def clean_ascii(text: str) -> str:
    if not text:
        return ""
    repl = {
        "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-", "\u2014": " - ",
        "\u2018": "'", "\u2019": "'", "\u201c": "", "\u201d": "", "\u2026": "..."
    }
    for k, v in repl.items():
        text = text.replace(k, v)
    out = "".join(ch if 32 <= ord(ch) <= 126 else " " for ch in text)
    return re.sub(r"\s+", " ", out).strip()


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
    prompts = {
        "ur": "سورہ رحمٰن کی تفسیر سناؤ، تلاوت سناؤ، نماز، قرآن، مسائل، والیوم، اردو وائس، ہندی وائس، انگلش وائس، فیمیل وائس، سرینگر۔",
        "hi": "सूरह रहमान की तफ़सीर सुनाओ, तिलावत सुनाओ, नमाज़, क़ुरआन, वॉल्यूम, उर्दू वॉइस, हिंदी वॉइस, इंग्लिश वॉइस, श्रीनगर।",
        "en": "Surah Rahman tafseer, Quran recitation, Islamic question, general knowledge, volume, Urdu voice, Hindi voice, Srinagar."
    }
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}"}
    files = {"file": ("speech.wav", wav_bytes, "audio/wav")}
    data = {
        "model": "whisper-large-v3-turbo",
        "language": stt_lang,
        "prompt": prompts.get(stt_lang, prompts["ur"]),
        "response_format": "json"
    }
    async with httpx.AsyncClient(timeout=25.0) as client:
        r = await client.post("https://api.groq.com/openai/v1/audio/transcriptions", headers=headers, data=data, files=files)
        if r.status_code != 200:
            print("Whisper Error:", r.status_code, r.text)
            return ""
        return r.json().get("text", "").strip()


async def ask_llm(user_text: str) -> dict:
    vkey = state["voice"]
    if vkey.startswith("ur"):
        lang_rule = (
            "Write 'speak' in PURE, NATURAL, EVERYDAY SPOKEN URDU using Urdu script (Nastaliq UTF-8). "
            "Write 'display' in clean ASCII Roman Urdu (no special unicode). "
            "Talk like a warm, intelligent human friend — never robotic or Sanskritized Hindi."
        )
    elif vkey.startswith("hi"):
        lang_rule = (
            "Write 'speak' in natural everyday Hindi using Devanagari script. "
            "Write 'display' in clean ASCII Roman Hindi."
        )
    else:
        lang_rule = (
            "Write both 'speak' and 'display' in natural, clear spoken English."
        )

    system_prompt = f"""You are a warm, intelligent, 100% accurate Islamic & General Knowledge AI voice companion in Srinagar, India (just like a real live voice conversation).
Current local time: {get_ist_time()}.
Current settings: voice={vkey}, volume={state['volume']}/18, speed={state['speed_label']}.

You MUST respond ONLY with a valid JSON object having exact keys: "cmd", "speak", "display".

1) COMMANDS (set "cmd" ONLY when user clearly asks for these; otherwise set "cmd": ""):
- Quran Tilawat / audio recitation ONLY: "QURAN:XXX" (3-digit Surah, e.g. "QURAN:055"). CRITICAL: NEVER set QURAN if user asks for TAFSEER (تفسیر), TARJUMA (ترجمہ), meaning, or explanation!
- Naat Radio: "RADIO_NAAT"
- Music Radio: "RADIO_MUSIC"
- Stop playback: "STOP"
- Volume: "VOL:UP", "VOL:DOWN", "VOL:SET:NN" (0-18), "VOL:MUTE"
- Voice switch: "VOICE:UR_MALE", "VOICE:UR_FEMALE", "VOICE:HI_MALE", "VOICE:HI_FEMALE", "VOICE:EN_MALE", "VOICE:EN_FEMALE" (if user just says "Urdu voice" use VOICE:UR_MALE, "ladki ki awaaz / female voice" use female of active language)
- Speed: "SPEED:SLOW", "SPEED:FAST", "SPEED:NORMAL"
- Clear memory: "CLEAR_MEM"

2) SPEECH & DISPLAY RULES:
- {lang_rule}
- ARABIC AYATS/DUAS: Whenever quoting any Quran ayat or Arabic dua inside "speak", wrap ONLY the Arabic part as [AR:Arabic with harakaat]. Example: [AR:قُلْ أَعُوذُ بِرَبِّ النَّاسِ]. In "display", write simple Roman reading.
- 100% FACTUAL ACCURACY:
  * Islamic knowledge must strictly match Quran, Sahih Hadith, and Tafseer Ibn Kathir. Verify all numbers (Fajr 2 Farz, Zuhr 4 Farz, Asr 4 Farz, Maghrib 3 Farz, Isha 4 Farz + 3 Witr).
  * General Knowledge (science, history, geography, math, India, Kashmir, world) must be 100% accurate. Never guess.
- NATURAL LIVE CONVERSATION:
  * Start answering directly without robotic intros like "Assalamu Alaikum main aapki madad ke liye hazir hoon".
  * For normal chat/questions, give a crisp, clear, natural 2 to 4 sentence answer.
  * For Tafseer or detailed explanation, give a complete, well-structured, easy-to-follow 5 to 8 sentence explanation.
  * No markdown, no asterisks, no bullet symbols."""

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
            "temperature": 0.15,
            "max_tokens": 900,
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
        "display": "Server busy, please try again."
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
    if len(wav_bytes) < 1000:
        return {"action": "ERROR", "display": "Audio too short"}

    vcfg = VOICES.get(state["voice"], VOICES["ur_male"])
    user_text = await transcribe_wav(wav_bytes, vcfg["stt"])
    print("Heard:", user_text)

    if not user_text:
        mp3 = await generate_mp3("آواز صحیح سنائی نہیں دی، دوبارہ فرمائیے۔", state["voice"], state["rate"])
        return {
            "action": "SPEAK",
            "heard": "",
            "display": "Awaaz nahi sunayi di, dobara boliye.",
            "audio_url": store_audio(mp3),
            "voice": vcfg["label"],
            "volume": state["volume"],
            "speed": state["speed_label"]
        }

    ai = await ask_llm(user_text)
    cmd = str(ai.get("cmd", "")).strip().upper()
    speak = str(ai.get("speak", "")).strip()
    display = clean_ascii(str(ai.get("display", "")))

    if cmd.startswith("QURAN:"):
        try:
            surah = int(cmd.split(":")[1])
            if 1 <= surah <= 114:
                return {
                    "action": "STREAM",
                    "title": f"QURAN S{surah}",
                    "url": f"http://server8.mp3quran.net/afs/{surah:03d}.mp3",
                    "voice": vcfg["label"],
                    "volume": state["volume"],
                    "speed": state["speed_label"]
                }
        except Exception:
            pass

    if cmd == "RADIO_NAAT":
        return {"action": "STREAM", "title": "NAAT RADIO", "url": NAAT_RADIO_URL,
                "voice": vcfg["label"], "volume": state["volume"], "speed": state["speed_label"]}

    if cmd == "RADIO_MUSIC":
        return {"action": "STREAM", "title": "MUSIC RADIO", "url": MUSIC_RADIO_URL,
                "voice": vcfg["label"], "volume": state["volume"], "speed": state["speed_label"]}

    if cmd == "STOP":
        return {"action": "STOP", "voice": vcfg["label"], "volume": state["volume"], "speed": state["speed_label"]}

    if cmd == "VOL:UP":
        state["volume"] = min(18, state["volume"] + 3)
        speak = f"آواز بڑھا دی گئی ہے، اب والیوم {state['volume']} پر ہے۔"
        display = f"Volume set to {state['volume']}/18"
    elif cmd == "VOL:DOWN":
        state["volume"] = max(1, state["volume"] - 3)
        speak = f"آواز کم کر دی گئی ہے، اب والیوم {state['volume']} پر ہے۔"
        display = f"Volume set to {state['volume']}/18"
    elif cmd == "VOL:MUTE":
        state["volume"] = 0
        return {"action": "MUTE", "volume": 0, "voice": vcfg["label"], "speed": state["speed_label"]}
    elif cmd.startswith("VOL:SET:"):
        try:
            v = int(cmd.split(":")[2])
            state["volume"] = max(0, min(18, v))
            speak = f"والیوم {state['volume']} سیٹ ہو گیا ہے۔"
            display = f"Volume set to {state['volume']}/18"
        except Exception:
            pass

    elif cmd.startswith("VOICE:"):
        vk = cmd.split(":")[1].lower()
        if vk in VOICES:
            state["voice"] = vk
            vcfg = VOICES[vk]
            confirm = {
                "ur_male":   ("اردو مردانہ آواز سیٹ ہو گئی ہے، فرمائیے میں آپ کی کیا مدد کروں؟", "Voice: Urdu Male (Asad)"),
                "ur_female": ("اردو زنانہ آواز سیٹ ہو گئی ہے، فرمائیے میں آپ کی کیا مدد کروں؟", "Voice: Urdu Female (Uzma)"),
                "hi_male":   ("हिंदी आवाज़ सेट हो गई है, बताइए मैं आपकी क्या मदद करूँ?", "Voice: Hindi Male (Madhur)"),
                "hi_female": ("हिंदी आवाज़ सेट हो गई है, बताइए मैं आपकी क्या मदद करूँ?", "Voice: Hindi Female (Swara)"),
                "en_male":   ("English male voice is now active. How can I help you?", "Voice: English Male"),
                "en_female": ("English female voice is now active. How can I help you?", "Voice: English Female"),
            }
            speak, display = confirm[vk]

    elif cmd == "SPEED:SLOW":
        state["rate"] = "-18%"
        state["speed_label"] = "Slow"
        speak = "ٹھیک ہے، اب میں آہستہ اور آرام سے بولوں گا۔"
        display = "Speed: Slow"
    elif cmd == "SPEED:FAST":
        state["rate"] = "+15%"
        state["speed_label"] = "Fast"
        speak = "ٹھیک ہے، اب میں تھوڑا تیز بولوں گا۔"
        display = "Speed: Fast"
    elif cmd == "SPEED:NORMAL":
        state["rate"] = "+0%"
        state["speed_label"] = "Norm"
        speak = "ٹھیک ہے، اب نارمل اسپیڈ سیٹ ہو گئی ہے۔"
        display = "Speed: Normal"
    elif cmd == "CLEAR_MEM":
        state["history"].clear()
        speak = "پرانی گفتگو صاف کر دی گئی ہے، فرمائیے اب کیا بات کرنی ہے؟"
        display = "Conversation memory cleared."
    else:
        state["history"].append({"role": "user", "content": user_text})
        state["history"].append({"role": "assistant", "content": display or speak[:200]})
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
