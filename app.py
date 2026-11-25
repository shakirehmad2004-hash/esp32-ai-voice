import os
import re
import time
import uuid
import urllib.parse
import xml.etree.ElementTree as ET
import datetime
from typing import Optional, List, Tuple, Dict
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
        os.getenv("GROQ_API_KEY", "")
        + ","
        + os.getenv("GROQ_API_KEY_2", "")
        + ","
        + os.getenv("GROQ_API_KEY_3", "")
    ).split(",")
    if k.strip()
]

FALLBACK_GROQ_MODELS = [
    "openai/gpt-oss-120b",
    "qwen/qwen3.8-27b",
    "openai/gpt-oss-20b",
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
]

MODEL_CACHE: Dict[str, Tuple[float, List[str]]] = {}

STATE = {
    "voice": "UR-M",
    "speed": "Norm",
    "volume": 13,
    "monster": False,
}

VOICE_MAP = {
    "UR-M": "ur-PK-AsadNeural",
    "EN-M": "en-US-GuyNeural",
    "AR-M": "ar-SA-HamedNeural",
}

GTTS_LANG_MAP = {
    "UR-M": "ur",
    "EN-M": "en",
    "AR-M": "ar",
}

SPEED_MAP = {
    "Slow": "-12%",
    "Norm": "+0%",
    "Fast": "+12%",
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

SURAH_NAMES: Dict[str, int] = {
    "fatiha": 1, "fatihah": 1, "fateha": 1, "فاتحہ": 1, "الفاتحة": 1, "फातिहा": 1,
    "baqarah": 2, "baqara": 2, "bakra": 2, "بقرہ": 2, "البقرة": 2, "बकरा": 2,
    "imran": 3, "aal imran": 3, "al imran": 3, "عمران": 3,
    "nisa": 4, "nisaa": 4, "نساء": 4,
    "maidah": 5, "maida": 5, "مائدہ": 5, "المائدة": 5,
    "anam": 6, "anaam": 6, "انعام": 6,
    "araf": 7, "aaraf": 7, "اعراف": 7,
    "anfal": 8, "انفال": 8,
    "tawbah": 9, "taubah": 9, "tauba": 9, "توبہ": 9, "التوبة": 9,
    "yunus": 10, "younus": 10, "یونس": 10,
    "hud": 11, "hood": 11, "ہود": 11, "هود": 11,
    "yusuf": 12, "yousuf": 12, "یوسف": 12,
    "raad": 13, "rad": 13, "رعد": 13,
    "ibrahim": 14, "ibraheem": 14, "ابراہیم": 14,
    "hijr": 15, "حجر": 15,
    "nahl": 16, "نحل": 16,
    "isra": 17, "bani israil": 17, "اسراء": 17,
    "kahf": 18, "kahaf": 18, "کہف": 18, "الكهف": 18, "कहफ": 18,
    "maryam": 19, "mariam": 19, "مریم": 19,
    "taha": 20, "ta ha": 20, "طہ": 20, "طه": 20,
    "anbiya": 21, "ambiya": 21, "انبیاء": 21,
    "hajj": 22, "haj": 22, "حج": 22,
    "muminun": 23, "mominoon": 23, "مؤمنون": 23,
    "nur": 24, "noor": 24, "نور": 24,
    "furqan": 25, "فرقان": 25,
    "shuara": 26, "شعراء": 26,
    "naml": 27, "نمل": 27,
    "qasas": 28, "قصص": 28,
    "ankabut": 29, "عنکبوت": 29,
    "rum": 30, "room": 30, "روم": 30,
    "luqman": 31, "lukman": 31, "لقمان": 31,
    "sajdah": 32, "sajda": 32, "سجدہ": 32, "السجدة": 32,
    "ahzab": 33, "احزاب": 33,
    "saba": 34, "سبا": 34,
    "fatir": 35, "فاطر": 35,
    "yasin": 36, "yaseen": 36, "یاسین": 36, "يس": 36, "यासीन": 36,
    "saffat": 37, "صافات": 37,
    "sad": 38, "saad": 38, "ص": 38,
    "zumar": 39, "زمر": 39,
    "ghafir": 40, "momin": 40, "غافر": 40,
    "fussilat": 41, "ha meem sajdah": 41, "فصلت": 41,
    "shura": 42, "شوری": 42,
    "zukhruf": 43, "زخرف": 43,
    "dukhan": 44, "دخان": 44,
    "jathiyah": 45, "jasiya": 45, "جاثیہ": 45,
    "ahqaf": 46, "احقاف": 46,
    "muhammad": 47, "محمد": 47,
    "fath": 48, "fatah": 48, "فتح": 48,
    "hujurat": 49, "حجرات": 49,
    "qaf": 50, "ق": 50,
    "dhariyat": 51, "zariyat": 51, "ذاریات": 51,
    "tur": 52, "toor": 52, "طور": 52,
    "najm": 53, "نجم": 53,
    "qamar": 54, "قمر": 54,
    "rahman": 55, "rehman": 55, "رحمن": 55, "الرحمن": 55, "रहमान": 55,
    "waqiah": 56, "waqia": 56, "واقعہ": 56, "الواقعة": 56, "वाकिया": 56,
    "hadid": 57, "hadeed": 57, "حدید": 57,
    "mujadila": 58, "مجادلہ": 58,
    "hashr": 59, "حشر": 59,
    "mumtahanah": 60, "ممتحنہ": 60,
    "saff": 61, "صف": 61,
    "jumuah": 62, "juma": 62, "جمعہ": 62, "الجمعة": 62,
    "munafiqun": 63, "منافقون": 63,
    "taghabun": 64, "تغابن": 64,
    "talaq": 65, "طلاق": 65,
    "tahrim": 66, "تحریم": 66,
    "mulk": 67, "tabarak": 67, "ملک": 67, "الملك": 67, "मुल्क": 67,
    "qalam": 68, "قلم": 68,
    "haqqah": 69, "haqqa": 69, "حاقہ": 69,
    "maarij": 70, "معارج": 70,
    "nuh": 71, "nooh": 71, "نوح": 71,
    "jinn": 72, "jin": 72, "جن": 72,
    "muzzammil": 73, "muzammil": 73, "مزمل": 73,
    "muddaththir": 74, "mudassir": 74, "مدثر": 74,
    "qiyamah": 75, "qiyama": 75, "قیامہ": 75,
    "insan": 76, "dahr": 76, "انسان": 76, "دہر": 76,
    "mursalat": 77, "مرسلات": 77,
    "naba": 78, "amma": 78, "نبا": 78,
    "naziat": 79, "نازعات": 79,
    "abasa": 80, "عبس": 80,
    "takwir": 81, "تکویر": 81,
    "infitar": 82, "انفطار": 82,
    "mutaffifin": 83, "مطففین": 83,
    "inshiqaq": 84, "انشقاق": 84,
    "buruj": 85, "بروج": 85,
    "tariq": 86, "طارق": 86,
    "ala": 87, "aala": 87, "اعلی": 87,
    "ghashiyah": 88, "ghashiya": 88, "غاشیہ": 88,
    "fajr": 89, "فجر": 89,
    "balad": 90, "بلد": 90,
    "shams": 91, "شمس": 91,
    "layl": 92, "lail": 92, "لیل": 92,
    "duha": 93, "zuha": 93, "ضحی": 93,
    "sharh": 94, "inshirah": 94, "شرح": 94, "الم نشرح": 94,
    "tin": 95, "teen": 95, "تین": 95,
    "alaq": 96, "علق": 96,
    "qadr": 97, "qadar": 97, "قدر": 97,
    "bayyinah": 98, "bayyina": 98, "بینہ": 98,
    "zalzalah": 99, "zilzal": 99, "زلزال": 99,
    "adiyat": 100, "عادیات": 100,
    "qariah": 101, "qaria": 101, "قارعہ": 101,
    "takathur": 102, "takasur": 102, "تکاثر": 102,
    "asr": 103, "عصر": 103, "अस्र": 103,
    "humazah": 104, "ہمزہ": 104,
    "fil": 105, "feel": 105, "فیل": 105,
    "quraysh": 106, "quraish": 106, "قریش": 106,
    "maun": 107, "maoon": 107, "ماعون": 107,
    "kawthar": 108, "kausar": 108, "کوثر": 108, "कौसर": 108,
    "kafirun": 109, "kafiroon": 109, "کافرون": 109,
    "nasr": 110, "نصر": 110,
    "masad": 111, "lahab": 111, "مسد": 111, "لہب": 111,
    "ikhlas": 112, "ekhlas": 112, "iklas": 112, "eclás": 112, "eklaský": 112,
    "qul huwallah": 112, "اخلاص": 112, "الإخلاص": 112, "الإخلاسية": 112, "इखलास": 112, "इख़लास": 112,
    "falaq": 113, "falak": 113, "فلق": 113, "الفلق": 113, "फलक": 113,
    "naas": 114, "nas": 114, "ناس": 114, "الناس": 114, "नास": 114,
}

NUMBER_WORDS = {
    "ek": 1, "aik": 1, "one": 1, "pehli": 1, "pehlay": 1, "pehle": 1, "first": 1, "ایک": 1, "پہلی": 1, "الأولى": 1, "पहली": 1,
    "do": 2, "two": 2, "دو": 2, "اثنين": 2, "दो": 2,
    "teen": 3, "three": 3, "تین": 3, "ثلاث": 3, "तीन": 3,
    "chaar": 4, "char": 4, "four": 4, "چار": 4, "أربع": 4, "चार": 4,
    "paanch": 5, "panch": 5, "five": 5, "پانچ": 5, "خمس": 5, "पांच": 5, "पाँच": 5,
    "cheh": 6, "chhe": 6, "six": 6, "چھ": 6, "ست": 6, "छह": 6,
    "saat": 7, "sat": 7, "seven": 7, "سات": 7, "سبع": 7, "सात": 7,
    "aath": 8, "ath": 8, "eight": 8, "آٹھ": 8, "ثمان": 8, "आठ": 8,
    "nau": 9, "nine": 9, "نو": 9, "تسع": 9, "नौ": 9,
    "das": 10, "ten": 10, "دس": 10, "عشر": 10, "दस": 10,
}

HIJRI_MONTHS_EN = [
    "Muharram", "Safar", "Rabi I", "Rabi II",
    "Jumada I", "Jumada II", "Rajab", "Shaban",
    "Ramadan", "Shawwal", "Dhul Qadah", "Dhul Hijjah"
]

HIJRI_MONTHS_UR = [
    "محرم الحرام", "صفر المظفر", "ربیع الاول", "ربیع الثانی",
    "جمادی الاول", "جمادی الثانی", "رجب المرجب", "شعبان المعظم",
    "رمضان المبارک", "شوال المکرم", "ذوالقعدہ", "ذوالحجہ"
]

WHISPER_HALLUCINATIONS = {
    "thank you", "thank you.", "thanks for watching", "thanks for watching.",
    "subscribe", "you", "you.", "mbc", "bye", "bye.", ".", "...", "okay", "ok",
}


def get_ist_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=5, minutes=30)))


def gregorian_to_hijri(year: int, month: int, day: int) -> Tuple[int, str, str, int]:
    a = (14 - month) // 12
    y = year + 4800 - a
    m = month + 12 * a - 3
    jdn = day + (153 * m + 2) // 5 + 365 * y + y // 4 - y // 100 + y // 400 - 32045
    l = jdn - 1948440 + 10632
    n = (l - 1) // 10631
    l = l - 10631 * n + 354
    j = ((10985 - l) // 5316) * ((50 * l) // 17719) + (l // 5670) * ((43 * l) // 15238)
    l = l - ((30 - j) // 15) * ((17719 * j) // 50) - (j // 16) * ((15238 * j) // 43) + 29
    h_month = int((24 * l) // 709)
    h_day = int(l - (709 * h_month) // 24)
    h_year = int(30 * n + j - 30)
    idx = max(0, min(11, h_month - 1))
    return h_day, HIJRI_MONTHS_EN[idx], HIJRI_MONTHS_UR[idx], h_year


def get_hijri_str() -> str:
    now = get_ist_now()
    hd, hm_en, _, hy = gregorian_to_hijri(now.year, now.month, now.day)
    return f"{hd} {hm_en} {hy}"


def format_urdu_period_and_time(now: datetime.datetime) -> str:
    h24 = now.hour
    m = now.minute
    h12 = h24 % 12 or 12
    if 4 <= h24 < 12:
        period = "صبح"
    elif 12 <= h24 < 17:
        period = "دوپہر"
    elif 17 <= h24 < 20:
        period = "شام"
    else:
        period = "رات"
    if m == 0:
        return f"{period} کے {h12} بجے ہیں"
    return f"{period} کے {h12} بج کر {m} منٹ ہوئے ہیں"


def clean_ascii(text: str) -> str:
    s = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S)
    s = re.sub(r"\b(?:DISPLAY|VOICE)\s*:\s*", "", s, flags=re.I)
    s = re.sub(r"[^\x20-\x7E]", " ", s)
    s = re.sub(r"[*_#`~\"']+", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def clean_arabic_display(text: str) -> str:
    s = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S)
    s = re.sub(r"[\u064B-\u065F\u0670\u0640]", "", s)
    s = re.sub(r"[*_#`~\"']+", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def is_tafseer_intent(text: str) -> bool:
    low = (text or "").lower()
    keywords = [
        "tafseer", "tafsir", "tafsīr", "tafser", "tapsir", "tasfir", "tabseer", "takseer", "taseer",
        "tafsiersonal", "psiersonal", "tarjuma", "tarjama", "translation", "meaning", "mafhoom",
        "matlab", "explain", "sharah", "samjhao", "samjhaiye", "ayat", "aayat", "ayah", "verse",
        "ayatul kursi", "ayat ul kursi", "nazool", "nuzool", "shaan", "halaat", "makki", "madani",
        "تفسیر", "تفسير", "تفصیر", "تبصیر", "تقصیر", "ترجمہ", "ترجمة", "آیت", "آیات", "آية",
        "معنی", "معنى", "مطلب", "شرح", "وضاحت", "تشریح", "سمجھاؤ", "نزول", "شان", "حالات", "مکی", "مدنی",
        "آیت الکرسی", "آية الكرسي",
        "तफसीर", "तफ़सीर", "तफ्सीर", "तर्जुमा", "तरजुमा", "आयत", "आयात", "मतलब", "अर्थ", "व्याख्या",
    ]
    if any(k in low for k in keywords):
        return True
    if re.search(r"taf[sz]e*r|tap[sz]i*r|tab[sz]e*r|tarj[au]m|nuz[ou]+l|shaan", low):
        return True
    return False


def detect_language(text: str) -> str:
    low = (text or "").lower()
    if any(w in low for w in ["english mein", "in english", "speak english", "انگریزی میں"]):
        return "EN"
    if any(w in low for w in ["arabic mein", "arabi mein", "in arabic", "بالعربية", "عربی میں"]):
        return "AR"
    if any(w in low for w in ["urdu mein", "in urdu", "hindi mein", "اردو میں"]):
        return "UR"

    if re.search(r"[\u0900-\u097F]", text):
        return "UR"

    if re.search(r"[\u0679\u0688\u0691\u06BA\u06D2\u06C1\u06BE\u06AF\u067E\u0686\u0698\u06CC]", text):
        return "UR"

    urdu_markers = {
        "کے", "کا", "کی", "میں", "ہے", "ہیں", "تھا", "بتاؤ", "بتائیں", "سناؤ", "سنائیں",
        "کیا", "کیسے", "آج", "کل", "کون", "کہاں", "نماز", "موسم", "خبریں", "تفسیر", "پہلی",
        "ٹائم", "ٹانم", "ابھی", "ہوا", "بجے", "حالات", "نزول",
        "kya", "hai", "hain", "he", "batao", "bataiye", "sunao", "sunawo", "sunahu", "kaise", "aaj",
        "namaz", "mausam", "khabar", "khabrein", "khabrai", "tafseer", "tafsir", "tarjuma",
        "pehli", "pehlay", "pehle", "aayat", "ayat", "surah", "sury", "suraj", "kaun", "kahan",
        "kitna", "band", "chalu", "dikhao", "ruko", "awaaz", "karo", "kero", "mera", "mujhe",
        "hum", "aap", "ki", "ka", "ke", "halaat", "nazool", "nuzool",
    }
    tokens = re.findall(r"[\w\u0600-\u06FF]+", low)
    if any(tok in urdu_markers for tok in tokens):
        return "UR"

    if re.search(r"[\u0600-\u06FF]", text):
        return "AR"

    return "EN"


def fmt_12_short(mins: int) -> str:
    mins = max(0, min(1439, int(round(mins))))
    h24, m = divmod(mins, 60)
    h12 = h24 % 12 or 12
    return f"{h12}:{m:02d}"


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


def build_namaz_response(lang: str) -> Tuple[str, str]:
    f, sr, z, a, mg, e = get_today_meeqat()
    disp = (
        f"Fajr {fmt_12_short(f)}. "
        f"Zohar {fmt_12_short(z)}. "
        f"Asar {fmt_12_short(a)}. "
        f"Maghrib {fmt_12_short(mg)}. "
        f"Esha {fmt_12_short(e)}."
    )
    if lang == "AR":
        ar = (
            f"الفجر {fmt_12_short(f)}. الظهر {fmt_12_short(z)}. "
            f"العصر {fmt_12_short(a)}. المغرب {fmt_12_short(mg)}. العشاء {fmt_12_short(e)}."
        )
        return ar, ar
    if lang == "UR":
        ur_tts = (
            f"فجر {fmt_12_short(f)}۔ ظہر {fmt_12_short(z)}۔ "
            f"عصر {fmt_12_short(a)}۔ مغرب {fmt_12_short(mg)}۔ عشاء {fmt_12_short(e)}۔"
        )
        return disp, ur_tts
    return disp, disp


def replace_number_words(text: str) -> str:
    out = text.lower()
    for word, val in NUMBER_WORDS.items():
        out = re.sub(rf"(?<!\w){re.escape(word)}(?!\w)", str(val), out)
    return out


def parse_quran_request(text: str) -> Tuple[Optional[int], Optional[int], Optional[int], bool]:
    low = text.lower()

    if any(k in low for k in ["ayatul kursi", "ayat ul kursi", "ayat-ul-kursi", "آیت الکرسی", "آية الكرسي"]):
        return 2, 255, 255, False
    if any(k in low for k in ["amanar rasul", "amanar rasool", "آمن الرسول"]):
        return 2, 285, 286, False

    surah_num = None
    m_surah = re.search(r"(?:surah|sura|sury|suraj|سوره|سورہ|سورة|सूरह)\s*(?:number|no\.?|نمبر)?\s*(\d{1,3})", low)
    if m_surah:
        surah_num = max(1, min(114, int(m_surah.group(1))))
    else:
        for name in sorted(SURAH_NAMES.keys(), key=len, reverse=True):
            if name in low:
                surah_num = SURAH_NAMES[name]
                break

    norm = replace_number_words(low)

    m_first = re.search(
        r"(?:first|pehli|pehlay|pehle|shuru\s*ki|ابتدائی|پہلی|أول|पहली)\s*(\d{1,2})\s*(?:ayat|aayat|ayah|ayahs|verses|آیات|آیت|آية|आयत|आयात)",
        norm,
    )
    if m_first:
        n = max(1, min(10, int(m_first.group(1))))
        return surah_num, 1, n, False

    m_first2 = re.search(r"\b1\s+(\d{1,2})\s*(?:ayat|aayat|ayah|ayahs|verses|آیات|آیت|آية|आयत|आयात)", norm)
    if m_first2 and any(k in low for k in ["pehli", "pehlay", "pehle", "first", "پہلی", "أول", "पहली"]):
        n = max(1, min(10, int(m_first2.group(1))))
        return surah_num, 1, n, False

    m_last = re.search(
        r"(?:last|aakhri|akhri|aakhir\s*ki|آخری|آخر|आखिरी)\s*(\d{1,2})\s*(?:ayat|aayat|ayah|ayahs|verses|آیات|آیت|آية|आयत|आयात)",
        norm,
    )
    if m_last:
        n = max(1, min(10, int(m_last.group(1))))
        return surah_num, n, n, True

    m_range = re.search(
        r"(?:ayat|aayat|ayah|verse|آیت|آیات|آية|आयत)?\s*(\d{1,3})\s*(?:se|say|to|-|تا|إلى|से)\s*(\d{1,3})",
        norm,
    )
    if m_range:
        s_ay = max(1, int(m_range.group(1)))
        e_ay = max(s_ay, min(s_ay + 9, int(m_range.group(2))))
        return surah_num, s_ay, e_ay, False

    m_single = re.search(r"(?:ayat|aayat|ayah|verse|آیت|آية|आयत)\s*(?:number|no\.?|نمبر)?\s*(\d{1,3})", norm)
    if m_single:
        a = max(1, int(m_single.group(1)))
        return surah_num, a, a, False

    if surah_num is None:
        nums = [int(x) for x in re.findall(r"\b(\d{1,3})\b", norm)]
        if nums and any(k in low for k in ["surah", "sura", "quran", "سورہ", "سورة", "قرآن", "सूरह"]):
            surah_num = max(1, min(114, nums[0]))

    return surah_num, None, None, False


async def fetch_quran_verses(
    surah_num: int, start_ay: Optional[int], end_ay: Optional[int], is_last: bool
) -> Tuple[str, str, int, str, str]:
    url = f"https://api.alquran.cloud/v1/surah/{surah_num}/editions/quran-uthmani,ur.jalandhry,en.sahih"
    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            r = await client.get(url)
            if r.status_code != 200:
                return "", "", 0, "", ""
            editions = r.json().get("data", [])
            if len(editions) < 3:
                return "", "", 0, "", ""
            ar_ed, ur_ed, en_ed = editions[0], editions[1], editions[2]
            s_name_en = ar_ed.get("englishName") or f"Surah {surah_num}"
            s_name_ar = ar_ed.get("name") or s_name_en
            total = ar_ed.get("numberOfAyahs", 0)
            ar_list = ar_ed.get("ayahs", [])
            ur_list = ur_ed.get("ayahs", [])
            en_list = en_ed.get("ayahs", [])

            if is_last and start_ay:
                s_idx = max(0, total - start_ay)
                e_idx = total
            elif start_ay is not None:
                s_idx = max(0, min(total - 1, start_ay - 1))
                target_end = end_ay if end_ay is not None else start_ay
                e_idx = max(s_idx + 1, min(total, target_end))
            else:
                s_idx = 0
                e_idx = min(total, 6)

            lines = []
            ur_fallback_parts = []
            for idx in range(s_idx, e_idx):
                anum = idx + 1
                ar_t = ar_list[idx].get("text", "") if idx < len(ar_list) else ""
                ur_t = ur_list[idx].get("text", "") if idx < len(ur_list) else ""
                en_t = en_list[idx].get("text", "") if idx < len(en_list) else ""
                lines.append(f"Ayah {anum} | AR: {ar_t} | UR: {ur_t} | EN: {en_t}")
                ur_fallback_parts.append(f"آیت {anum}: {ur_t}")

            return s_name_en, s_name_ar, total, "\n".join(lines), " ".join(ur_fallback_parts)
    except Exception as ex:
        print(f"Quran API error: {ex}")
        return "", "", 0, "", ""


async def fetch_rss_items(urls: List[str], limit: int = 5) -> List[str]:
    items: List[str] = []
    headers = {"User-Agent": "Mozilla/5.0 (compatible; ESP32Assistant/2.0)"}
    async with httpx.AsyncClient(timeout=7.0, follow_redirects=True, headers=headers) as client:
        for u in urls:
            try:
                r = await client.get(u)
                if r.status_code != 200:
                    continue
                root = ET.fromstring(r.text)
                for item in root.findall(".//item"):
                    title = (item.findtext("title") or "").strip()
                    pub = (item.findtext("pubDate") or "").strip()
                    if title:
                        clean_t = re.sub(r"\s+", " ", title)
                        items.append(f"{clean_t} ({pub[:16]})" if pub else clean_t)
                    if len(items) >= limit:
                        return items
            except Exception as ex:
                print(f"RSS error ({u}): {ex}")
    return items


async def fetch_cricket_context(query: str) -> List[str]:
    q_extra = re.sub(r"\b(cricket|news|live|score|scores|match|aaj|ki|kya|hai|batao|sunao)\b", "", query, flags=re.I).strip()
    search_q = f"cricket live score match {q_extra}".strip()
    gnews = f"https://news.google.com/rss/search?q={urllib.parse.quote(search_q)}&hl=en-IN&gl=IN&ceid=IN:en"
    feeds = [
        "https://www.espncricinfo.com/rss/content/story/feeds/0.xml",
        gnews,
    ]
    return await fetch_rss_items(feeds, limit=5)


async def fetch_news_context(query: str) -> List[str]:
    cleaned = re.sub(
        r"\b(news|khabar|khabrein|khabrai|headlines|live|today|aaj|ki|ka|ke|taza|latest|breaking|batao|bataiye|sunao|sunawo|tell|me|about|what|is|the)\b",
        "",
        query,
        flags=re.I,
    ).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)

    feeds: List[str] = []
    if cleaned and len(cleaned) >= 2:
        feeds.append(
            f"https://news.google.com/rss/search?q={urllib.parse.quote(cleaned)}&hl=en-IN&gl=IN&ceid=IN:en"
        )
    feeds.extend([
        "https://news.google.com/rss?hl=en-IN&gl=IN&ceid=IN:en",
        "https://feeds.bbci.co.uk/news/world/asia/rss.xml",
        "https://www.aljazeera.com/xml/rss/all.xml",
    ])
    return await fetch_rss_items(feeds, limit=5)


async def fetch_weather_info(query: str) -> Tuple[str, Optional[float], Optional[float]]:
    lat, lon, place = 34.0837, 74.7973, "Srinagar"
    m_city = re.search(r"(?:in|at|of|mein|me|ka|ki)\s+([a-zA-Z]{3,20})", query, flags=re.I)
    if m_city:
        cand = m_city.group(1).strip()
        if cand.lower() not in {"aaj", "today", "abhi", "now", "kaisa", "weather", "mausam", "mosam", "kya", "hai"}:
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    g = await client.get(
                        f"https://geocoding-api.open-meteo.com/v1/search?name={urllib.parse.quote(cand)}&count=1"
                    )
                    if g.status_code == 200 and g.json().get("results"):
                        res = g.json()["results"][0]
                        lat, lon, place = res["latitude"], res["longitude"], res["name"]
            except Exception:
                pass
    try:
        url = (
            f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
            "&current=temperature_2m,wind_speed_10m&timezone=Asia%2FKolkata"
        )
        async with httpx.AsyncClient(timeout=7.0) as client:
            r = await client.get(url)
            if r.status_code == 200:
                cur = r.json().get("current", {})
                return place, cur.get("temperature_2m"), cur.get("wind_speed_10m")
    except Exception as ex:
        print(f"Weather error: {ex}")
    return place, None, None


def _rank_groq_model(mid: str) -> int:
    m = mid.lower()
    if "120b" in m:
        return 1
    if "70b" in m and "versatile" in m:
        return 2
    if "qwen" in m:
        return 3
    if "20b" in m or "32b" in m:
        return 4
    if "maverick" in m or "scout" in m:
        return 5
    if "llama" in m:
        return 6
    return 15


async def get_available_groq_models(key: str) -> List[str]:
    now_t = time.time()
    cached = MODEL_CACHE.get(key)
    if cached and (now_t - cached[0]) < 600:
        return cached[1]

    skip_tokens = ("whisper", "tts", "orpheus", "playai", "guard", "safeguard", "moderation", "embedding", "speech", "audio")
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.get(
                "https://api.groq.com/openai/v1/models",
                headers={"Authorization": f"Bearer {key}"},
            )
            if r.status_code == 200:
                raw_list = r.json().get("data", [])
                chat_models = []
                for item in raw_list:
                    mid = str(item.get("id") or "")
                    if not mid:
                        continue
                    low = mid.lower()
                    if any(tok in low for tok in skip_tokens):
                        continue
                    chat_models.append(mid)
                chat_models.sort(key=_rank_groq_model)
                if chat_models:
                    MODEL_CACHE[key] = (now_t, chat_models[:8])
                    return chat_models[:8]
    except Exception as ex:
        print(f"Groq /models error: {ex}")

    return FALLBACK_GROQ_MODELS


async def call_groq_bilingual(system_prompt: str, user_prompt: str, lang: str, max_tokens: int = 950) -> Optional[Tuple[str, str]]:
    if not RAW_KEYS:
        return None

    if lang == "UR":
        format_rule = (
            "STRICT LANGUAGE LOCK: The user asked in URDU/ROMAN URDU.\n"
            "Reply using EXACTLY these two lines and nothing else:\n"
            "DISPLAY: <Clean answer in Roman Urdu using A-Z letters and 0-9 numbers only; NO Urdu script, NO English sentences, NO words like DISPLAY or VOICE>\n"
            "VOICE: <Exact same clean answer in natural, fluent Urdu script (اردو) for a male Urdu voice>\n"
            "Never output English-only answers when lang is UR."
        )
    elif lang == "AR":
        format_rule = (
            "STRICT LANGUAGE LOCK: The user asked in ARABIC.\n"
            "Reply ONLY in clear, accurate Modern Standard Arabic. No Urdu, no English words, no markdown."
        )
    else:
        format_rule = (
            "STRICT LANGUAGE LOCK: The user asked in ENGLISH.\n"
            "Reply ONLY in clear, natural English. No Urdu words, no Arabic script, no markdown."
        )

    full_sys = system_prompt + "\n\n" + format_rule

    for key in RAW_KEYS:
        models = await get_available_groq_models(key)
        for model in models:
            try:
                async with httpx.AsyncClient(timeout=20.0) as client:
                    resp = await client.post(
                        "https://api.groq.com/openai/v1/chat/completions",
                        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                        json={
                            "model": model,
                            "messages": [
                                {"role": "system", "content": full_sys},
                                {"role": "user", "content": user_prompt},
                            ],
                            "max_tokens": max_tokens,
                            "temperature": 0.2,
                        },
                    )
                    if resp.status_code == 200:
                        raw = resp.json()["choices"][0]["message"]["content"].strip()
                        raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.S).strip()
                        print(f"Groq OK [{model}] lang={lang}")

                        if lang == "UR":
                            m_disp = re.search(r"DISPLAY:\s*(.+?)(?=\nVOICE:|\Z)", raw, flags=re.S | re.I)
                            m_voice = re.search(r"VOICE:\s*(.+)", raw, flags=re.S | re.I)
                            if m_disp and m_voice:
                                disp = clean_ascii(m_disp.group(1))
                                voice = re.sub(r"[*_#`~]+", "", m_voice.group(1)).strip()
                                if disp and voice:
                                    return disp, voice
                            disp_fb = clean_ascii(raw)
                            if disp_fb:
                                return disp_fb, raw
                            return "Jawab tayyar hai.", raw

                        if lang == "AR":
                            ar_clean = clean_arabic_display(raw)
                            if ar_clean:
                                return ar_clean, ar_clean
                        else:
                            en_clean = clean_ascii(raw)
                            if en_clean:
                                return en_clean, en_clean

                    print(f"Groq {model} ({resp.status_code}): {resp.text[:160]}")
            except Exception as ex:
                print(f"Groq {model} exception: {ex}")
    return None


async def wiki_fallback(query: str) -> Tuple[str, str]:
    clean_q = re.sub(
        r"\b(kya|hai|hain|he|ka|ki|ke|what|is|are|who|tell|me|about|batao|bataiye|sunao|maloomat)\b",
        "",
        query,
        flags=re.I,
    ).strip()
    clean_q = clean_ascii(clean_q) or clean_ascii(query)
    if not clean_q:
        msg = "Abhi AI server busy hai. Thodi der baad dobara poochein."
        return msg, "ابھی اے آئی سرور مصروف ہے۔ تھوڑی دیر بعد دوبارہ پوچھیں۔"

    headers = {"User-Agent": "ESP32VoiceAssistant/2.0"}
    try:
        async with httpx.AsyncClient(timeout=8.0, headers=headers) as client:
            s_url = (
                "https://en.wikipedia.org/w/api.php?action=query&list=search&srlimit=1&format=json&srsearch="
                + urllib.parse.quote(clean_q[:80])
            )
            sr = await client.get(s_url)
            title = clean_q
            if sr.status_code == 200:
                hits = sr.json().get("query", {}).get("search", [])
                if hits:
                    title = hits[0].get("title", clean_q)

            url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{urllib.parse.quote(title)}"
            r = await client.get(url)
            if r.status_code == 200:
                ext = (r.json().get("extract") or "").strip()
                if ext:
                    sents = re.split(r"(?<=[.!?])\s+", ext)
                    ans = clean_ascii(" ".join(sents[:3]))[:360]
                    if ans:
                        return ans, ans
    except Exception as ex:
        print(f"Wiki fallback error: {ex}")
    msg = "Abhi AI server busy hai. Thodi der baad dobara poochein."
    return msg, "ابھی اے آئی سرور مصروف ہے۔ تھوڑی دیر بعد دوبارہ پوچھیں۔"


async def transcribe_wav(wav_bytes: bytes) -> str:
    if not RAW_KEYS:
        return ""
    hint_prompt = "Surah Ikhlas ki tafseer sunao, Educational philosophy kya hai, Abhi time kya hua, Namaz, Cricket, News."
    for key in RAW_KEYS:
        try:
            async with httpx.AsyncClient(timeout=18.0) as client:
                files = {"file": ("audio.wav", wav_bytes, "audio/wav")}
                data = {
                    "model": "whisper-large-v3-turbo",
                    "prompt": hint_prompt,
                    "temperature": "0",
                }
                resp = await client.post(
                    "https://api.groq.com/openai/v1/audio/transcriptions",
                    headers={"Authorization": f"Bearer {key}"},
                    files=files,
                    data=data,
                )
                if resp.status_code == 200:
                    txt = (resp.json().get("text") or "").strip()
                    if txt.lower().strip(" .!?,") in WHISPER_HALLUCINATIONS or len(txt) < 2:
                        return ""
                    return txt
                print(f"Whisper ({resp.status_code}): {resp.text[:160]}")
        except Exception as e:
            print(f"Whisper error: {e}")
    return ""


async def fetch_google_tts_mp3(text: str, lang_code: str) -> bytes:
    tl = GTTS_LANG_MAP.get(lang_code, "ur")
    words = text.split()
    chunks: List[str] = []
    cur = ""
    for w in words:
        if len(cur) + len(w) + 1 <= 165:
            cur = (cur + " " + w).strip()
        else:
            if cur:
                chunks.append(cur)
            cur = w[:165]
    if cur:
        chunks.append(cur)

    mp3_bytes = bytearray()
    headers = {"User-Agent": "Mozilla/5.0"}
    async with httpx.AsyncClient(timeout=12.0, headers=headers) as client:
        for ch in chunks[:14]:
            url = (
                f"https://translate.google.com/translate_tts?ie=UTF-8&client=tw-ob"
                f"&tl={tl}&q={urllib.parse.quote(ch)}"
            )
            try:
                r = await client.get(url)
                if r.status_code == 200 and len(r.content) > 200:
                    mp3_bytes.extend(r.content)
            except Exception as ex:
                print(f"Google TTS chunk error: {ex}")
    return bytes(mp3_bytes)


async def make_tts_with_duration(text: str, voice_code: str) -> Tuple[str, int]:
    fid = uuid.uuid4().hex[:10]
    out_path = os.path.join(AUDIO_DIR, f"{fid}.mp3")
    voice = VOICE_MAP.get(voice_code, "ur-PK-AsadNeural")
    rate = SPEED_MAP.get(STATE["speed"], "+0%")
    clean_text = re.sub(r"[*_#`~]+", "", text or "").strip() or "جی بتائیے۔"

    audio_chunks = bytearray()
    last_end_ms = 0
    tts_source = "edge"

    try:
        comm = edge_tts.Communicate(clean_text, voice=voice, rate=rate)
        async for chunk in comm.stream():
            ctype = chunk.get("type")
            if ctype == "audio":
                audio_chunks.extend(chunk["data"])
            elif ctype in ("WordBoundary", "SentenceBoundary"):
                end_ms = int((chunk.get("offset", 0) + chunk.get("duration", 0)) / 10000)
                if end_ms > last_end_ms:
                    last_end_ms = end_ms
    except Exception as ex:
        print(f"Edge-TTS error: {ex}")

    if len(audio_chunks) < 800:
        tts_source = "google-fallback"
        fb_bytes = await fetch_google_tts_mp3(clean_text, voice_code)
        audio_chunks = bytearray(fb_bytes)
        last_end_ms = 0

    with open(out_path, "wb") as f:
        f.write(audio_chunks)

    bps = 6000.0 if tts_source == "edge" else 4000.0
    est_ms = int((len(audio_chunks) / bps) * 1000) if audio_chunks else 2500
    total_ms = max(last_end_ms, est_ms, 1500)
    print(f"TTS [{tts_source}] bytes={len(audio_chunks)} ms={total_ms}")

    now_t = time.time()
    for fn in os.listdir(AUDIO_DIR):
        fp = os.path.join(AUDIO_DIR, fn)
        try:
            if os.path.isfile(fp) and now_t - os.path.getmtime(fp) > 300:
                os.remove(fp)
        except Exception:
            pass

    return f"/audio/{fid}.mp3", total_ms


def build_synced_pages(display_text: str, total_ms: int, lang: str) -> List[Dict]:
    max_chars = 36 if lang == "AR" else 72
    txt = (display_text or "").strip()
    if not txt:
        return [{"t": "...", "ms": max(1500, total_ms)}]

    raw_sents = [s.strip() for s in re.split(r"(?<=[.!?؟۔])\s+", txt) if s.strip()]
    chunks: List[str] = []

    for s in raw_sents:
        if len(s) <= max_chars:
            chunks.append(s)
        else:
            words = s.split(" ")
            cur = ""
            for w in words:
                if not cur:
                    cur = w
                elif len(cur) + 1 + len(w) <= max_chars:
                    cur += " " + w
                else:
                    chunks.append(cur)
                    cur = w
            if cur:
                chunks.append(cur)

    chunks = chunks[:28]
    if not chunks:
        chunks = [txt[:max_chars]]

    weights = [max(6, len(c)) for c in chunks]
    w_sum = sum(weights)
    pages: List[Dict] = []
    for c, w in zip(chunks, weights):
        ms = max(1300, int(round(total_ms * (w / w_sum))))
        pages.append({"t": c, "ms": ms})
    return pages


async def generate_answer(heard: str, lang: str) -> Tuple[str, str, str]:
    now = get_ist_now()
    hd, hm_en, hm_ur, hy = gregorian_to_hijri(now.year, now.month, now.day)
    hijri_en = f"{hd} {hm_en} {hy}"
    hijri_ur = f"{hd} {hm_ur} {hy} ہجری"
    low = heard.lower()

    # 1. Namaz timings
    if any(w in low for w in ["namaz", "nimaz", "meeqat", "miqat", "prayer time", "salah time", "نماز", "صلاة", "اوقات", "नमाज", "नमाज़"]):
        disp, tts = build_namaz_response(lang)
        return "NAMAZ", disp, tts

    # 2. Time / Date / Hijri
    time_words = [
        "time", "waqt", "baj", "baje", "date", "tareekh", "tarikh", "hijri",
        "وقت", "ٹائم", "ٹانم", "بجے", "بجا", "تاریخ", "ہجری",
        "التاريخ", "الساعة", "वक्त", "टाइम", "बजे", "बजा", "तारीख",
    ]
    if any(w in low for w in time_words) and not any(w in low for w in ["namaz", "نماز", "match", "cricket"]):
        t_12 = now.strftime("%I:%M %p").lstrip("0")
        d_en = now.strftime("%A, %d %B %Y")
        period_ur = format_urdu_period_and_time(now)
        if lang == "AR":
            ans = f"الوقت الان {t_12}. التاريخ {d_en}. الهجري {hijri_en}."
            return "TIME", clean_arabic_display(ans), ans
        if lang == "UR":
            disp = f"Waqt {t_12}. Date {d_en}. Hijri {hijri_en}."
            tts = f"ابھی {period_ur}۔ آج تاریخ {d_en} ہے، اور ہجری تاریخ {hijri_ur} ہے۔"
            return "TIME", disp, tts
        ans = f"It is {t_12} on {d_en}. Hijri date is {hijri_en}."
        return "TIME", ans, ans

    # 3. Weather
    if any(w in low for w in ["mausam", "mosam", "weather", "temperature", "temp", "barish", "موسم", "الطقس", "मौसम"]):
        place, temp, wind = await fetch_weather_info(heard)
        if temp is not None:
            if lang == "AR":
                ans = f"الطقس في {place} الان {temp} درجة والرياح {wind} كيلومتر."
                return "WEATHER", clean_arabic_display(ans), ans
            if lang == "UR":
                disp = f"{place} mein abhi {temp} degree Celsius aur hawa {wind} kmh hai."
                tts = f"{place} میں ابھی درجہ حرارت {temp} ڈگری سینٹی گریڈ اور ہوا {wind} کلومیٹر فی گھنٹہ ہے۔"
                return "WEATHER", disp, tts
            ans = f"In {place}, it is currently {temp} degrees Celsius with wind at {wind} km/h."
            return "WEATHER", ans, ans

    # 4. Quran Tarjuma + Motabar Tafseer + Surah Detail (Nazool & Waqt Ke Halaat)
    s_num, s_ay, e_ay, is_last = parse_quran_request(heard)
    detail_words = ["detail", "details", "tafseel", "tafsil", "mukammal", "poora", "pura", "kahan", "kab", "waqt", "halaat", "halat", "nazool", "nuzool", "shaan", "مکمل", "تفصیل", "حالات", "نزول"]
    if is_tafseer_intent(heard) or (s_num is not None and any(w in low for w in detail_words)):
        if s_num:
            s_name_en, s_name_ar, total_ay, verse_block, ur_fb = await fetch_quran_verses(s_num, s_ay, e_ay, is_last)
            if verse_block:
                sys_p = (
                    "You are a careful, authentic Sunni Islamic scholar (aligned with Tafseer-e-Usmani, Ma'ariful Quran, and Tafseer al-Jalalayn). "
                    "Never invent personal opinions, psychology/nafsaniat metaphors, or unverified claims. Stick strictly to authentic Quranic meaning and classical shaan-e-nuzool.\n"
                    "Present the answer in this clear order:\n"
                    "1) Surah Name, Surah Number, total ayahs, and Makki or Madani status.\n"
                    "2) Place of nazool and historical circumstances at that time (waqt ke halaat / shaan-e-nuzool).\n"
                    "3) Clear tarjuma (translation) of the ayahs, followed by concise, authentic tafseer."
                )
                usr_p = (
                    f"User question: {heard}\n"
                    f"Surah Number: {s_num} | English Name: {s_name_en} | Arabic Name: {s_name_ar} | Total Ayahs: {total_ay}\n"
                    f"Verified Ayahs:\n{verse_block}"
                )
                res = await call_groq_bilingual(sys_p, usr_p, lang, max_tokens=1200)
                if res:
                    return f"SURAH {s_num}", res[0], res[1]
                en_fb = clean_ascii(verse_block)[:360]
                return f"SURAH {s_num}", en_fb, (ur_fb if lang == "UR" and ur_fb else en_fb)

    # 5. Live Cricket
    if any(w in low for w in ["cricket", "ipl", "t20", "odi", "test match", "score", "virat", "rohit", "babar", "کرکٹ", "اسکور", "میچ", "क्रिकेट"]):
        headlines = await fetch_cricket_context(heard)
        if headlines:
            sys_p = (
                f"You are a live cricket presenter. Today is {now.strftime('%d %b %Y, %I:%M %p IST')}. "
                "Report ONLY facts from the live cricket headlines below in 3 to 4 complete sentences."
            )
            usr_p = f"User question: {heard}\nLive Cricket Feeds:\n" + "\n".join(f"- {h}" for h in headlines)
            res = await call_groq_bilingual(sys_p, usr_p, lang, max_tokens=500)
            if res:
                return "CRICKET", res[0], res[1]
            fb = clean_ascii(". ".join(headlines[:2]))[:260]
            return "CRICKET", fb, fb

    # 6. Live News
    if any(w in low for w in ["news", "khabar", "khabrein", "khabrai", "headlines", "taza", "latest", "breaking", "خبر", "خبریں", "أخبار", "खबर", "खबरें"]):
        headlines = await fetch_news_context(heard)
        if headlines:
            sys_p = (
                f"You are an accurate live news anchor. Today is {now.strftime('%d %b %Y, %I:%M %p IST')}. "
                "Summarize the most relevant live headlines below clearly in 3 to 4 complete sentences."
            )
            usr_p = f"User asked: {heard}\nLive Headlines:\n" + "\n".join(f"- {h}" for h in headlines)
            res = await call_groq_bilingual(sys_p, usr_p, lang, max_tokens=550)
            if res:
                return "NEWS", res[0], res[1]
            fb = clean_ascii(". ".join(headlines[:2]))[:260]
            return "NEWS", fb, fb

    # 7. General Knowledge + Full Islamic Knowledge
    sys_p = (
        f"You are an accurate, all-rounder male voice assistant based in Srinagar, Kashmir. "
        f"Today is {now.strftime('%A, %d %B %Y, %I:%M %p IST')} (Hijri: {hijri_en}). "
        "You answer ALL general knowledge (education, philosophy, science, history, geography, tech, math) AND authentic Islamic knowledge (Quran, Hadith, Seerah, Fiqh, Duas). "
        "ADAPTIVE & COMPLETE LENGTH: Short factual questions get 2 clear sentences; broad/educational/Islamic topics get a thorough, complete answer ending cleanly with a full stop."
    )
    res = await call_groq_bilingual(sys_p, heard, lang, max_tokens=950)
    if res:
        return "ANSWER", res[0], res[1]

    disp, tts = await wiki_fallback(heard)
    return "INFO", disp, tts


@app.get("/")
async def root():
    return {
        "status": "ok",
        "voice": STATE["voice"],
        "speed": STATE["speed"],
        "volume": STATE["volume"],
        "monster": STATE["monster"],
        "hijri": get_hijri_str(),
    }


@app.get("/audio/{fid}")
async def get_audio(fid: str):
    if fid.endswith(".mp3"):
        fid = fid[:-4]
    safe_id = re.sub(r"[^a-zA-Z0-9]", "", fid)
    fp = os.path.join(AUDIO_DIR, f"{safe_id}.mp3")
    if os.path.exists(fp) and os.path.getsize(fp) > 200:
        return FileResponse(fp, media_type="audio/mpeg", filename=f"{safe_id}.mp3")
    return JSONResponse({"error": "not found"}, status_code=404)


@app.post("/chat")
async def chat(request: Request):
    wav_bytes = await request.body()
    heard = await transcribe_wav(wav_bytes)
    print(f"Heard: {heard}")

    if not heard:
        msg_d = "Awaaz saaf nahi aayi. Dobara bolein."
        msg_t = "آواز صاف نہیں آئی۔ دوبارہ بولیں۔"
        audio_url, total_ms = await make_tts_with_duration(msg_t, "UR-M")
        return {
            "action": "SPEAK",
            "lang": "UR",
            "header": "MIC",
            "heard": "",
            "display": msg_d,
            "pages": build_synced_pages(msg_d, total_ms, "UR"),
            "audio_url": audio_url,
            "voice": "UR-M",
            "volume": STATE["volume"],
            "monster": STATE["monster"],
        }

    lang = detect_language(heard)
    voice_code = {"UR": "UR-M", "EN": "EN-M", "AR": "AR-M"}.get(lang, "UR-M")
    STATE["voice"] = voice_code
    low = heard.lower()

    if any(k in low for k in ["stop", "ruko", "band karo", "chup", "بند کرو", "رکو", "توقف", "रुको"]) and "monster" not in low and "face" not in low:
        return {
            "action": "STOP",
            "lang": lang,
            "header": "STOP",
            "heard": clean_ascii(heard)[:20],
            "display": "Stopped.",
            "voice": voice_code,
            "volume": STATE["volume"],
            "monster": STATE["monster"],
        }

    if any(k in low for k in ["mute", "awaaz band", "sound off", "آواز بند"]):
        STATE["volume"] = 0
        return {
            "action": "MUTE",
            "lang": lang,
            "header": "MUTE",
            "heard": clean_ascii(heard)[:20],
            "display": "Muted.",
            "voice": voice_code,
            "volume": 0,
            "monster": STATE["monster"],
        }

    m_vol = re.search(r"(?:volume|awaaz|آواز)\s*(?:set|to|par|pe)?\s*(\d{1,2})", low)
    if m_vol:
        STATE["volume"] = max(2, min(18, int(m_vol.group(1))))
        msg_d = f"Volume {STATE['volume']} set ho gaya."
        msg_t = f"والیوم {STATE['volume']} سیٹ ہو گیا ہے۔" if lang == "UR" else f"Volume set to {STATE['volume']}."
        audio_url, total_ms = await make_tts_with_duration(msg_t, voice_code)
        return {
            "action": "SPEAK",
            "lang": lang,
            "header": "VOLUME",
            "heard": clean_ascii(heard)[:20],
            "display": msg_d,
            "pages": build_synced_pages(msg_d, total_ms, lang),
            "audio_url": audio_url,
            "voice": voice_code,
            "volume": STATE["volume"],
            "monster": STATE["monster"],
        }

    if any(k in low for k in ["volume up", "awaaz badhao", "awaaz tej", "awaaz tez", "louder", "آواز بڑھاؤ", "آواز تیز"]):
        STATE["volume"] = min(18, STATE["volume"] + 3)
        msg_d = f"Volume badha kar {STATE['volume']} kar diya."
        msg_t = f"والیوم بڑھا کر {STATE['volume']} کر دیا ہے۔" if lang == "UR" else f"Volume increased to {STATE['volume']}."
        audio_url, total_ms = await make_tts_with_duration(msg_t, voice_code)
        return {
            "action": "SPEAK",
            "lang": lang,
            "header": "VOLUME",
            "heard": clean_ascii(heard)[:20],
            "display": msg_d,
            "pages": build_synced_pages(msg_d, total_ms, lang),
            "audio_url": audio_url,
            "voice": voice_code,
            "volume": STATE["volume"],
            "monster": STATE["monster"],
        }

    if any(k in low for k in ["volume down", "awaaz kam", "softer", "quieter", "آواز کم"]):
        STATE["volume"] = max(3, STATE["volume"] - 3)
        msg_d = f"Volume kam kar ke {STATE['volume']} kar diya."
        msg_t = f"والیوم کم کر کے {STATE['volume']} کر دیا ہے۔" if lang == "UR" else f"Volume decreased to {STATE['volume']}."
        audio_url, total_ms = await make_tts_with_duration(msg_t, voice_code)
        return {
            "action": "SPEAK",
            "lang": lang,
            "header": "VOLUME",
            "heard": clean_ascii(heard)[:20],
            "display": msg_d,
            "pages": build_synced_pages(msg_d, total_ms, lang),
            "audio_url": audio_url,
            "voice": voice_code,
            "volume": STATE["volume"],
            "monster": STATE["monster"],
        }

    if any(k in low for k in ["monster", "monister", "animation", "chehra", "face", "مونسٹر", "چہرہ"]):
        if any(k in low for k in ["off", "band", "close", "hatao", "hide", "home", "بند", "ہٹاؤ"]):
            STATE["monster"] = False
            msg_d = "Monster face band ho gaya."
            msg_t = "مونسٹر فیس بند کر دیا گیا ہے۔" if lang == "UR" else "Monster face turned off."
            audio_url, total_ms = await make_tts_with_duration(msg_t, voice_code)
            return {
                "action": "MONSTER_OFF",
                "lang": lang,
                "header": "HOME",
                "display": msg_d,
                "pages": build_synced_pages(msg_d, total_ms, lang),
                "audio_url": audio_url,
                "voice": voice_code,
                "volume": STATE["volume"],
                "monster": False,
            }
        if any(k in low for k in ["on", "kholo", "open", "dikhao", "chalu", "start", "show", "کھولو", "دکھاؤ", "چالو"]):
            STATE["monster"] = True
            msg_d = "Monster face chalu ho gaya."
            msg_t = "مونسٹر فیس آن کر دیا گیا ہے۔" if lang == "UR" else "Monster face turned on."
            audio_url, total_ms = await make_tts_with_duration(msg_t, voice_code)
            return {
                "action": "MONSTER_ON",
                "lang": lang,
                "header": "MONSTER",
                "display": msg_d,
                "pages": build_synced_pages(msg_d, total_ms, lang),
                "audio_url": audio_url,
                "voice": voice_code,
                "volume": STATE["volume"],
                "monster": True,
            }

    s_num, _, _, _ = parse_quran_request(heard)
    is_quran_play = any(w in low for w in [
        "surah", "sura", "sury", "suraj", "quran", "tilawat", "recite", "recitation",
        "سورہ", "سورة", "قرآن", "تلاوت", "सूरह", "कुरान"
    ])
    if is_quran_play and not is_tafseer_intent(heard) and not (s_num is not None and any(w in low for w in detail_words)):
        num = s_num or 1
        stream_url = f"https://server8.mp3quran.net/afs/{num:03d}.mp3"
        return {
            "action": "STREAM",
            "lang": "EN",
            "header": f"SURAH {num:03d}",
            "title": f"SURAH {num:03d}",
            "url": stream_url,
            "voice": voice_code,
            "volume": STATE["volume"],
            "monster": STATE["monster"],
        }

    header, disp, tts = await generate_answer(heard, lang)
    audio_url, total_ms = await make_tts_with_duration(tts, voice_code)
    pages = build_synced_pages(disp, total_ms, lang)

    return {
        "action": "SPEAK",
        "lang": lang,
        "header": header,
        "heard": clean_ascii(heard)[:16],
        "display": disp,
        "pages": pages,
        "audio_url": audio_url,
        "voice": voice_code,
        "volume": STATE["volume"],
        "monster": STATE["monster"],
    }
