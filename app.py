import os
import re
import json
import time
import uuid
import asyncio
import unicodedata
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
HISTORY_FILE = "/tmp/chat_history.json"
TAFHEEM_NOTES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tafheem_notes.json")
MAX_HISTORY = 30
AUDIO_TTL_SEC = 900

# Strictly Tafheem-ul-Quran (Syed Abul A'la Maududi) Real Human Audio Sources (All 114 Surahs)
ARCHIVE_TAFHEEM_FULL_ID = "Tafheem-ul-Quran-by-Syed-Abul-Ala-Moududi-Audio-MP3-CD"
ARCHIVE_TAFHEEM_SHORT_ID = "Tafheem-ul-Quran-by-Syed-Abul-Ala-Moududi-Urdu-Audio-MP3-CD"
QURANURDU_MISHARI_PAGE = "https://www.quranurdu.com/quran_mishari/"
# Optional: set SEERAT_ARCHIVE_ID in Render Environment ONLY if you want a specific Archive.org Ar-Raheeq MP3 item
CUSTOM_SEERAT_ARCHIVE_ID = os.getenv("SEERAT_ARCHIVE_ID", "").strip()

ARCHIVE_MAP_CACHE: Dict[str, Dict[int, str]] = {}
QURANURDU_MAP_CACHE: Dict[int, Dict[int, str]] = {}
SEERAT_MP3_CACHE: Dict[int, str] = {}
SEERAT_CACHE_TS: float = 0.0
RESOLVED_URL_CACHE: Dict[str, Tuple[float, str]] = {}
TAFHEEM_NOTES_CACHE: Optional[Dict[str, str]] = None

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
    "llama-3.3-70b-versatile",
    "meta-llama/llama-4-maverick-17b-128e-instruct",
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "llama-3.1-8b-instant",
    "qwen/qwen3-32b",
    "openai/gpt-oss-120b",
]

MODEL_CACHE: Dict[str, Tuple[float, List[str]]] = {}

STATE = {
    "voice": "UR-M",
    "speed": "Norm",
    "volume": 11,
    "last_nonzero_volume": 11,
    "monster": False,
}

LIMIT_STATUS: Dict[str, Optional[str]] = {
    "model": None,
    "remaining_requests": None,
    "limit_requests": None,
    "remaining_tokens": None,
    "limit_tokens": None,
    "updated_at": None,
}

# STRICTLY LOCKED TO ONLY THESE TWO ASSISTANTS (Urdu: Asad, English: Prabhat) — ZERO OTHER VOICES
VOICE_CANDIDATES = {
    "UR-M": ["ur-PK-AsadNeural"],
    "EN-M": ["en-IN-PrabhatNeural"],
}

SPEED_MAP = {
    "Slow": "-16%",
    "Norm": "+0%",
    "Fast": "+12%",
}

# Strictly ONE Book: Ar-Raheeq Al-Makhtum (الرحیق المختوم — Maulana Safi-ur-Rahman Mubarakpuri), 20 Topic-Wise Chapters
SEERAT_CHAPTERS: Dict[int, Tuple[str, str, List[str]]] = {
    1:  ("Arab Before Islam", "اسلام سے پہلے عرب کے حالات", ["arab ke halaat", "jahiliyat", "before islam", "عرب کے حالات"]),
    2:  ("Birth and Childhood", "ولادت باسعادت اور بچپن", ["wiladat", "birth", "bachpan", "halima sadia", "ولادت", "بچپن"]),
    3:  ("Youth and Marriage to Khadijah", "جوانی، تجارت اور حضرت خدیجہ سے نکاح", ["jawani", "khadijah", "khadeeja", "جوانی", "خدیجہ"]),
    4:  ("Rebuilding of Kaaba and Hajar-e-Aswad", "تعمیر کعبہ اور حجر اسود کا واقعہ", ["hajar e aswad", "hajr e aswad", "tameer e kaaba", "حجر اسود", "تعمیر کعبہ"]),
    5:  ("First Revelation in Cave Hira", "غار حرا اور پہلی وحی", ["ghar e hira", "pehli wahi", "first revelation", "غار حرا", "پہلی وحی"]),
    6:  ("Secret and Open Dawah in Makkah", "مکہ میں خفیہ اور اعلانیہ دعوت", ["dawat e islam", "makkah dawah", "koh e safa", "دعوت اسلام"]),
    7:  ("Persecution and Migration to Abyssinia", "قریش کے مظالم اور ہجرت حبشہ", ["habsha", "abyssinia", "hijrat e habsha", "ہجرت حبشہ"]),
    8:  ("Boycott, Year of Sorrow and Taif", "شعب ابی طالب، عام الحزن اور سفر طائف", ["taif", "safar e taif", "aam ul huzn", "shib e abi talib", "طائف", "عام الحزن"]),
    9:  ("Isra and Miraj", "واقعہ اسراء اور معراج", ["miraj", "meraj", "shab e miraj", "isra", "معراج", "اسراء"]),
    10: ("Pledges of Aqabah", "بیعت عقبہ اولیٰ اور ثانیہ", ["aqabah", "aqaba", "bait e aqaba", "بیعت عقبہ"]),
    11: ("Migration to Madinah", "ہجرت مدینہ اور غار ثور", ["hijrat", "hijrah", "hijrat e madina", "ghar e saur", "ہجرت مدینہ", "غار ثور"]),
    12: ("Masjid Nabawi and Charter of Madinah", "مسجد نبوی، مواخات اور میثاق مدینہ", ["masjid e nabawi", "muwakhat", "meesaq e madina", "میثاق مدینہ"]),
    13: ("Battle of Badr", "غزوہ بدر", ["badr", "badar", "ghazwa e badr", "jung e badr", "غزوہ بدر", "بدر"]),
    14: ("Battle of Uhud", "غزوہ احد", ["uhud", "ohad", "ghazwa e uhud", "jung e uhud", "غزوہ احد", "احد"]),
    15: ("Battle of the Trench", "غزوہ خندق اور احزاب", ["khandaq", "khandak", "ghazwa e khandaq", "خندق", "غزوہ خندق"]),
    16: ("Treaty of Hudaibiyah", "صلح حدیبیہ اور بیعت رضوان", ["hudaibiyah", "hudaibiya", "sulah hudaibiya", "bait e rizwan", "صلح حدیبیہ", "حدیبیہ"]),
    17: ("Letters to Kings and Battle of Khyber", "بادشاہوں کو خطوط اور غزوہ خیبر", ["khyber", "khaibar", "ghazwa e khyber", "خیبر", "غزوہ خیبر"]),
    18: ("Conquest of Makkah", "فتح مکہ", ["fatah makkah", "fateh makkah", "conquest of makkah", "فتح مکہ"]),
    19: ("Hunain, Tabuk and Farewell Sermon", "غزوہ حنین، تبوک اور خطبہ حجۃ الوداع", ["hunain", "tabuk", "hajjatul wida", "khutba hajjatul wida", "farewell sermon", "حجۃ الوداع", "تبوک", "حنین"]),
    20: ("Final Days and Passing", "آخری ایام اور وصال مبارک", ["wisaal", "wafat", "wafaat", "وصال", "وفات"]),
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
    "fatiha": 1, "fatihah": 1, "fateha": 1, "al fatiha": 1, "فاتحہ": 1, "الفاتحة": 1, "फातिहा": 1,
    "baqarah": 2, "baqara": 2, "baqra": 2, "bakra": 2, "al baqarah": 2, "بقرہ": 2, "البقرة": 2, "बकरा": 2, "बकरह": 2,
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
    "kahf": 18, "kahaf": 18, "al kahf": 18, "alkahf": 18, "tul kahf": 18, "tulkah": 18, "کہف": 18, "الكهف": 18, "कहफ": 18, "कहफ़": 18,
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
    "fath": 48, "fatah": 48, "فتح": 48, "الفتح": 48, "फतह": 48,
    "hujurat": 49, "حجرات": 49,
    "qaf": 50, "ق": 50,
    "dhariyat": 51, "zariyat": 51, "ذاریات": 51,
    "tur": 52, "toor": 52, "طور": 52,
    "najm": 53, "نجم": 53,
    "qamar": 54, "قمر": 54,
    "rahman": 55, "rehman": 55, "al rahman": 55, "ar rahman": 55, "رحمن": 55, "الرحمن": 55, "रहमान": 55,
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
    "mulk": 67, "al mulk": 67, "tabarak": 67, "ملک": 67, "الملك": 67, "मुल्क": 67,
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
    "ikhlas": 112, "ekhlas": 112, "iklas": 112, "qul huwallah": 112, "اخلاص": 112, "الإخلاص": 112, "इखलास": 112, "इख़लास": 112,
    "falaq": 113, "falak": 113, "فلق": 113, "الفلق": 113, "फलक": 113,
    "naas": 114, "nas": 114, "ناس": 114, "الناس": 114, "नास": 114,
}

MADANI_SURAHS = {2, 3, 4, 5, 8, 9, 22, 24, 33, 47, 48, 49, 55, 57, 58, 59, 60, 61, 62, 63, 64, 65, 66, 76, 98, 110}

NUMBER_WORDS = {
    "ek": 1, "aik": 1, "one": 1, "pehli": 1, "pehla": 1, "pehlay": 1, "pehle": 1, "first": 1, "ایک": 1, "پہلی": 1, "پہلا": 1, "الأولى": 1, "पहली": 1, "पहला": 1,
    "two": 2, "second": 2, "doosra": 2, "dusra": 2, "دو": 2, "دوسرا": 2, "اثنين": 2, "दो": 2, "दूसरा": 2,
    "teen": 3, "three": 3, "third": 3, "teesra": 3, "تین": 3, "تیسرا": 3, "ثلاث": 3, "तीन": 3,
    "chaar": 4, "char": 4, "four": 4, "fourth": 4, "chautha": 4, "چار": 4, "أربع": 4, "चार": 4,
    "paanch": 5, "panch": 5, "five": 5, "fifth": 5, "پانچ": 5, "خمس": 5, "पांच": 5, "पाँच": 5,
    "cheh": 6, "chhe": 6, "six": 6, "sixth": 6, "چھ": 6, "ست": 6, "छह": 6,
    "saat": 7, "sat": 7, "seven": 7, "seventh": 7, "سات": 7, "سبع": 7, "सात": 7,
    "aath": 8, "ath": 8, "eight": 8, "eighth": 8, "آٹھ": 8, "ثمان": 8, "आठ": 8,
    "nau": 9, "nine": 9, "ninth": 9, "نو": 9, "تسع": 9, "नौ": 9,
    "das": 10, "ten": 10, "tenth": 10, "دس": 10, "عشر": 10, "दस": 10,
    "gyarah": 11, "eleven": 11, "بارہ": 12, "barah": 12, "twelve": 12,
    "terah": 13, "thirteen": 13, "chaudah": 14, "fourteen": 14, "pandrah": 15, "fifteen": 15,
    "solah": 16, "sixteen": 16, "satrah": 17, "seventeen": 17, "atharah": 18, "eighteen": 18,
    "unnees": 19, "nineteen": 19, "bees": 20, "twenty": 20,
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
    "subtitles by", "amara.org", "thank you for watching",
}

WEATHER_STOPWORDS = {
    "aaj", "kal", "today", "tomorrow", "abhi", "now", "kaisa", "kaisi", "kaise",
    "weather", "mausam", "mosam", "temperature", "temp", "forecast", "barish",
    "baarish", "rain", "snow", "snowfall", "kya", "hai", "hogi", "hoga", "hain",
    "cold", "hot", "sardi", "garmi", "thand", "thandi", "dhoop", "hawa", "badal",
    "barf", "yahan", "idhar", "here", "outside", "bahar", "batao", "bataiye",
    "tell", "me", "the", "my", "area", "right", "currently", "please", "sir", "bhai",
}

URDU_TO_ROMAN_MAP = {
    "آ": "aa", "ا": "a", "أ": "a", "إ": "i", "ب": "b", "پ": "p", "ت": "t", "ٹ": "t",
    "ث": "s", "ج": "j", "چ": "ch", "ح": "h", "خ": "kh", "د": "d", "ڈ": "d", "ذ": "z",
    "ر": "r", "ڑ": "r", "ز": "z", "ژ": "zh", "س": "s", "ش": "sh", "ص": "s", "ض": "z",
    "ط": "t", "ظ": "z", "ع": "a", "غ": "gh", "ف": "f", "ق": "q", "ک": "k", "ك": "k",
    "گ": "g", "ل": "l", "م": "m", "ن": "n", "ں": "n", "و": "o", "ہ": "h", "ه": "h",
    "ھ": "h", "ۃ": "t", "ة": "h", "ء": "", "ی": "i", "ي": "i", "ے": "e", "ئ": "i",
    "۔": ".", "،": ",", "؟": "?", "؛": ";",
}

# Targeted Urdu TTS pronunciation fixes for ur-PK-AsadNeural (fixes mispronounced words heard in testing)
URDU_PRONUNCIATION_RULES: List[Tuple[str, str]] = [
    (r"(?<!\w)مکی(?!\w)", "مَکّی"),
    (r"(?<!\w)مومن(?!\w)", "مؤمِن"),
    (r"(?<!\w)مومنین(?!\w)", "مؤمِنین"),
    (r"(?<!\w)مومنوں(?!\w)", "مؤمِنوں"),
    (r"(?<!\w)ملتی(?!\w)", "مِلتی"),
    (r"(?<!\w)ملتا(?!\w)", "مِلتا"),
    (r"(?<!\w)ملتے(?!\w)", "مِلتے"),
    (r"(?<!\w)ملنا(?!\w)", "مِلنا"),
    (r"(?<!\w)جلتی(?!\w)", "جُلتی"),
    (r"(?<!\w)اخری(?!\w)", "آخری"),
    (r"(?<!\w)اخر(?!\w)", "آخر"),
    (r"(?<!\w)تورنت(?!\w)", "فوراً"),
    (r"(?<!\w)تاپمان(?!\w)", "درجہ حرارت"),
    (r"(?<!\w)لگ بھگ(?!\w)", "تقریباً"),
    (r"ٹھنڈی\s+ہوا", "ٹھنڈی ہَوا"),
    (r"تیز\s+ہوا", "تیز ہَوا"),
    (r"گرم\s+ہوا", "گرم ہَوا"),
    (r"ہوا\s+چل", "ہَوا چل"),
    (r"اور\s+ہوا\s+", "اور ہَوا "),
    (r"\b(?:IST|UTC|GMT)\b", ""),
    (r"(?<!\w)اِسٹ(?!\w)", ""),
    (r"(?<!\w)آئی\s*ایس\s*ٹی(?!\w)", ""),
    (r"\s+یا\s+", "، یا "),
]


def normalize_input(text: str) -> str:
    if not text:
        return ""
    s = unicodedata.normalize("NFC", text)
    out = []
    for ch in s:
        o = ord(ch)
        if 0x0660 <= o <= 0x0669:
            out.append(chr(ord("0") + (o - 0x0660)))
        elif 0x06F0 <= o <= 0x06F9:
            out.append(chr(ord("0") + (o - 0x06F0)))
        elif 0x0966 <= o <= 0x096F:
            out.append(chr(ord("0") + (o - 0x0966)))
        else:
            out.append(ch)
    return "".join(out)


def has_word(text: str, words: List[str]) -> bool:
    low = (text or "").lower()
    for w in words:
        if re.search(rf"(?<!\w){re.escape(w.lower())}(?!\w)", low):
            return True
    return False


def urdu_to_roman_fallback(text: str) -> str:
    s = normalize_input(text or "")
    s = re.sub(r"[\u064B-\u065F\u0670\u06D6-\u06ED\u0640]", "", s)
    out = []
    for ch in s:
        if ch in URDU_TO_ROMAN_MAP:
            out.append(URDU_TO_ROMAN_MAP[ch])
        elif 32 <= ord(ch) <= 126:
            out.append(ch)
        else:
            out.append(" ")
    return re.sub(r"\s+", " ", "".join(out)).strip()[:320]


def load_tafheem_notes() -> Dict[str, str]:
    global TAFHEEM_NOTES_CACHE
    if TAFHEEM_NOTES_CACHE is not None:
        return TAFHEEM_NOTES_CACHE
    TAFHEEM_NOTES_CACHE = {}
    if os.path.exists(TAFHEEM_NOTES_FILE):
        try:
            with open(TAFHEEM_NOTES_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    TAFHEEM_NOTES_CACHE = {str(k): str(v) for k, v in data.items()}
                    print(f"Loaded {len(TAFHEEM_NOTES_CACHE)} verbatim Tafheem notes")
        except Exception as ex:
            print(f"Tafheem notes load error: {ex}")
    return TAFHEEM_NOTES_CACHE


def load_history() -> List[Dict]:
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data[-MAX_HISTORY:]
        except Exception as ex:
            print(f"History load error: {ex}")
    return []


def append_history(entry: Dict) -> None:
    hist = load_history()
    hist.append(entry)
    hist = hist[-MAX_HISTORY:]
    tmp_path = f"{HISTORY_FILE}.{uuid.uuid4().hex[:6]}.tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(hist, f, ensure_ascii=False)
        os.replace(tmp_path, HISTORY_FILE)
    except Exception as ex:
        print(f"History save error: {ex}")
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass


def clear_history() -> None:
    try:
        if os.path.exists(HISTORY_FILE):
            os.remove(HISTORY_FILE)
    except Exception as ex:
        print(f"History clear error: {ex}")


def get_ist_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=5, minutes=30)))


def update_groq_limits(resp: httpx.Response, model: str) -> None:
    try:
        rem_r = resp.headers.get("x-ratelimit-remaining-requests")
        lim_r = resp.headers.get("x-ratelimit-limit-requests")
        rem_t = resp.headers.get("x-ratelimit-remaining-tokens")
        lim_t = resp.headers.get("x-ratelimit-limit-tokens")
        if rem_r or rem_t:
            LIMIT_STATUS["model"] = model
            if rem_r:
                LIMIT_STATUS["remaining_requests"] = rem_r
            if lim_r:
                LIMIT_STATUS["limit_requests"] = lim_r
            if rem_t:
                LIMIT_STATUS["remaining_tokens"] = rem_t
            if lim_t:
                LIMIT_STATUS["limit_tokens"] = lim_t
            LIMIT_STATUS["updated_at"] = get_ist_now().strftime("%I:%M %p IST")
    except Exception:
        pass


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


def strip_think_tags(text: str) -> str:
    return re.sub(r"<think>.*?(?:</think>|\Z)", "", text or "", flags=re.S).strip()


def clean_ascii(text: str) -> str:
    s = strip_think_tags(text)
    s = re.sub(r"\b(?:DISPLAY|VOICE)\s*:\s*", "", s, flags=re.I)
    s = s.replace("\u2019", "'").replace("\u2018", "'")
    s = re.sub(r"[^\x20-\x7E]", " ", s)
    s = re.sub(r"[*_#`~\"]+", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def clean_urdu_for_tts(text: str, keep_clock_colons: bool = False) -> str:
    s = strip_think_tags(text)
    s = re.sub(r"\b(?:DISPLAY|VOICE)\s*:\s*", "", s, flags=re.I)
    # Prevent Edge-TTS Urdu from reading Surah:Ayah like 2:43 as "2 baj kar 43 minute"
    if not keep_clock_colons:
        s = re.sub(r"[\(\[]?\b(\d{1,3})\s*:\s*(\d{1,3})\b[\)\]]?", r" سورہ \1 آیت \2 ", s)
    s = re.sub(r"[\(\[]\s*[\d\u0660-\u0669\u06F0-\u06F9]+\s*[\)\]]", "", s)
    s = re.sub(r"[\u064B-\u065F\u0670\u06D6-\u06ED\u0640]", "", s)
    s = s.replace("\u0671", "ا").replace("أ", "ا").replace("إ", "ا")
    s = s.replace("الله", "اللہ").replace("لله", "للہ")
    s = s.replace("(", " ").replace(")", " ").replace("[", " ").replace("]", " ")
    s = re.sub(r"[*_#`~•\"']+", "", s)
    # Apply selective pronunciation diacritics AFTER stripping raw harakat
    for pat, repl in URDU_PRONUNCIATION_RULES:
        s = re.sub(pat, repl, s)
    s = re.sub(r"\s*،\s*،+", "، ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def clean_english_for_tts(text: str) -> str:
    s = clean_voice_script(text)
    s = s.replace("->", " to ").replace("=>", " to ")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def clean_surah_name_urdu(s_name_ar: str, surah_num: int) -> str:
    s = clean_urdu_for_tts(s_name_ar)
    s = re.sub(r"^(?:سورة|سورہ|سورۃ|سوره)\s+", "", s).strip()
    s = s.replace("ة", "ہ").replace("ۃ", "ہ")
    if s.startswith("ال") and len(s) > 3:
        s = s[2:]
    if s:
        return f"سورہ {s}"
    return f"سورہ نمبر {surah_num}"


def clean_voice_script(text: str) -> str:
    s = strip_think_tags(text)
    s = re.sub(r"\b(?:DISPLAY|VOICE)\s*:\s*", "", s, flags=re.I)
    s = re.sub(r"[*_#`~•]+", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def detect_length_mode(text: str) -> str:
    if has_word(text, [
        "short", "brief", "briefly", "mukhtasar", "chhota", "chota", "kam lafzon",
        "in short", "one line", "two lines", "مختصر", "چھوٹا", "संक्षिप्त", "छोटा"
    ]):
        return "brief"
    if has_word(text, [
        "long", "detailed", "detail", "tafseel", "tafsil", "mukammal", "poora", "pura",
        "in depth", "deeply", "expand", "مفصل", "تفصیل", "مکمل", "विस्तार", "पूरा"
    ]):
        return "long"
    return "normal"


def detect_part_num(text: str) -> int:
    if has_word(text, [
        "part 2", "part two", "part do", "second part",
        "hissa 2", "hissa do", "doosra hissa", "dusra hissa",
        "پارٹ 2", "حصہ 2", "دوسرا حصہ", "पार्ट 2", "दूसरा हिस्सा",
    ]):
        return 2
    return 1


def format_history_for_prompt() -> str:
    hist = load_history()
    if not hist:
        return ""
    lines = []
    for idx, item in enumerate(hist[-10:], 1):
        lines.append(f"{idx}. User: {item.get('u', '')} | Assistant: {item.get('a', '')}")
    return (
        "Conversation History (use this for follow-up questions):\n"
        + "\n".join(lines)
        + "\n\n"
    )


def is_namaz_query(text: str) -> bool:
    if has_word(text, [
        "meeqat", "miqat", "prayer time", "prayer times", "salah time", "salah times",
        "namaz ka waqt", "namaz ka time", "namaz time", "namaz timings", "nimaz ka waqt",
        "اوقات نماز", "نماز کا وقت", "نماز کا ٹائم", "नमाज का वक्त", "नमाज़ का वक्त",
    ]):
        return True
    prayer_names = ["fajr", "fajar", "zohar", "zuhr", "dhuhr", "asr", "asar", "maghrib", "magrib", "esha", "isha", "فجر", "ظہر", "عصر", "مغرب", "عشاء"]
    time_hints = ["time", "timing", "timings", "waqt", "kab", "kitne baje", "baje", "وقت", "ٹائم", "کب", "بجے", "वक्त", "कब", "बजे"]
    masail_hints = ["masla", "masayil", "masail", "tareeqa", "tarika", "farz", "sunnat", "wajib", "qaza", "toot", "sajda", "rakat", "مسئلہ", "مسائل", "طریقہ", "رکعت"]
    if has_word(text, prayer_names) and has_word(text, time_hints) and not has_word(text, masail_hints) and not has_word(text, ["surah", "sura", "suratul", "tafseer", "tafsir", "tarjuma", "ayat", "aayat", "سورہ", "سورۃ", "سورة", "تفسیر", "ترجمہ", "آیت"]):
        return True
    return False


def is_limit_query(text: str) -> bool:
    return has_word(text, [
        "limit kitni", "kitni limit", "quota", "api limit", "groq limit",
        "limit bachi", "kitna bacha", "remaining limit", "daily limit", "لمٹ کتنی",
    ])


def is_time_or_date_query(text: str) -> bool:
    if is_namaz_query(text) or is_weather_query(text):
        return False
    exclude = [
        "time complexity", "space complexity", "part time", "full time", "first time",
        "last time", "real time", "every time", "date of birth", "release date",
        "exam date", "match", "cricket", "history", "update", "updated", "data",
    ]
    if has_word(text, exclude):
        return False
    if has_word(text, ["hijri", "ہجری", "التاريخ", "الساعة"]):
        return True
    clock_phrases = [
        "what time", "what is the time", "current time", "tell me the time",
        "time kya", "kya time", "waqt kya", "kya waqt", "kitna time", "kitna waqt",
        "kitne baje", "kya baje", "abhi time", "abhi waqt",
        "today date", "today's date", "what is the date", "what date", "what day is",
        "aaj ki date", "aaj ki tareekh", "aaj ki tarikh", "aaj kya date", "aaj kya tareekh",
        "aaj kaun sa din", "aaj kya din",
        "ٹائم کیا", "وقت کیا", "کتنے بجے", "آج کی تاریخ", "آج کیا تاریخ", "آج کون سا دن",
        "आज की तारीख", "कितने बजे", "टाइम क्या", "वक्त क्या",
    ]
    if has_word(text, clock_phrases):
        return True
    toks = re.findall(r"[\w\u0600-\u06FF\u0900-\u097F]+", (text or "").lower())
    if len(toks) <= 4 and has_word(text, ["time", "waqt", "date", "tareekh", "tarikh", "baje", "وقت", "ٹائم", "تاریخ", "بجے", "तारीख", "टाइम"]):
        return True
    return False


def is_weather_query(text: str) -> bool:
    if has_word(text, ["temporary", "template", "attempt", "contempt"]):
        return False
    if has_word(text, [
        "mausam", "mosam", "weather", "temperature", "forecast", "barish", "baarish",
        "rain", "snowfall", "موسم", "بارش", "الطقس", "मौसम", "बारिश",
    ]):
        return True
    if has_word(text, ["thand", "thandi", "sardi", "garmi", "dhoop", "barf", "ٹھنڈ", "سردی", "گرمی", "برف"]) and has_word(text, ["aaj", "abhi", "kal", "kaisi", "kaisa", "kitni", "shopian", "srinagar", "pulwama", "kashmir", "bahar", "آج", "ابھی"]):
        return True
    return False


def is_cricket_query(text: str) -> bool:
    if has_word(text, ["cricket", "ipl", "t20", "odi", "test match", "virat", "rohit", "babar", "psl", "bbl", "world cup", "asia cup", "wicket", "کرکٹ", "क्रिकेट"]):
        return True
    if has_word(text, ["score", "live score", "match", "میچ", "اسکور"]) and has_word(text, ["india", "pakistan", "australia", "england", "match", "live", "team", "series", "today", "aaj", "انڈیا", "پاکستان"]):
        return True
    return False


def is_news_query(text: str) -> bool:
    return has_word(text, [
        "news", "khabar", "khabrein", "khabrai", "headlines", "taza khabar",
        "breaking news", "latest news", "خبر", "خبریں", "أخبار", "खबर", "खबरें",
    ])


def is_islamic_masayil_query(text: str) -> bool:
    islamic_keywords = [
        "masla", "masail", "masayil", "maslaa", "fiqh", "fatwa", "shariat", "shariah", "sharia",
        "islamic", "islam", "deen", "deeni", "mazhab",
        "wuzu", "wudu", "wazoo", "ghusl", "ghusal", "gusl", "tayammum", "tayamum", "najasat", "paki", "napaki",
        "namaz", "nimaz", "salah", "salat", "rakat", "rakaat", "sajda", "sajdah", "sajda sahw", "sajda e sahw",
        "qaza", "witr", "witar", "tahajjud", "ishraq", "chasht", "istikhara", "janaza", "janazah", "jamaat", "imamat", "musafir", "qasr",
        "roza", "roze", "ramzan", "ramadan", "sehri", "iftar", "iftari", "itikaf", "fidya", "kaffara", "kaffarah", "taraweeh", "tarawih",
        "zakat", "zakaat", "nisab", "nisaab", "sadaqah", "sadqa", "fitrana", "fitra", "ushr",
        "hajj", "umrah", "umra", "ihram", "tawaf", "sai", "qurbani", "udhiyah", "aqiqah", "aqiqa",
        "nikah", "talaq", "khula", "iddat", "mehr", "mahr", "virasat", "wirasat", "miras", "inheritance",
        "halal", "haram", "haraam", "makrooh", "makruh", "jaiz", "jayaz", "najaiz", "farz", "fard", "wajib", "sunnat", "sunnah", "nafil", "mustahab", "bidat", "bidah",
        "sood", "riba", "interest haram", "gunah", "sawab", "tauba", "tawbah", "istighfar", "zikr", "dhikr", "durood", "darood", "dua", "duain", "masnoon",
        "aqeedah", "aqeeda", "tawheed", "tauheed", "shirk", "risalat", "akhirat", "qiyamat", "barzakh", "jannat", "jahannam", "farishte", "malaika",
        "hadith", "hadees", "bukhari", "muslim", "tirmizi", "abu dawood", "sahaba", "sahabi", "khulafa", "khilafat",
        "hazrat abu bakr", "hazrat umar", "hazrat usman", "hazrat ali", "imam abu hanifa", "imam shafii", "imam malik", "imam ahmad",
        "anbiya", "paighambar", "nabi", "rasool", "rasul", "hazrat adam", "hazrat ibrahim", "hazrat musa", "hazrat isa", "hazrat yusuf", "hazrat nooh", "hazrat sulaiman", "hazrat dawood",
        "مسئلہ", "مسائل", "فقہ", "فتوی", "شریعت", "اسلام", "دین", "وضو", "غسل", "تیمم", "نماز", "سجدہ", "قضا", "وتر", "تہجد", "استخارہ", "جنازہ",
        "روزہ", "رمضان", "سحری", "افطار", "زکوۃ", "زکات", "نصاب", "صدقہ", "حج", "عمرہ", "قربانی", "نکاح", "طلاق", "عدت", "وراثت",
        "حلال", "حرام", "مکروہ", "جائز", "ناجائز", "فرض", "واجب", "سنت", "نفل", "بدعت", "سود", "توبہ", "دعا", "درود",
        "عقیدہ", "توحید", "شرک", "آخرت", "قیامت", "جنت", "جہنم", "حدیث", "احادیث", "صحابہ", "خلفاء", "انبیاء",
        "मसला", "मसाइल", "वुजू", "गुस्ल", "नमाज", "नमाज़", "रोजा", "जकात", "हज", "उमराह", "निकाह", "तलाक", "हलाल", "हराम", "हदीस", "इस्लाम",
    ]
    return has_word(text, islamic_keywords)


def is_seerat_follow_up(text: str) -> bool:
    return has_word(text, [
        "agla chapter", "next chapter", "agla bab", "agla baab", "is se agla chapter",
        "agla topic", "next topic", "next seerat", "seerat agla", "اگلا چیپٹر", "اگلا باب", "अगला चैप्टर",
    ])


def get_last_seerat_chapter() -> Optional[int]:
    hist = load_history()
    for item in reversed(hist):
        if item.get("seerat_chapter") is not None:
            return int(item.get("seerat_chapter"))
    return None


def parse_seerat_request(text: str) -> Tuple[bool, Optional[int], bool, bool]:
    """Returns (is_seerat, chapter_num, is_list_request, matched_by_topic)."""
    norm_text = normalize_input(text or "")
    low = norm_text.lower()

    if has_word(low, ["surah", "sura", "suratul", "ayat", "aayat", "ayah", "tafseer", "tafsir", "tarjuma", "سورہ", "سورة", "آیت", "تفسیر", "ترجمہ"]):
        return False, None, False, False

    if has_word(low, ["kisne likhi", "who wrote", "author", "کس نے لکھی"]):
        return False, None, False, False

    if is_seerat_follow_up(low):
        last_ch = get_last_seerat_chapter()
        if last_ch is not None:
            return True, min(20, last_ch + 1), False, False

    seerat_triggers = [
        "seerat", "sirat", "seerah", "sirah", "seerat un nabi", "seerat-un-nabi",
        "sirat un nabi", "seerat e mustafa", "seerat e tayyaba", "raheeq", "raheeq ul makhtoom",
        "ar raheeq", "al raheeq", "سیرت", "سیرت النبی", "سیرت مصطفی", "الرحیق المختوم", "सीरत", "सीरतुन नबी",
    ]
    has_seerat_word = has_word(low, seerat_triggers)
    has_chapter_word = has_word(low, ["chapter", "chapters", "topic", "bab", "baab", "episode", "mp3", "audio", "sunao", "چیپٹر", "باب", "قسط", "चैप्टर"])

    matched_topic_ch = None
    for ch_num, (_, _, kws) in SEERAT_CHAPTERS.items():
        if has_word(low, kws):
            matched_topic_ch = ch_num
            break

    if not has_seerat_word and not (matched_topic_ch is not None and has_chapter_word):
        return False, None, False, False

    if has_word(low, ["list", "topics", "kitne chapter", "kitne bab", "all chapters", "saare chapter", "فہرست", "کتنے چیپٹر", "کتنے باب"]):
        return True, None, True, False

    norm = replace_number_words(low)

    m_ch = re.search(r"(?<!\w)(?:chapter|topic|bab|baab|part|episode|number|no\.?|چیپٹر|باب|قسط|نمبر|चैप्टर)\s*(?:number|no\.?|نمبر)?\s*(\d{1,2})(?!\w)", norm)
    if m_ch:
        return True, max(1, min(20, int(m_ch.group(1)))), False, False

    m_ch_rev = re.search(r"(?<!\w)(\d{1,2})\s*(?:number|no\.?|نمبر)?\s*(?:chapter|topic|bab|baab|episode|چیپٹر|باب|قسط|चैप्टर)(?!\w)", norm)
    if m_ch_rev:
        return True, max(1, min(20, int(m_ch_rev.group(1)))), False, False

    if matched_topic_ch is not None:
        return True, matched_topic_ch, False, True

    nums = [int(x) for x in re.findall(r"\b(\d{1,2})\b", norm)]
    if nums:
        return True, max(1, min(20, nums[0])), False, False

    return True, 1, False, False


def has_explicit_quran_context(text: str) -> bool:
    low = (text or "").lower()
    if has_word(low, [
        "surah", "sura", "sury", "suraj", "surat", "suratul", "soorah", "soora",
        "quran", "quraan", "tilawat", "tilawah", "tilaat", "recite", "recitation",
        "tafseer", "tafsir", "tafser", "tafheem", "tafhim", "maududi", "moududi", "modudi",
        "tarjuma", "tarjama", "ayat", "aayat", "ayah", "verse", "verses",
        "ayatul kursi", "ayat ul kursi", "ayat-ul-kursi", "amanar rasul", "amanar rasool",
        "deebacha", "dibacha", "muqadma", "muqaddama",
        "سورہ", "سورۃ", "سورة", "سوره", "قرآن", "تلاوت", "تفسیر", "تفسير", "تفہیم", "مودودی",
        "ترجمہ", "ترجمة", "آیت", "آیات", "آية", "آيات", "ايت", "ایات",
        "آیت الکرسی", "آية الكرسي", "دیباچہ", "مقدمہ",
        "सूरह", "कुरान", "तिलावत", "तफसीर", "तफ़सीर", "तफ्सीर", "तर्जुमा", "तरजुमा", "आयत", "आयात",
    ]):
        return True
    return bool(re.search(r"\b(?:taf[sz]e*r|tap[sz]i*r|tafh[ie]+m|tarj[au]m|til[au]w?a?t)\w*", low))


def is_quran_info_question(text: str) -> bool:
    low = (text or "").lower()
    if has_word(low, ["sunao", "sunawo", "sunaiye", "play", "recite", "tilawat", "tilaat", "chala", "chalao", "lagao", "سناؤ", "سنائیں", "چلاؤ", "لگاؤ", "सुनाओ"]):
        return False
    if has_word(low, [
        "kitni", "kitne", "kitna", "kisne", "kis ne", "kis par", "kab nazil", "kahan nazil",
        "kaun si", "kaun sa", "who wrote", "how many", "when was", "total surah", "total ayat",
        "longest surah", "shortest surah", "badi surah", "choti surah", "makki hai ya madani",
        "کتنی", "کتنے", "کس نے", "کب نازل", "کہاں نازل", "کون سی", "कितनी", "कितने", "किसने",
    ]):
        return True
    return False


def is_follow_up_ayah_request(text: str) -> bool:
    return has_word(text, [
        "is se aagay", "is se age", "is se agay", "is ke aagay", "is ke age",
        "agli aayat", "agla aayat", "agli ayat", "next ayah", "next ayat", "next verse",
        "isi surah", "isi sura", "usi surah", "usi sura",
        "اس سے آگے", "اگلی آیت", "اگلی آیات", "اسی سورہ",
    ])


def is_translation_request(text: str) -> bool:
    return has_word(text, [
        "tarjuma", "tarjama", "translation", "translate", "urdu translation",
        "ترجمہ", "ترجمة", "اردو ترجمہ", "तर्जुमा", "तरजुमा",
    ])


def is_tafseer_only_request(text: str) -> bool:
    low = (text or "").lower()
    if has_word(low, [
        "tafseer", "tafsir", "tafsīr", "tafser", "tapsir", "tasfir", "tabseer",
        "tafheem", "tafhim", "maududi", "moududi", "modudi",
        "sharah", "nazool", "nuzool", "shaan", "shan", "matlab", "meaning", "explain", "samjhao",
        "تفسیر", "تفسير", "تفہیم", "مودودی", "شرح", "وضاحت", "نزول", "شان", "مطلب", "معنی",
        "तफसीर", "तफ़सीर", "तफ्सीर", "तफहीम", "व्याख्या", "मतलब",
    ]):
        return True
    return bool(re.search(r"\b(?:taf[sz]e*r|tap[sz]i*r|tab[sz]e*r|tafh[ie]+m|nuz[ou]+l)\w*", low))


def is_tafseer_intent(text: str) -> bool:
    if is_translation_request(text) or is_tafseer_only_request(text):
        return True
    return has_word(text, [
        "ayat", "aayat", "ayah", "verse", "verses", "ayatul kursi", "ayat ul kursi",
        "آیت", "آیات", "آية", "آيات", "ايت", "ایات", "آیت الکرسی", "آية الكرسي", "आयत", "आयात",
    ])


def detect_language(text: str) -> str:
    """STRICTLY LOCKED TO URDU OR ENGLISH ONLY — NEVER switches to Arabic or any third assistant."""
    low = (text or "").lower()
    if has_word(low, ["english mein", "in english", "speak english", "انگریزی میں"]):
        return "EN"
    if has_word(low, ["urdu mein", "in urdu", "hindi mein", "اردو میں"]):
        return "UR"

    # Any Devanagari or Arabic/Urdu script -> URDU assistant (never Arabic voice)
    if re.search(r"[\u0900-\u097F\u0600-\u06FF]", text):
        return "UR"

    urdu_markers = {
        "kya", "hai", "hain", "tha", "thi", "hota", "hoti", "hote", "hoga", "hogi",
        "batao", "bataiye", "sunao", "sunawo", "sunahu", "samjhao", "samjhaiye",
        "kaise", "kaisa", "kaisi", "aaj", "kal", "abhi", "kab", "kyun", "kyu", "kaun", "kahan",
        "kitna", "kitni", "kitne", "kisne", "kisko", "kise", "nahi", "nhi", "aur", "lekin",
        "mausam", "mosam", "khabar", "khabrein", "khabrai", "tarjuma", "tarjama",
        "pehli", "pehla", "pehlay", "pehle", "chalu", "dikhao", "ruko", "awaaz", "karo", "kero",
        "mera", "meri", "mere", "mujhe", "aap", "tum", "ki", "ka", "ke", "se", "mein", "tak",
        "aagay", "agay", "agli", "agla", "halaat", "nazool", "nuzool", "dheere", "ahista", "tez",
        "bolo", "bhai", "yaar", "bahut", "thoda", "kuch", "sab", "sahi", "galat",
        "wala", "wali", "wale", "hissa", "doosra", "dusra", "waqt", "tareekh", "tarikh",
        "tilawat", "tilaat", "suratul", "masla", "masayil", "masail", "seerat", "wuzu", "roza", "namaz",
        "tafseel", "tafsil", "mukhtasar", "ahmiyat", "ahamiyat", "tareeqa", "tarika",
    }
    tokens = re.findall(r"[\w]+", low)
    if any(tok in urdu_markers for tok in tokens):
        return "UR"

    return "EN"


def detect_quran_lang(text: str) -> str:
    if has_word(text, ["in english", "english mein", "english translation", "english tafseer", "انگریزی میں"]):
        return "EN"
    return "UR"


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
    if lang == "UR":
        ur_tts = (
            f"فجر {fmt_12_short(f)}۔ ظہر {fmt_12_short(z)}۔ "
            f"عصر {fmt_12_short(a)}۔ مغرب {fmt_12_short(mg)}۔ عشاء {fmt_12_short(e)}۔"
        )
        return disp, ur_tts
    return disp, disp


def replace_number_words(text: str) -> str:
    out = normalize_input(text).lower()
    for word, val in NUMBER_WORDS.items():
        out = re.sub(rf"(?<!\w){re.escape(word)}(?!\w)", str(val), out)
    out = re.sub(r"\bdo\s+(?=(?:se|say|to|ayat|aayat|ayah|chapter|bab)\b)", "2 ", out)
    out = re.sub(r"\b(ayat|aayat|ayah|se|say|to|chapter|bab|part|hissa)\s+do\b", r"\1 2", out)
    return out


def get_last_quran_context() -> Tuple[Optional[int], Optional[int]]:
    hist = load_history()
    for item in reversed(hist):
        if item.get("surah_num") is not None:
            return item.get("surah_num"), item.get("last_ayah")
    return None, None


def _norm_ar_match(s: str) -> str:
    s = re.sub(r"[\u064B-\u065F\u0670\u0640]", "", s or "")
    s = s.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا").replace("ة", "ہ").replace("ۃ", "ہ").replace("ي", "ی")
    s = re.sub(r"(?<!\w)ال(?!له\b)(?=[\u0600-\u06FF]{2,})", "", s)
    return s


def parse_quran_request(text: str) -> Tuple[Optional[int], Optional[int], Optional[int], bool]:
    norm_text = normalize_input(text or "")
    low = norm_text.lower()

    if has_word(low, ["ayatul kursi", "ayat ul kursi", "ayat-ul-kursi", "آیت الکرسی", "آية الكرسي"]):
        return 2, 255, 255, False
    if has_word(low, ["amanar rasul", "amanar rasool", "آمن الرسول"]):
        return 2, 285, 286, False
    if has_word(low, ["deebacha", "dibacha", "muqadma", "muqaddama", "tafheem introduction", "دیباچہ", "مقدمہ"]):
        return 0, None, None, False

    explicit_q = has_explicit_quran_context(low)
    follow_up = is_follow_up_ayah_request(low)

    m_colon = re.search(r"\b(\d{1,3})\s*:\s*(\d{1,3})(?:\s*-\s*(\d{1,3}))?\b", low)
    if m_colon and explicit_q:
        s_n = int(m_colon.group(1))
        a1 = int(m_colon.group(2))
        a2 = int(m_colon.group(3)) if m_colon.group(3) else a1
        if 1 <= s_n <= 114 and a1 >= 1:
            return s_n, a1, max(a1, min(a1 + 10, a2)), False

    if not explicit_q and not follow_up:
        return None, None, None, False

    rem = re.sub(
        r"\b(?:part|hissa|پارٹ|حصہ|पार्ट)\s*(?:1|2|one|two|ek|do)\b|\b(?:first|second|pehla|doosra|dusra)\s*(?:part|hissa)\b",
        " ",
        low,
    )

    surah_num = None
    m_surah = re.search(r"(?<!\w)(?:surah|sura|sury|suraj|surat|suratul|soorah|soora|سوره|سورہ|سورۃ|سورة|सूरह)\s*(?:number|num\.?|no\.?|نمبر|نंबर)?\s*(\d{1,3})(?!\w)", rem)
    if m_surah:
        surah_num = max(1, min(114, int(m_surah.group(1))))
        rem = rem[:m_surah.start()] + " " + rem[m_surah.end():]
    else:
        rem_norm = _norm_ar_match(rem)
        for name in sorted(SURAH_NAMES.keys(), key=len, reverse=True):
            n_clean = _norm_ar_match(name)
            m_nm = re.search(rf"(?<!\w){re.escape(n_clean)}(?!\w)", rem_norm)
            if m_nm:
                surah_num = SURAH_NAMES[name]
                rem = rem_norm[:m_nm.start()] + " " + rem_norm[m_nm.end():]
                break

    norm = replace_number_words(rem)
    last_s, last_ay = get_last_quran_context()

    ayah_words = r"(?:ayat|aayat|ayah|ayahs|verse|verses|آیت|آیات|آية|آيات|ايت|ایات|आयत|आयात)"
    has_explicit_ayah_word = bool(re.search(rf"(?<!\w){ayah_words}(?!\w)", low))
    if surah_num is None and (follow_up or has_explicit_ayah_word):
        if last_s is not None:
            surah_num = last_s

    if follow_up and surah_num is not None:
        base_ay = (last_ay or 1) + 1
        return surah_num, base_ay, base_ay + 2, False

    m_range = re.search(
        rf"(?:{ayah_words}\s*(?:number|num\.?|no\.?|نمبر|نंबर)?\s*)?(\d{{1,3}})\s*(?:se\s*(?:le\s*kar|lekar)?|say\s*(?:le\s*kar|lekar)?|to|-|تا|إلى|से\s*(?:लेकर)?)\s*(\d{{1,3}})(?!\w)",
        norm,
    )
    if m_range and (has_explicit_ayah_word or surah_num is not None):
        s_ay = max(1, int(m_range.group(1)))
        e_ay = max(s_ay, min(s_ay + 10, int(m_range.group(2))))
        return surah_num, s_ay, e_ay, False

    m_first = re.search(
        rf"(?<!\w)(?:first|pehli|pehlay|pehle|shuru\s*ki|ابتدائی|پہلی|أول|पहली)\s*(\d{{1,2}})\s*{ayah_words}(?!\w)",
        norm,
    )
    if m_first:
        n = max(1, min(10, int(m_first.group(1))))
        return surah_num, 1, n, False

    m_last = re.search(
        rf"(?<!\w)(?:last|aakhri|akhri|aakhir\s*ki|آخری|آخر|आखिरी)\s*(\d{{1,2}})\s*{ayah_words}(?!\w)",
        norm,
    )
    if m_last:
        n = max(1, min(10, int(m_last.group(1))))
        return surah_num, n, n, True

    m_single = re.search(
        rf"(?<!\w){ayah_words}\s*(?:number|num\.?|no\.?|#|نمبر|نंबर)?\s*(\d{{1,3}})(?:st|nd|rd|th|vi|wi|ween|ویں)?(?!\w)",
        norm,
    )
    if m_single:
        a = max(1, int(m_single.group(1)))
        return surah_num, a, a, False

    m_single_rev = re.search(
        rf"(?<!\w)(\d{{1,3}})(?:st|nd|rd|th|vi|wi|ween|ویں)?\s*(?:number|num\.?|no\.?|نمبر|نंबर)?\s*{ayah_words}(?!\w)",
        norm,
    )
    if m_single_rev:
        a = max(1, int(m_single_rev.group(1)))
        return surah_num, a, a, False

    if surah_num is not None and has_explicit_ayah_word:
        nums = [int(x) for x in re.findall(r"\b(\d{1,3})\b", norm)]
        if nums:
            return surah_num, max(1, nums[0]), max(1, nums[0]), False

    if surah_num is None and has_word(low, ["surah", "sura", "suratul", "سورہ", "سورۃ", "سورة", "सूरह"]):
        nums = [int(x) for x in re.findall(r"\b(\d{1,3})\b", norm)]
        if nums:
            surah_num = max(1, min(114, nums[0]))

    return surah_num, None, None, False


def _encode_url_path(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    encoded_path = urllib.parse.quote(urllib.parse.unquote(parts.path), safe="/:@-._~!$&'()*+,;=")
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, encoded_path, parts.query, parts.fragment))


async def _resolve_final_mp3_url(url: str) -> str:
    now_t = time.time()
    cached = RESOLVED_URL_CACHE.get(url)
    if cached and (now_t - cached[0]) < 1800:
        return cached[1]
    headers = {"User-Agent": "Mozilla/5.0 (compatible; ESP32Assistant/2.0)"}
    try:
        async with httpx.AsyncClient(timeout=5.0, follow_redirects=True, headers=headers) as client:
            r = await client.head(url)
            if r.status_code in (200, 206, 301, 302):
                final_u = str(r.url)
                RESOLVED_URL_CACHE[url] = (now_t, final_u)
                return final_u
    except Exception as ex:
        print(f"Redirect resolve warning: {ex}")
    return url


def _extract_surah_num_from_name(name: str) -> Optional[int]:
    base = os.path.basename(urllib.parse.unquote(name or ""))
    m_lead = re.match(r"^0*(\d{1,3})(?!\d)", base)
    if m_lead:
        n = int(m_lead.group(1))
        if 0 <= n <= 114:
            return n
    m_tag = re.search(r"(?:surah|sura|tafheem)[_\-\s]*0*(\d{1,3})(?!\d)", base, flags=re.I)
    if m_tag:
        n = int(m_tag.group(1))
        if 0 <= n <= 114:
            return n
    return None


async def _load_archive_surah_map(item_id: str) -> Dict[int, str]:
    if item_id in ARCHIVE_MAP_CACHE and ARCHIVE_MAP_CACHE[item_id]:
        return ARCHIVE_MAP_CACHE[item_id]

    smap: Dict[int, str] = {}
    headers = {"User-Agent": "Mozilla/5.0 (compatible; ESP32Assistant/2.0)"}
    try:
        meta_url = f"https://archive.org/metadata/{item_id}"
        async with httpx.AsyncClient(timeout=14.0, follow_redirects=True, headers=headers) as client:
            r = await client.get(meta_url)
            if r.status_code != 200:
                print(f"Archive metadata failed ({item_id}): {r.status_code}")
                return smap
            files = r.json().get("files", [])

            mp3_entries: List[Tuple[int, str]] = []
            m3u_names: List[str] = []

            for f in files:
                fname = str(f.get("name") or "")
                fmt = str(f.get("format") or "").lower()
                low_fn = fname.lower()
                if low_fn.endswith(".mp3"):
                    if "vbr mp3" in fmt:
                        prio = 1
                    elif "64kbps" in fmt or low_fn.endswith("_64kb.mp3"):
                        prio = 3
                    else:
                        prio = 2
                    mp3_entries.append((prio, fname))
                elif low_fn.endswith(".m3u"):
                    m3u_names.append(fname)

            mp3_entries.sort(key=lambda x: x[0])
            for _, fname in mp3_entries:
                n = _extract_surah_num_from_name(fname)
                if n is not None and 0 <= n <= 114 and n not in smap:
                    smap[n] = f"https://archive.org/download/{item_id}/{urllib.parse.quote(fname, safe='/')}"

            if len(smap) < 50:
                for m3u_name in m3u_names:
                    m3u_url = f"https://archive.org/download/{item_id}/{urllib.parse.quote(m3u_name, safe='/')}"
                    mr = await client.get(m3u_url)
                    if mr.status_code == 200:
                        for ln in mr.text.splitlines():
                            ln = ln.strip()
                            if ln.lower().startswith("http") and ".mp3" in ln.lower():
                                n = _extract_surah_num_from_name(urllib.parse.urlsplit(ln).path)
                                if n is not None and 0 <= n <= 114 and n not in smap:
                                    smap[n] = _encode_url_path(ln)
    except Exception as ex:
        print(f"Archive map error ({item_id}): {ex}")

    if smap:
        ARCHIVE_MAP_CACHE[item_id] = smap
        print(f"Loaded {len(smap)} Surahs from Archive [{item_id}]")
    return smap


async def _load_quranurdu_mishari_map() -> Dict[int, Dict[int, str]]:
    if QURANURDU_MAP_CACHE:
        return QURANURDU_MAP_CACHE
    headers = {"User-Agent": "Mozilla/5.0 (compatible; ESP32Assistant/2.0)"}
    try:
        async with httpx.AsyncClient(timeout=12.0, follow_redirects=True, headers=headers) as client:
            r = await client.get(QURANURDU_MISHARI_PAGE)
            if r.status_code == 200:
                hrefs = re.findall(r'href=["\']([^"\']+\.mp3)["\']', r.text, flags=re.I)
                grouped: Dict[int, List[str]] = {}
                for h in hrefs:
                    n = _extract_surah_num_from_name(h)
                    if n is not None and 1 <= n <= 114:
                        grouped.setdefault(n, []).append(h)
                for n, lst in grouped.items():
                    parts_map: Dict[int, str] = {}
                    for h in lst:
                        dec = urllib.parse.unquote(h)
                        full_u = _encode_url_path(urllib.parse.urljoin(QURANURDU_MISHARI_PAGE, h))
                        if "(2)" in dec:
                            parts_map[2] = full_u
                        elif "(1)" in dec or 1 not in parts_map:
                            parts_map[1] = full_u
                    if parts_map:
                        QURANURDU_MAP_CACHE[n] = parts_map
                print(f"Loaded {len(QURANURDU_MAP_CACHE)} Surahs from QuranUrdu Mishary+Maududi")
    except Exception as ex:
        print(f"QuranUrdu map error: {ex}")
    return QURANURDU_MAP_CACHE


def _natural_sort_key(fname: str) -> Tuple[int, str]:
    base = os.path.basename(fname or "")
    nums = re.findall(r"\d+", base)
    return (int(nums[0]) if nums else 9999, base.lower())


async def _load_seerat_archive_item(client: httpx.AsyncClient, item_id: str) -> Dict[int, str]:
    cmap: Dict[int, str] = {}
    try:
        r = await client.get(f"https://archive.org/metadata/{item_id}")
        if r.status_code != 200:
            return cmap
        files = r.json().get("files", [])
        seen_stems = set()
        mp3s: List[str] = []
        for f in files:
            fname = str(f.get("name") or "")
            low_fn = fname.lower()
            if not low_fn.endswith(".mp3") or low_fn.endswith("_64kb.mp3"):
                continue
            stem = re.sub(r"\.mp3$", "", os.path.basename(low_fn))
            if stem in seen_stems:
                continue
            seen_stems.add(stem)
            mp3s.append(fname)
        if len(mp3s) >= 5:
            mp3s.sort(key=_natural_sort_key)
            for idx, fname in enumerate(mp3s, 1):
                cmap[idx] = f"https://archive.org/download/{item_id}/{urllib.parse.quote(fname, safe='/')}"
    except Exception as ex:
        print(f"Seerat archive item error ({item_id}): {ex}")
    return cmap


async def fetch_custom_seerat_archive_mp3(chapter_num: int) -> Optional[str]:
    """Only runs if user explicitly pins SEERAT_ARCHIVE_ID in Render Environment (no random unknown books)."""
    global SEERAT_CACHE_TS
    if not CUSTOM_SEERAT_ARCHIVE_ID:
        return None
    now_t = time.time()
    if not SEERAT_MP3_CACHE and (now_t - SEERAT_CACHE_TS) > 300:
        SEERAT_CACHE_TS = now_t
        headers = {"User-Agent": "Mozilla/5.0 (compatible; ESP32Assistant/2.0)"}
        try:
            async with httpx.AsyncClient(timeout=12.0, follow_redirects=True, headers=headers) as client:
                cmap = await _load_seerat_archive_item(client, CUSTOM_SEERAT_ARCHIVE_ID)
                if cmap:
                    SEERAT_MP3_CACHE.update(cmap)
                    print(f"Loaded {len(SEERAT_MP3_CACHE)} Seerat MP3 chapters from pinned [{CUSTOM_SEERAT_ARCHIVE_ID}]")
        except Exception as ex:
            print(f"Custom Seerat archive error: {ex}")
    if chapter_num in SEERAT_MP3_CACHE:
        return await _resolve_final_mp3_url(SEERAT_MP3_CACHE[chapter_num])
    return None


async def fetch_tafheem_tafseer_mp3(surah_num: int) -> Optional[str]:
    for item_id in (ARCHIVE_TAFHEEM_FULL_ID, ARCHIVE_TAFHEEM_SHORT_ID):
        smap = await _load_archive_surah_map(item_id)
        if surah_num in smap:
            final_u = await _resolve_final_mp3_url(smap[surah_num])
            print(f"Tafheem Tafseer MP3 ({surah_num}): {final_u}")
            return final_u
    print(f"Tafheem MP3 not found for Surah {surah_num}")
    return None


async def fetch_tafheem_urdu_translation_mp3(surah_num: int, part: int = 1) -> Optional[str]:
    qmap = await _load_quranurdu_mishari_map()
    if surah_num in qmap:
        url = qmap[surah_num].get(part) or qmap[surah_num].get(1)
        if url:
            print(f"Maududi Translation MP3 (QuranUrdu {surah_num} P{part}): {url}")
            return url
    return await fetch_tafheem_tafseer_mp3(surah_num)


async def fetch_maududi_verses(
    surah_num: int, start_ay: Optional[int], end_ay: Optional[int], is_last: bool
) -> Tuple[str, str, int, List[Tuple[int, str, str, str]], Optional[int]]:
    urls = [
        f"https://api.alquran.cloud/v1/surah/{surah_num}/editions/quran-uthmani,ur.maududi,en.maududi",
        f"https://api.alquran.cloud/v1/surah/{surah_num}/editions/quran-uthmani,ur.maududi,en.sahih",
    ]
    async with httpx.AsyncClient(timeout=12.0) as client:
        for url in urls:
            try:
                r = await client.get(url)
                if r.status_code != 200:
                    continue
                editions = r.json().get("data", [])
                if len(editions) < 3:
                    continue
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
                    e_idx = min(total, 4)

                out_ayahs: List[Tuple[int, str, str, str]] = []
                for idx in range(s_idx, e_idx):
                    anum = idx + 1
                    ar_t = (ar_list[idx].get("text", "") if idx < len(ar_list) else "").strip()
                    ur_t = (ur_list[idx].get("text", "") if idx < len(ur_list) else "").strip()
                    en_t = (en_list[idx].get("text", "") if idx < len(en_list) else "").strip()
                    out_ayahs.append((anum, ar_t, ur_t, en_t))

                actual_last_ayah = out_ayahs[-1][0] if out_ayahs else None
                return s_name_en, s_name_ar, total, out_ayahs, actual_last_ayah
            except Exception as ex:
                print(f"Quran API error ({url}): {ex}")
    return "", "", 0, [], None


def _rank_groq_model(mid: str) -> int:
    m = mid.lower()
    if "70b" in m and "versatile" in m:
        return 1
    if "llama-4" in m and "maverick" in m:
        return 2
    if "llama-4" in m and "scout" in m:
        return 3
    if "llama-3.1-8b" in m:
        return 4
    if "120b" in m:
        return 5
    if "qwen" in m and ("32b" in m or "27b" in m):
        return 6
    if "20b" in m or "32b" in m:
        return 7
    if "llama" in m:
        return 8
    return 15


async def get_available_groq_models(key: str) -> List[str]:
    now_t = time.time()
    cached = MODEL_CACHE.get(key)
    if cached and (now_t - cached[0]) < 600:
        return cached[1]

    skip_tokens = (
        "whisper", "tts", "orpheus", "playai", "guard", "safeguard",
        "moderation", "embedding", "speech", "audio", "vision", "compound",
    )
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
                    MODEL_CACHE[key] = (now_t, chat_models[:6])
                    return chat_models[:6]
    except Exception as ex:
        print(f"Groq /models error: {ex}")

    return FALLBACK_GROQ_MODELS


async def call_groq_pure_urdu_text(system_prompt: str, user_prompt: str, max_tokens: int = 1400) -> str:
    if not RAW_KEYS:
        return ""

    for key in RAW_KEYS:
        models = await get_available_groq_models(key)
        for model in models:
            try:
                async with httpx.AsyncClient(timeout=26.0) as client:
                    resp = await client.post(
                        "https://api.groq.com/openai/v1/chat/completions",
                        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                        json={
                            "model": model,
                            "messages": [
                                {"role": "system", "content": system_prompt},
                                {"role": "user", "content": user_prompt},
                            ],
                            "max_tokens": max_tokens,
                            "temperature": 0.15,
                        },
                    )
                    update_groq_limits(resp, model)
                    if resp.status_code == 200:
                        raw = resp.json()["choices"][0]["message"]["content"].strip()
                        raw = strip_think_tags(raw)
                        pure = re.sub(r"[A-Za-z]+", "", raw)
                        pure = clean_urdu_for_tts(pure)
                        urdu_char_count = len(re.findall(r"[\u0600-\u06FF]", pure))
                        if urdu_char_count >= 25:
                            print(f"Pure Urdu OK [{model}] chars={urdu_char_count}")
                            return pure
                        print(f"Pure Urdu too short [{model}] chars={urdu_char_count}")
                    else:
                        print(f"Pure Urdu Groq {model} ({resp.status_code}): {resp.text[:140]}")
            except Exception as ex:
                print(f"Pure Urdu Groq {model} exception: {ex}")
    return ""


async def build_seerat_chapter_narration_mp3(chapter_num: int, lang: str) -> Tuple[str, str, int, str]:
    """Strictly ONE Book: Ar-Raheeq Al-Makhtum (Maulana Safi-ur-Rahman Mubarakpuri), topic-wise cached MP3."""
    en_title, ur_title, _ = SEERAT_CHAPTERS.get(chapter_num, SEERAT_CHAPTERS[1])
    cache_fid = f"seerat_ch{chapter_num:02d}_{lang.lower()}"
    cache_path = os.path.join(AUDIO_DIR, f"{cache_fid}.mp3")

    if os.path.exists(cache_path) and os.path.getsize(cache_path) > 2000:
        est_ms = int((os.path.getsize(cache_path) / 6200.0) * 1000)
        return f"SEERAT CH{chapter_num:02d}", f"/audio/{cache_fid}.mp3", est_ms, f"Ar-Raheeq Al-Makhtum Ch {chapter_num}: {en_title}"

    if lang == "EN":
        sys_p = (
            "You are narrating an authentic topic-wise audiobook chapter strictly from ONE book: "
            "Ar-Raheeq Al-Makhtum (The Sealed Nectar) by Safi-ur-Rahman al-Mubarakpuri. "
            "Write 12 to 15 clear, flowing, easy-to-understand English sentences covering the exact authentic historical events and lessons of this topic. "
            "No headings, no bullet points, no markdown, no extra filler."
        )
        usr_p = f"Ar-Raheeq Al-Makhtum, Topic {chapter_num}: {en_title}."
        res = await call_groq_bilingual(sys_p, usr_p, "EN", "long")
        body = res[1] if res else f"Topic {chapter_num} from Ar-Raheeq Al-Makhtum covers {en_title}."
        script = f"Ar-Raheeq Al-Makhtum. Chapter {chapter_num}, {en_title}. {body}"
        url, total_ms = await make_tts_with_duration(script, "EN-M", fixed_fid=cache_fid)
        return f"SEERAT CH{chapter_num:02d}", url, total_ms, script

    sys_p = (
        "آپ صرف ایک مستند کتاب الرحیق المختوم از مولانا صفی الرحمن مبارکپوری کے مطابق سیرت النبی صلی اللہ علیہ وسلم کا موضوع وار آڈیو باب بیان کر رہے ہیں۔\n"
        "سخت اصول:\n"
        "1) پورا بیان صرف خالص، سلیس، بامحاورہ اردو رسم الخط میں ہو۔ ایک بھی انگریزی لفظ، ہندی لفظ، ہیڈنگ، نمبر یا بلٹ پوائنٹ نہ لکھیں۔\n"
        "2) صرف الرحیق المختوم کے مطابق اس موضوع کے مستند تاریخی واقعات، پسِ منظر اور عملی سبق 12 سے 15 صاف جملوں میں بیان کریں۔\n"
        "3) کوئی غیر مستند قصہ یا فالتو تمہید شامل نہ کریں۔ عربی اعراب نہ لگائیں اور اللہ کو اردو ہ کے ساتھ لکھیں۔"
    )
    usr_p = f"الرحیق المختوم، باب نمبر {chapter_num}، موضوع: {ur_title}۔ اس باب کو مکمل اور مستند انداز میں بیان کریں۔"
    narration = await call_groq_pure_urdu_text(sys_p, usr_p, max_tokens=1700)
    if not narration:
        narration = f"یہ کتاب الرحیق المختوم کا باب نمبر {chapter_num} ہے جس کا موضوع {ur_title} ہے۔"

    script = f"الرحیق المختوم، سیرت النبی صلی اللہ علیہ وسلم۔ باب نمبر {chapter_num}، {ur_title}۔ {narration}"
    url, total_ms = await make_tts_with_duration(script, "UR-M", fixed_fid=cache_fid)
    return f"SEERAT CH{chapter_num:02d}", url, total_ms, script


async def build_exact_tafheem_ayah_audio(
    surah_num: int,
    start_ay: Optional[int],
    end_ay: Optional[int],
    is_last: bool,
    lang: str,
) -> Tuple[str, str, int, str, List[Dict], Optional[int]]:
    s_name_en, s_name_ar, total_ay, ayahs, actual_last_ayah = await fetch_maududi_verses(
        surah_num, start_ay, end_ay, is_last
    )
    if not ayahs:
        txt = f"سورہ نمبر {surah_num} کی آیات ابھی لوڈ نہیں ہو سکیں۔" if lang == "UR" else f"Could not load Surah {surah_num} right now."
        disp_err = f"Surah {surah_num} load failed."
        url, total_ms = await make_tts_with_duration(txt, voice_code_for_lang(lang))
        return f"SURAH {surah_num}", url, total_ms, txt, build_synced_pages(disp_err, total_ms, lang), None

    rev_type = "Madani" if surah_num in MADANI_SURAHS else "Makki"
    first_a = ayahs[0][0]
    last_a = ayahs[-1][0]
    surah_ur = clean_surah_name_urdu(s_name_ar, surah_num)
    rev_ur = "مدنی" if rev_type == "Madani" else "مکی"
    notes_map = load_tafheem_notes()

    hdr = f"AYAH {surah_num}:{first_a}" if first_a == last_a else f"AYAH {surah_num}:{first_a}-{last_a}"

    if lang == "UR":
        if last_a > first_a:
            intro = f"{surah_ur}، {rev_ur}۔ مولانا مودودی کا ترجمہ اور تفسیر، آیت {first_a} سے {last_a} تک۔"
        else:
            intro = f"{surah_ur}، {rev_ur}۔ مولانا مودودی کا ترجمہ اور تفسیر، آیت {first_a}۔"

        trans_voice_parts = [intro]
        disp_trans_parts = [f"{s_name_en} ({surah_num}:{first_a})." if first_a == last_a else f"{s_name_en} ({surah_num}:{first_a}-{last_a})."]
        cleaned_verses: List[Tuple[int, str]] = []
        verbatim_notes: List[str] = []

        for anum, _, ur_t, en_t in ayahs:
            clean_ur = clean_urdu_for_tts(ur_t)
            cleaned_verses.append((anum, clean_ur))
            trans_voice_parts.append(f"آیت {anum}۔ {clean_ur}۔")
            clean_en = clean_ascii(en_t)
            if clean_en:
                disp_trans_parts.append(f"Ayah {anum}: {clean_en}")
            note = notes_map.get(f"{surah_num}:{anum}", "").strip()
            if note:
                verbatim_notes.append(clean_urdu_for_tts(note))

        tafseer_voice = ""
        if verbatim_notes:
            tafseer_voice = "تفسیر۔ " + " ".join(verbatim_notes)
        else:
            verse_block = "\n".join(f"آیت {anum}: {ut}" for anum, ut in cleaned_verses)
            sys_p = (
                "آپ ایک مستند اردو عالمِ دین ہیں۔ دی گئی آیت یا آیات کی تفسیر صرف مولانا سید ابوالاعلیٰ مودودی کی تفہیم القرآن کے منہج اور نکات کے مطابق خالص اردو میں بیان کریں۔\n"
                "سخت اصول:\n"
                "1) پورا جواب صرف خالص اردو رسم الخط (اردو) میں ہو۔ ایک بھی انگریزی لفظ، ہندی لفظ، رومن اردو، ہیڈنگ، نمبر یا بلٹ پوائنٹ نہ لکھیں۔\n"
                "2) اردو گرامر بالکل فطری، سلیس اور بامحاورہ ہو، ٹوٹے ہوئے یا ترجمہ زدہ جملے ہرگز نہ ہوں۔\n"
                "3) آیت کا لفظی ترجمہ دوبارہ نہ دہرائیں۔ سیدھا پسِ منظر، شانِ نزول، اصل پیغام اور مولانا مودودی کے اہم تفسیری نکات 5 سے 7 واضح جملوں میں بیان کریں۔\n"
                "4) عربی اعراب نہ لگائیں، کولن والا حوالہ جیسے 2:43 نہ لکھیں، اور اللہ کو اردو ہ کے ساتھ لکھیں۔"
            )
            usr_p = (
                f"{surah_ur} ({rev_ur})، آیت {first_a} سے {last_a} تک مولانا مودودی کا ترجمہ:\n"
                f"{verse_block}\n\n"
                "اب ان آیات کی خالص اردو میں تفہیم القرآن کے مطابق مستند تفسیر بیان کریں۔"
            )
            tafseer_part = await call_groq_pure_urdu_text(sys_p, usr_p, max_tokens=1400)
            if tafseer_part:
                tafseer_voice = f"تفسیر۔ {tafseer_part}"

        trans_voice = " ".join(trans_voice_parts)
        script = f"{trans_voice} {tafseer_voice}".strip()
        url, total_ms = await make_tts_with_duration(script, "UR-M")

        disp_trans_text = " ".join(disp_trans_parts)
        if tafseer_voice:
            trans_ratio = len(trans_voice) / max(1, len(script))
            trans_ms = max(1500, int(round(total_ms * trans_ratio)))
            tafseer_ms = max(1500, total_ms - trans_ms)
            pages = build_synced_pages(disp_trans_text, trans_ms, "UR")
            pages.append({"t": "Tafseer (Tafheem-ul-Quran): Playing in Urdu...", "ms": tafseer_ms})
        else:
            pages = build_synced_pages(disp_trans_text, total_ms, "UR")

        return hdr, url, total_ms, script, pages, actual_last_ayah

    parts = [f"{s_name_en}, Surah {surah_num}, {rev_type}. Translation and Tafseer based on Syed Abul Ala Maududi's Tafheem-ul-Quran."]
    for anum, _, _, en_t in ayahs:
        parts.append(f"Verse {anum}: {clean_ascii(en_t)}")
    sys_p = (
        "You are a clear Islamic scholar. Explain these verses strictly according to Syed Abul Ala Maududi's Tafheem-ul-Quran "
        "in 4 to 5 simple, easy-to-understand English sentences without repeating the translation."
    )
    usr_p = f"Surah {surah_num} ({s_name_en}), Verses {first_a} to {last_a}."
    res = await call_groq_bilingual(sys_p, usr_p, "EN", "normal")
    if res and res[1]:
        parts.append(f"Tafseer: {res[1]}")
    script = " ".join(parts)
    url, total_ms = await make_tts_with_duration(script, "EN-M")
    return hdr, url, total_ms, script, build_synced_pages(script, total_ms, "EN"), actual_last_ayah


def voice_code_for_lang(lang: str) -> str:
    return "EN-M" if lang == "EN" else "UR-M"


async def fetch_rss_items(urls: List[str], limit: int = 5) -> List[str]:
    items: List[str] = []
    headers = {"User-Agent": "Mozilla/5.0 (compatible; ESP32Assistant/2.0)"}
    async with httpx.AsyncClient(timeout=7.0, follow_redirects=True, headers=headers) as client:
        for u in urls:
            try:
                r = await client.get(u)
                if r.status_code != 200:
                    continue
                root = ET.fromstring(r.content)
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
    cand = ""

    m_ur = re.search(
        r"\b([a-zA-Z]{3,20}(?:\s+[a-zA-Z]{3,20})?)\s+(?:ka|ki|ke|mein|me)\s+(?:mausam|mosam|weather|temperature|barish|baarish|thand|sardi|garmi)\b",
        query,
        flags=re.I,
    )
    if m_ur:
        cand = m_ur.group(1).strip()
    else:
        m_en = re.search(r"\b(?:in|at|of|for)\s+([a-zA-Z]{3,20}(?:\s+[a-zA-Z]{3,20})?)\b", query, flags=re.I)
        if m_en:
            cand = m_en.group(1).strip()
        elif has_word(query, ["shopian", "شوپیاں", "شاپیاں"]):
            cand = "Shopian"
        elif has_word(query, ["pulwama", "پلوامہ"]):
            cand = "Pulwama"

    if cand:
        cand = " ".join(w for w in cand.split() if w.lower() not in WEATHER_STOPWORDS).strip()

    if cand:
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


async def call_groq_bilingual(system_prompt: str, user_prompt: str, lang: str, length_mode: str = "normal") -> Optional[Tuple[str, str]]:
    if not RAW_KEYS:
        return None

    history_ctx = format_history_for_prompt()

    if length_mode == "brief":
        length_rule = "LENGTH MODE: BRIEF. Give a short, crisp, direct answer in 1 to 2 sentences."
        max_tokens = 320
    elif length_mode == "long":
        length_rule = "LENGTH MODE: DETAILED. Give a thorough, clear, well-structured answer in 6 to 9 natural sentences."
        max_tokens = 1500
    else:
        length_rule = "LENGTH MODE: NORMAL. Give a direct, clear, accurate answer in 3 to 5 natural sentences."
        max_tokens = 900

    if lang == "UR":
        format_rule = (
            "STRICT LANGUAGE LOCK: User spoke in URDU.\n"
            "Write natural, pure, everyday spoken Urdu — NEVER use Hindi words like 'turant', 'taapman', 'lagbhag'.\n"
            "NEVER write colon verse references like '2:43' or English acronyms like 'IST' in VOICE (write 'سورہ بقرہ آیت نمبر 43' instead).\n"
            "Reply using EXACTLY these two plain lines with NO markdown stars, NO intro filler, and NO extra text:\n"
            "DISPLAY: <Clean, accurate answer in Roman Urdu using A-Z and 0-9 only; NO Urdu script, NO DISPLAY/VOICE words>\n"
            "VOICE: <Exact same clean, accurate answer in natural Urdu script (اردو) with proper punctuation (۔); NO Arabic harakat, NO DISPLAY/VOICE words>\n"
            f"{length_rule}\n"
            "Always resolve follow-ups using Conversation History."
        )
    else:
        format_rule = (
            "STRICT LANGUAGE LOCK: User spoke in ENGLISH.\n"
            "Use SIMPLE, CLEAR, everyday Indian English that is very easy to understand when spoken aloud. "
            "Use clean, natural grammar. Avoid complex jargon, heavy vocabulary, markdown symbols, or filler.\n"
            f"{length_rule}\n"
            "Answer directly and accurately, using Conversation History for follow-ups."
        )

    full_sys = system_prompt + "\n\n" + history_ctx + format_rule

    for key in RAW_KEYS:
        models = await get_available_groq_models(key)
        for model in models:
            try:
                async with httpx.AsyncClient(timeout=24.0) as client:
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
                            "temperature": 0.15,
                        },
                    )
                    update_groq_limits(resp, model)
                    if resp.status_code == 200:
                        raw = resp.json()["choices"][0]["message"]["content"].strip()
                        raw = strip_think_tags(raw)
                        raw_clean_md = raw.replace("**", "").replace("__", "")
                        print(f"Groq OK [{model}] lang={lang} mode={length_mode}")

                        if lang == "UR":
                            m_disp = re.search(r"DISPLAY\s*:\s*(.+?)(?=\n\s*VOICE\s*:|\Z)", raw_clean_md, flags=re.S | re.I)
                            m_voice = re.search(r"VOICE\s*:\s*(.+)", raw_clean_md, flags=re.S | re.I)
                            if m_disp and m_voice:
                                disp = clean_ascii(m_disp.group(1))
                                voice = clean_urdu_for_tts(clean_voice_script(m_voice.group(1)))
                                if voice:
                                    if len(disp) < 4:
                                        disp = urdu_to_roman_fallback(voice)
                                    return disp, voice
                            voice_only = clean_urdu_for_tts(clean_voice_script(raw_clean_md))
                            if voice_only:
                                disp_fb = clean_ascii(voice_only)
                                if len(disp_fb) < 4:
                                    disp_fb = urdu_to_roman_fallback(voice_only)
                                return disp_fb, voice_only

                        en_clean = clean_ascii(raw_clean_md)
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
    hint_prompt = (
        "Suratul Kahf tilawat sunao, Surah Yaseen, Surah Baqarah, Surah Fatiha, Surah Rahman, Surah Mulk, "
        "Surah Ikhlas, Surah 45 ayat 20 tarjuma aur tafseer, Tafheem-ul-Quran, Maulana Maududi, "
        "Seerat un Nabi Ar-Raheeq Al-Makhtum chapter 1 sunao, Ghazwa e Badr, Hijrat e Madina, "
        "Islamic masayil, Sajda e Sahw, Wuzu, Namaz, Roza, Zakat, Shopian ka mausam, News, Cricket, Python."
    )
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
                    low_t = txt.lower().strip(" .!?,")
                    if low_t in WHISPER_HALLUCINATIONS or len(low_t) < 2:
                        return ""
                    return txt
                print(f"Whisper ({resp.status_code}): {resp.text[:160]}")
        except Exception as e:
            print(f"Whisper error: {e}")
    return ""


async def _run_edge_tts_once(clean_text: str, voice: str, rate: str) -> Tuple[bytes, int]:
    audio_chunks = bytearray()
    last_end_ms = 0
    comm = edge_tts.Communicate(clean_text, voice=voice, rate=rate)
    async for chunk in comm.stream():
        ctype = chunk.get("type")
        if ctype == "audio":
            audio_chunks.extend(chunk["data"])
        elif ctype in ("WordBoundary", "SentenceBoundary"):
            end_ms = int((chunk.get("offset", 0) + chunk.get("duration", 0)) / 10000)
            if end_ms > last_end_ms:
                last_end_ms = end_ms
    return bytes(audio_chunks), last_end_ms


async def make_tts_with_duration(
    text: str,
    voice_code: str,
    keep_clock_colons: bool = False,
    fixed_fid: Optional[str] = None,
) -> Tuple[str, int]:
    """STRICTLY LOCKED to ONLY ur-PK-AsadNeural (UR-M) and en-IN-PrabhatNeural (EN-M)."""
    fid = fixed_fid or uuid.uuid4().hex[:10]
    out_path = os.path.join(AUDIO_DIR, f"{fid}.mp3")
    locked_voice = VOICE_CANDIDATES["EN-M"][0] if voice_code == "EN-M" else VOICE_CANDIDATES["UR-M"][0]

    if STATE["speed"] == "Norm":
        rate = "-6%" if voice_code == "EN-M" else "-4%"
    else:
        rate = SPEED_MAP.get(STATE["speed"], "+0%")

    if voice_code == "EN-M":
        clean_text = clean_english_for_tts(text) or "Please ask again."
    else:
        clean_text = clean_urdu_for_tts(clean_voice_script(text), keep_clock_colons=keep_clock_colons) or "جی بتائیے۔"

    audio_bytes = b""
    last_end_ms = 0

    for attempt in range(1, 5):
        try:
            audio_bytes, last_end_ms = await _run_edge_tts_once(clean_text, locked_voice, rate)
            if len(audio_bytes) >= 800:
                break
        except Exception as ex:
            print(f"Locked TTS attempt {attempt} ({locked_voice}) error: {ex}")
        await asyncio.sleep(0.35)

    if len(audio_bytes) < 800:
        print(f"Locked TTS failed on all retries ({locked_voice}); returning text-only without switching voice")
        return "", max(2200, len(clean_text) * 55)

    with open(out_path, "wb") as f:
        f.write(audio_bytes)

    est_ms = int((len(audio_bytes) / 6200.0) * 1000)
    total_ms = last_end_ms if last_end_ms > 0 else max(1100, est_ms)
    print(f"TTS [locked] voice={locked_voice} rate={rate} bytes={len(audio_bytes)} ms={total_ms}")

    now_t = time.time()
    for fn in os.listdir(AUDIO_DIR):
        if fn.startswith("seerat_"):
            continue
        fp = os.path.join(AUDIO_DIR, fn)
        try:
            if os.path.isfile(fp) and now_t - os.path.getmtime(fp) > AUDIO_TTL_SEC:
                os.remove(fp)
        except Exception:
            pass

    return f"/audio/{fid}.mp3", total_ms


def build_synced_pages(display_text: str, total_ms: int, lang: str) -> List[Dict]:
    max_chars = 76
    txt = (display_text or "").strip()
    if not txt:
        return [{"t": "...", "ms": max(1000, total_ms)}]

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

    chunks = chunks[:27]
    if not chunks:
        chunks = [txt[:max_chars]]

    weights = [max(4, len(c)) for c in chunks]
    w_sum = sum(weights)
    pages: List[Dict] = []
    for c, w in zip(chunks, weights):
        ms = max(650, int(round(total_ms * (w / w_sum))))
        pages.append({"t": c, "ms": ms})
    return pages


def _public_base_url(request: Request) -> str:
    base = str(request.base_url).rstrip("/")
    if "onrender.com" in base and base.startswith("http://"):
        base = "https://" + base[len("http://"):]
    return base or "https://esp32-ai-voice.onrender.com"


async def generate_answer(heard: str, lang: str) -> Tuple[str, str, str, bool]:
    """Returns (header, display_text, tts_text, keep_clock_colons)."""
    now = get_ist_now()
    hd, hm_en, hm_ur, hy = gregorian_to_hijri(now.year, now.month, now.day)
    hijri_en = f"{hd} {hm_en} {hy}"
    hijri_ur = f"{hd} {hm_ur} {hy} ہجری"
    length_mode = detect_length_mode(heard)

    if is_limit_query(heard):
        rem_r = LIMIT_STATUS.get("remaining_requests")
        rem_t = LIMIT_STATUS.get("remaining_tokens")
        mdl = LIMIT_STATUS.get("model") or "Groq"
        if rem_r or rem_t:
            disp = f"{mdl}: {rem_r or '?'} reqs, {rem_t or '?'} tokens left."
            if lang == "UR":
                tts = f"ابھی تک کے ریکارڈ کے مطابق {rem_r or 'کچھ'} ریکویسٹس اور {rem_t or 'کچھ'} ٹوکنز باقی ہیں۔"
                return "LIMIT", disp, tts, False
            return "LIMIT", disp, disp, False
        disp = "Abhi koi limit data record nahi hua. Ek sawal poochne ke baad check karein."
        tts = "ابھی تک کوئی لمٹ ریکارڈ نہیں ہوئی۔ ایک سوال پوچھنے کے بعد دوبارہ چیک کریں۔" if lang == "UR" else "No limit data recorded yet. Ask one question first."
        return "LIMIT", disp, tts, False

    if is_namaz_query(heard):
        disp, tts = build_namaz_response(lang)
        return "NAMAZ", disp, tts, True

    if is_time_or_date_query(heard):
        t_12 = now.strftime("%I:%M %p").lstrip("0")
        d_en = now.strftime("%A, %d %B %Y")
        period_ur = format_urdu_period_and_time(now)
        if lang == "UR":
            disp = f"Waqt {t_12}. Date {d_en}. Hijri {hijri_en}."
            tts = f"ابھی {period_ur}۔ آج تاریخ {d_en} ہے، اور ہجری تاریخ {hijri_ur} ہے۔"
            return "TIME", disp, tts, True
        ans = f"It is {t_12} on {d_en}. Hijri date is {hijri_en}."
        return "TIME", ans, ans, True

    if is_weather_query(heard):
        place, temp, wind = await fetch_weather_info(heard)
        if temp is not None:
            if lang == "UR":
                disp = f"{place} mein abhi {temp} degree Celsius aur hawa {wind} kmh hai."
                tts = f"{place} میں ابھی درجہ حرارت {temp} ڈگری سینٹی گریڈ اور ہَوا کی رفتار {wind} کلومیٹر فی گھنٹہ ہے۔"
                return "WEATHER", disp, tts, False
            ans = f"In {place}, it is currently {temp} degrees Celsius with wind at {wind} kilometers per hour."
            return "WEATHER", ans, ans, False

    if is_cricket_query(heard):
        headlines = await fetch_cricket_context(heard)
        if headlines:
            sys_p = (
                f"You are a clear, direct live cricket presenter. Today is {now.strftime('%d %b %Y, %I:%M %p')}. "
                "Report ONLY facts from the live cricket headlines below in simple, natural wording."
            )
            usr_p = f"User question: {heard}\nLive Cricket Feeds:\n" + "\n".join(f"- {h}" for h in headlines)
            res = await call_groq_bilingual(sys_p, usr_p, lang, length_mode)
            if res:
                return "CRICKET", res[0], res[1], False
            fb = clean_ascii(". ".join(headlines[:2]))[:260]
            return "CRICKET", fb, fb, False

    if is_news_query(heard):
        headlines = await fetch_news_context(heard)
        if headlines:
            sys_p = (
                f"You are a clear, direct live news anchor. Today is {now.strftime('%d %b %Y, %I:%M %p')}. "
                "Summarize the most relevant live headlines below cleanly and accurately."
            )
            usr_p = f"User asked: {heard}\nLive Headlines:\n" + "\n".join(f"- {h}" for h in headlines)
            res = await call_groq_bilingual(sys_p, usr_p, lang, length_mode)
            if res:
                return "NEWS", res[0], res[1], False
            fb = clean_ascii(". ".join(headlines[:2]))[:260]
            return "NEWS", fb, fb, False

    if is_islamic_masayil_query(heard):
        sys_p = (
            f"You are an authentic, respectful, deeply knowledgeable Islamic scholar (Aalim) answering in Kashmir, India. "
            f"Today is {now.strftime('%d %B %Y')} (Hijri: {hijri_en}).\n"
            "STRICT RULES FOR ISLAMIC MASAYIL & FULL ISLAMIC KNOWLEDGE:\n"
            "1) Answer strictly from authentic Quran, Sahih Sunnah, and established Sunni Hanafi Fiqh clearly and accurately.\n"
            "2) NEVER confuse different rulings (for example, Sajda-e-Sahw is done by saying one salam to the right in the final Qa'dah, then two sajdahs, then Tashahhud, Durood, Dua and both salams; NEVER confuse it with Sajda-e-Tilawat).\n"
            "3) NEVER invent or guess Quran verse numbers, Arabic phrases, or Hadith numbers. If citing a Surah or Sahih Bukhari/Muslim, cite only 100% established facts without colon numbers like 2:43.\n"
            "4) Start directly with the exact ruling or answer. Zero fluff, zero rambling, clear natural language."
        )
        res = await call_groq_bilingual(sys_p, heard, lang, length_mode)
        if res:
            hdr = "MASAYIL" if has_word(heard, ["masla", "masail", "masayil", "wuzu", "ghusl", "namaz", "roza", "zakat", "talaq", "nikah", "halal", "haram", "jaiz", "farz", "wajib", "sunnat", "sajda", "qaza", "مسئلہ", "مسائل", "وضو", "نماز", "روزہ", "زکوۃ", "طلاق"]) else "ISLAM"
            return hdr, res[0], res[1], False

    sys_p = (
        f"You are a focused, accurate all-rounder male voice assistant based in Srinagar, Kashmir. "
        f"Today is {now.strftime('%A, %d %B %Y, %I:%M %p')} (Hijri: {hijri_en}). "
        "Give factually accurate, direct, well-spoken answers across every field: Islamic knowledge, science, math, education, psychology, history, geography, electronics, Python, English grammar, Urdu literature, and daily life. "
        "Start directly with the answer. Never say 'As an AI', never add unnecessary filler, never guess unknown facts."
    )
    res = await call_groq_bilingual(sys_p, heard, lang, length_mode)
    if res:
        return "ANSWER", res[0], res[1], False

    disp, tts = await wiki_fallback(heard)
    return "INFO", disp, tts, False


@app.get("/")
async def root():
    return {
        "status": "ok",
        "voice": STATE["voice"],
        "speed": STATE["speed"],
        "volume": STATE["volume"],
        "monster": STATE["monster"],
        "hijri": get_hijri_str(),
        "limits": LIMIT_STATUS,
    }


@app.get("/audio/{fid}")
async def get_audio(fid: str):
    if fid.endswith(".mp3"):
        fid = fid[:-4]
    safe_id = re.sub(r"[^a-zA-Z0-9_]", "", fid)
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

    heard_norm = normalize_input(heard)
    lang = detect_language(heard_norm)
    voice_code = voice_code_for_lang(lang)
    STATE["voice"] = voice_code
    low = heard_norm.lower()
    word_count = len(re.findall(r"[\w\u0600-\u06FF\u0900-\u097F]+", low))

    # 1) STOP
    if (
        word_count <= 5
        and has_word(low, ["stop", "ruko", "band karo", "chup", "بند کرو", "رکو", "توقف", "रुको"])
        and not has_word(low, ["monster", "face", "bus", "non", "watch", "train"])
    ):
        return {
            "action": "STOP",
            "lang": lang,
            "header": "STOP",
            "heard": clean_ascii(heard_norm)[:20],
            "display": "Stopped.",
            "voice": voice_code,
            "volume": STATE["volume"],
            "monster": STATE["monster"],
        }

    # 2) UNMUTE / MUTE
    if word_count <= 5 and has_word(low, ["unmute", "awaaz chalu", "sound on", "آواز چالو"]):
        STATE["volume"] = max(4, STATE.get("last_nonzero_volume", 11))
        msg_d = f"Volume {STATE['volume']} chalu ho gaya."
        msg_t = f"آواز چالو کر دی گئی ہے، والیوم {STATE['volume']} ہے۔" if lang == "UR" else f"Unmuted. Volume is {STATE['volume']}."
        audio_url, total_ms = await make_tts_with_duration(msg_t, voice_code)
        return {
            "action": "SPEAK",
            "lang": lang,
            "header": "VOLUME",
            "heard": clean_ascii(heard_norm)[:20],
            "display": msg_d,
            "pages": build_synced_pages(msg_d, total_ms, lang),
            "audio_url": audio_url,
            "voice": voice_code,
            "volume": STATE["volume"],
            "monster": STATE["monster"],
        }

    if word_count <= 5 and has_word(low, ["mute", "awaaz band", "sound off", "آواز بند"]):
        if STATE["volume"] > 0:
            STATE["last_nonzero_volume"] = STATE["volume"]
        STATE["volume"] = 0
        return {
            "action": "MUTE",
            "lang": lang,
            "header": "MUTE",
            "heard": clean_ascii(heard_norm)[:20],
            "display": "Muted.",
            "voice": voice_code,
            "volume": 0,
            "monster": STATE["monster"],
        }

    # 3) CLEAR HISTORY
    if word_count <= 6 and has_word(low, ["clear history", "reset chat", "history clear", "nayi baat", "naya chat", "ہسٹری صاف"]):
        clear_history()
        msg_d = "Purani chat history saaf ho gayi."
        msg_t = "پرانی چیٹ ہسٹری صاف کر دی گئی ہے۔" if lang == "UR" else "Chat history cleared."
        audio_url, total_ms = await make_tts_with_duration(msg_t, voice_code)
        return {
            "action": "SPEAK",
            "lang": lang,
            "header": "RESET",
            "heard": clean_ascii(heard_norm)[:20],
            "display": msg_d,
            "pages": build_synced_pages(msg_d, total_ms, lang),
            "audio_url": audio_url,
            "voice": voice_code,
            "volume": STATE["volume"],
            "monster": STATE["monster"],
        }

    # 4) VOLUME
    if word_count <= 7 and not has_word(low, ["cube", "cylinder", "sphere", "cone", "formula", "calculate", "area", "math", "physics", "liter", "litre"]):
        m_vol = re.search(
            r"(?:volume|awaaz|آواز)(?:\s+(?:ko|set|to|at|on|par|pe|karo|krdo|kardo|rakh|rakho)){0,3}\s+(\d{1,2})\b",
            low,
        )
        if m_vol:
            STATE["volume"] = max(2, min(16, int(m_vol.group(1))))
            STATE["last_nonzero_volume"] = STATE["volume"]
            msg_d = f"Volume {STATE['volume']} set ho gaya."
            msg_t = f"والیوم {STATE['volume']} سیٹ ہو گیا ہے۔" if lang == "UR" else f"Volume set to {STATE['volume']}."
            audio_url, total_ms = await make_tts_with_duration(msg_t, voice_code)
            return {
                "action": "SPEAK",
                "lang": lang,
                "header": "VOLUME",
                "heard": clean_ascii(heard_norm)[:20],
                "display": msg_d,
                "pages": build_synced_pages(msg_d, total_ms, lang),
                "audio_url": audio_url,
                "voice": voice_code,
                "volume": STATE["volume"],
                "monster": STATE["monster"],
            }

    if has_word(low, ["volume up", "awaaz badhao", "awaaz tej", "awaaz tez", "louder", "increase volume", "آواز بڑھاؤ", "آواز تیز"]):
        STATE["volume"] = min(16, max(2, STATE["volume"]) + 2)
        STATE["last_nonzero_volume"] = STATE["volume"]
        msg_d = f"Volume badha kar {STATE['volume']} kar diya."
        msg_t = f"والیوم بڑھا کر {STATE['volume']} کر دیا ہے۔" if lang == "UR" else f"Volume increased to {STATE['volume']}."
        audio_url, total_ms = await make_tts_with_duration(msg_t, voice_code)
        return {
            "action": "SPEAK",
            "lang": lang,
            "header": "VOLUME",
            "heard": clean_ascii(heard_norm)[:20],
            "display": msg_d,
            "pages": build_synced_pages(msg_d, total_ms, lang),
            "audio_url": audio_url,
            "voice": voice_code,
            "volume": STATE["volume"],
            "monster": STATE["monster"],
        }

    if has_word(low, ["volume down", "awaaz kam", "softer", "quieter", "decrease volume", "آواز کم"]):
        STATE["volume"] = max(3, STATE["volume"] - 2)
        STATE["last_nonzero_volume"] = STATE["volume"]
        msg_d = f"Volume kam kar ke {STATE['volume']} kar diya."
        msg_t = f"والیوم کم کر کے {STATE['volume']} کر دیا ہے۔" if lang == "UR" else f"Volume decreased to {STATE['volume']}."
        audio_url, total_ms = await make_tts_with_duration(msg_t, voice_code)
        return {
            "action": "SPEAK",
            "lang": lang,
            "header": "VOLUME",
            "heard": clean_ascii(heard_norm)[:20],
            "display": msg_d,
            "pages": build_synced_pages(msg_d, total_ms, lang),
            "audio_url": audio_url,
            "voice": voice_code,
            "volume": STATE["volume"],
            "monster": STATE["monster"],
        }

    # 5) SPEED
    if word_count <= 6 and has_word(low, ["slow bolo", "speed slow", "slow speed", "dheere bolo", "ahista bolo", "speak slowly", "آہستہ بولو", "دھیرے بولو"]):
        STATE["speed"] = "Slow"
        msg_d = "Speed slow set ho gayi."
        msg_t = "اب میں آہستہ بولوں گا۔" if lang == "UR" else "I will speak slowly now."
        audio_url, total_ms = await make_tts_with_duration(msg_t, voice_code)
        return {
            "action": "SPEAK",
            "lang": lang,
            "header": "SPEED",
            "heard": clean_ascii(heard_norm)[:20],
            "display": msg_d,
            "pages": build_synced_pages(msg_d, total_ms, lang),
            "audio_url": audio_url,
            "voice": voice_code,
            "volume": STATE["volume"],
            "monster": STATE["monster"],
        }

    if word_count <= 6 and has_word(low, ["fast bolo", "speed fast", "fast speed", "tez bolo", "jaldi bolo", "speak fast", "تیز بولو"]):
        STATE["speed"] = "Fast"
        msg_d = "Speed fast set ho gayi."
        msg_t = "اب میں تھوڑا تیز بولوں گا۔" if lang == "UR" else "I will speak faster now."
        audio_url, total_ms = await make_tts_with_duration(msg_t, voice_code)
        return {
            "action": "SPEAK",
            "lang": lang,
            "header": "SPEED",
            "heard": clean_ascii(heard_norm)[:20],
            "display": msg_d,
            "pages": build_synced_pages(msg_d, total_ms, lang),
            "audio_url": audio_url,
            "voice": voice_code,
            "volume": STATE["volume"],
            "monster": STATE["monster"],
        }

    if word_count <= 6 and has_word(low, ["normal speed", "speed normal", "normal bolo", "نارمل سپیڈ"]):
        STATE["speed"] = "Norm"
        msg_d = "Speed normal set ho gayi."
        msg_t = "سپیڈ نارمل کر دی گئی ہے۔" if lang == "UR" else "Speaking speed set to normal."
        audio_url, total_ms = await make_tts_with_duration(msg_t, voice_code)
        return {
            "action": "SPEAK",
            "lang": lang,
            "header": "SPEED",
            "heard": clean_ascii(heard_norm)[:20],
            "display": msg_d,
            "pages": build_synced_pages(msg_d, total_ms, lang),
            "audio_url": audio_url,
            "voice": voice_code,
            "volume": STATE["volume"],
            "monster": STATE["monster"],
        }

    # 6) MONSTER FACE
    if has_word(low, ["monster", "monister", "animation", "chehra", "face", "مونسٹر", "چہرہ"]):
        if has_word(low, ["off", "band", "close", "hatao", "hide", "home", "بند", "ہٹاؤ"]):
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
        if has_word(low, ["on", "kholo", "open", "dikhao", "chalu", "start", "show", "کھولو", "دکھاؤ", "چالو"]):
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

    # 7) NAMAZ MEEQAT TIMINGS
    if is_namaz_query(heard_norm):
        disp, tts = build_namaz_response(lang)
        append_history({"u": heard_norm, "a": disp[:360], "surah_num": None, "last_ayah": None})
        audio_url, total_ms = await make_tts_with_duration(tts, voice_code, keep_clock_colons=True)
        pages = build_synced_pages(disp, total_ms, lang)
        return {
            "action": "SPEAK",
            "lang": lang,
            "header": "NAMAZ",
            "heard": clean_ascii(heard_norm)[:16],
            "display": disp,
            "pages": pages,
            "audio_url": audio_url,
            "voice": voice_code,
            "volume": STATE["volume"],
            "monster": STATE["monster"],
        }

    # 8) SEERAT-UN-NABI — STRICTLY ONE BOOK (Ar-Raheeq Al-Makhtum), 20 TOPIC-WISE MP3s
    is_seerat, seerat_ch, is_seerat_list, by_topic = parse_seerat_request(heard_norm)
    if is_seerat:
        if is_seerat_list:
            disp_list = "Ar-Raheeq 20 Topics: 1 Arab, 2 Birth, 5 Wahi, 9 Miraj, 11 Hijrah, 13 Badr, 14 Uhud, 15 Khandaq, 16 Hudaibiyah, 18 Fatah Makkah, 19 Hajjatul Wida, 20 Wisaal."
            tts_list = (
                "کتاب الرحیق المختوم کے بیس موضوع وار ابواب موجود ہیں۔ "
                "باب ایک عرب کے حالات، باب دو ولادت، باب پانچ پہلی وحی، باب نو معراج، باب گیارہ ہجرتِ مدینہ، "
                "باب تیرہ غزوہ بدر، باب چودہ احد، باب پندرہ خندق، باب سولہ صلح حدیبیہ، باب اٹھارہ فتح مکہ، "
                "باب انیس خطبہ حجۃ الوداع، اور باب بیس وصالِ مبارک۔ آپ کسی بھی موضوع کا نام یا باب نمبر بول کر سن سکتے ہیں۔"
                if lang != "EN" else disp_list
            )
            s_voice = "EN-M" if lang == "EN" else "UR-M"
            audio_url, total_ms = await make_tts_with_duration(tts_list, s_voice)
            return {
                "action": "SPEAK",
                "lang": "EN" if lang == "EN" else "UR",
                "header": "RAHEEQ 1-20",
                "heard": clean_ascii(heard_norm)[:16],
                "display": disp_list,
                "pages": build_synced_pages(disp_list, total_ms, "EN" if lang == "EN" else "UR"),
                "audio_url": audio_url,
                "voice": s_voice,
                "volume": STATE["volume"],
                "monster": STATE["monster"],
            }

        ch_num = seerat_ch or 1
        en_title, ur_title, _ = SEERAT_CHAPTERS.get(ch_num, SEERAT_CHAPTERS[1])
        s_lang = "EN" if has_word(low, ["in english", "english mein"]) else "UR"

        # If user explicitly pinned SEERAT_ARCHIVE_ID in Render, stream that exact book's MP3
        if s_lang == "UR" and CUSTOM_SEERAT_ARCHIVE_ID and not by_topic:
            archive_mp3 = await fetch_custom_seerat_archive_mp3(ch_num)
            if archive_mp3:
                append_history({
                    "u": heard_norm,
                    "a": f"Played Ar-Raheeq Al-Makhtum MP3 Chapter {ch_num}: {en_title}",
                    "surah_num": None,
                    "last_ayah": None,
                    "seerat_chapter": ch_num,
                })
                lbl = f"RAHEEQ CH{ch_num:02d}"
                return {
                    "action": "STREAM",
                    "lang": "UR",
                    "header": lbl,
                    "title": lbl,
                    "url": archive_mp3,
                    "voice": "UR-M",
                    "volume": STATE["volume"],
                    "monster": STATE["monster"],
                }

        lbl, audio_rel, exact_ms, script_txt = await build_seerat_chapter_narration_mp3(ch_num, s_lang)
        s_voice = "EN-M" if s_lang == "EN" else "UR-M"
        STATE["voice"] = s_voice
        append_history({
            "u": heard_norm,
            "a": f"Ar-Raheeq Al-Makhtum Chapter {ch_num} ({en_title}): {script_txt[:220]}",
            "surah_num": None,
            "last_ayah": None,
            "seerat_chapter": ch_num,
        })
        if audio_rel:
            return {
                "action": "STREAM",
                "lang": s_lang,
                "header": lbl,
                "title": f"{lbl} {en_title[:10]}",
                "url": f"{_public_base_url(request)}{audio_rel}",
                "voice": s_voice,
                "volume": STATE["volume"],
                "monster": STATE["monster"],
            }
        disp_fb = f"Ar-Raheeq Ch {ch_num}: {en_title}."
        return {
            "action": "SPEAK",
            "lang": s_lang,
            "header": lbl,
            "heard": clean_ascii(heard_norm)[:16],
            "display": disp_fb,
            "pages": build_synced_pages(disp_fb, exact_ms, s_lang),
            "audio_url": "",
            "voice": s_voice,
            "volume": STATE["volume"],
            "monster": STATE["monster"],
        }

    # 9) STRICTLY TAFHEEM-UL-QURAN ENGINE (All 114 Surahs)
    if not is_quran_info_question(heard_norm):
        s_num, s_ay, e_ay, is_last = parse_quran_request(heard_norm)
        req_part = detect_part_num(heard_norm)
        q_lang = detect_quran_lang(heard_norm)

        if s_num is None and req_part == 2:
            last_s, _ = get_last_quran_context()
            if last_s in {2, 3, 4, 5, 6, 7}:
                s_num = last_s

        # 9a) Full-Surah Tafheem-ul-Quran Tafseer + Translation (Real Human MP3)
        if (
            s_num is not None
            and s_ay is None
            and req_part == 1
            and q_lang == "UR"
            and not is_follow_up_ayah_request(heard_norm)
            and (is_tafseer_only_request(heard_norm) or s_num == 0)
        ):
            tafheem_mp3 = await fetch_tafheem_tafseer_mp3(s_num)
            if tafheem_mp3:
                append_history({
                    "u": heard_norm,
                    "a": f"Played Tafheem-ul-Quran real human Tafseer MP3 for Surah {s_num}",
                    "surah_num": s_num if s_num > 0 else 1,
                    "last_ayah": None,
                })
                lbl = "TAFHEEM INTRO" if s_num == 0 else f"TAFHEEM {s_num:03d}"
                return {
                    "action": "STREAM",
                    "lang": "UR",
                    "header": lbl,
                    "title": lbl,
                    "url": tafheem_mp3,
                    "voice": "UR-M",
                    "volume": STATE["volume"],
                    "monster": STATE["monster"],
                }

        # 9b) Full-Surah Syed Maududi Urdu Translation (Real Human MP3, Part 1 & Part 2)
        if (
            s_num is not None
            and s_num > 0
            and s_ay is None
            and q_lang == "UR"
            and not is_follow_up_ayah_request(heard_norm)
            and (is_translation_request(heard_norm) or req_part == 2)
        ):
            human_mp3 = await fetch_tafheem_urdu_translation_mp3(s_num, req_part)
            if human_mp3:
                append_history({
                    "u": heard_norm,
                    "a": f"Played Maududi Urdu translation MP3 for Surah {s_num} Part {req_part}",
                    "surah_num": s_num,
                    "last_ayah": None,
                })
                lbl = f"TAFHEEM {s_num:03d} P2" if (req_part == 2 and s_num in {2, 3, 4, 5, 6, 7}) else f"MAUDUDI {s_num:03d}"
                return {
                    "action": "STREAM",
                    "lang": "UR",
                    "header": lbl,
                    "title": lbl,
                    "url": human_mp3,
                    "voice": "UR-M",
                    "volume": STATE["volume"],
                    "monster": STATE["monster"],
                }

        # 9c) Specific Ayah/Range -> Exact Maududi Translation + AUTOMATIC Pure Urdu Tafheem Tafseer + Synced OLED
        if s_num is not None and s_num > 0 and (s_ay is not None or is_tafseer_intent(heard_norm) or is_follow_up_ayah_request(heard_norm)):
            q_voice = voice_code_for_lang(q_lang)
            STATE["voice"] = q_voice
            hdr_title, audio_rel, exact_ms, summary_txt, pages, actual_last_ay = await build_exact_tafheem_ayah_audio(
                s_num, s_ay, e_ay, is_last, q_lang
            )
            append_history({
                "u": heard_norm,
                "a": summary_txt[:360],
                "surah_num": s_num,
                "last_ayah": actual_last_ay,
            })
            return {
                "action": "SPEAK",
                "lang": q_lang,
                "header": hdr_title,
                "heard": clean_ascii(heard_norm)[:16],
                "display": pages[0]["t"] if pages else hdr_title,
                "pages": pages,
                "audio_url": audio_rel,
                "voice": q_voice,
                "volume": STATE["volume"],
                "monster": STATE["monster"],
            }

        # 9d) Direct Tilawat -> Mishary Alafasy MP3
        play_trigger = has_word(low, ["sunao", "sunawo", "sunaiye", "play", "recite", "recitation", "tilawat", "tilawah", "tilaat", "chala", "chalao", "lagao", "سناؤ", "سنائیں", "تلاوت", "چلاؤ", "لگاؤ", "सुनाओ", "तिलावत"])
        if s_num is not None and s_num > 0 and (play_trigger or word_count <= 4):
            append_history({"u": heard_norm, "a": f"Played Surah {s_num}", "surah_num": s_num, "last_ayah": None})
            stream_url = f"https://server8.mp3quran.net/afs/{s_num:03d}.mp3"
            return {
                "action": "STREAM",
                "lang": "EN",
                "header": f"SURAH {s_num:03d}",
                "title": f"SURAH {s_num:03d}",
                "url": stream_url,
                "voice": voice_code,
                "volume": STATE["volume"],
                "monster": STATE["monster"],
            }

        if play_trigger and has_word(low, ["quran", "tilawat", "tilaat", "قرآن", "تلاوت", "कुरान", "तिलावत"]):
            append_history({"u": heard_norm, "a": "Played Surah 1", "surah_num": 1, "last_ayah": None})
            return {
                "action": "STREAM",
                "lang": "EN",
                "header": "SURAH 001",
                "title": "SURAH 001",
                "url": "https://server8.mp3quran.net/afs/001.mp3",
                "voice": voice_code,
                "volume": STATE["volume"],
                "monster": STATE["monster"],
            }

    # 10) ISLAMIC MASAYIL + ALL-ROUNDER MALE AI ASSISTANT (Strictly UR-M or EN-M)
    header, disp, tts, keep_clock = await generate_answer(heard_norm, lang)
    append_history({"u": heard_norm, "a": disp[:360], "surah_num": None, "last_ayah": None})
    audio_url, total_ms = await make_tts_with_duration(tts, voice_code, keep_clock_colons=keep_clock)
    pages = build_synced_pages(disp, total_ms, lang)

    return {
        "action": "SPEAK",
        "lang": lang,
        "header": header,
        "heard": clean_ascii(heard_norm)[:16],
        "display": disp,
        "pages": pages,
        "audio_url": audio_url,
        "voice": voice_code,
        "volume": STATE["volume"],
        "monster": STATE["monster"],
    }
