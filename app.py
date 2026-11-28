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

# Strong multilingual/reasoning models first; 8B strictly last resort
FALLBACK_GROQ_MODELS = [
    "llama-3.3-70b-versatile",
    "openai/gpt-oss-120b",
    "meta-llama/llama-4-maverick-17b-128e-instruct",
    "qwen/qwen3-32b",
    "openai/gpt-oss-20b",
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "llama-3.1-8b-instant",
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

# STRICTLY LOCKED TO ONLY THESE TWO ASSISTANTS (Urdu: Asad, English: Prabhat)
VOICE_CANDIDATES = {
    "UR-M": ["ur-PK-AsadNeural"],
    "EN-M": ["en-IN-PrabhatNeural"],
}

SPEED_MAP = {
    "Slow": "-16%",
    "Norm": "+0%",
    "Fast": "+12%",
}

# Exact J&K / Kashmir coordinates + Whisper typo protection (Shopian != Sofia)
KASHMIR_WEATHER_PLACES: List[Tuple[List[str], str, float, float]] = [
    (["pinjoora", "pinjura", "پنجورہ"], "Pinjoora, Shopian", 33.7450, 74.8650),
    (["shopian", "shopiyan", "shupiyan", "supiyan", "shopyan", "sofia", "sophia", "sofiyan", "شوپیاں", "شاپیاں", "صوفیہ", "صوفیا", "शोपियां", "सोफिया"], "Shopian", 33.7199, 74.8342),
    (["pulwama", "پلوامہ", "पुलवामा"], "Pulwama", 33.8716, 74.8946),
    (["srinagar", "سرینگر", "श्रीनगर"], "Srinagar", 34.0837, 74.7973),
    (["anantnag", "اننت ناگ", "अनंतनाग"], "Anantnag", 33.7311, 75.1487),
    (["kulgam", "کلگام", "कुलगाम"], "Kulgam", 33.6451, 75.0180),
    (["baramulla", "baramula", "بارہمولہ"], "Baramulla", 34.2090, 74.3429),
    (["budgam", "badgam", "بڈگام"], "Budgam", 34.0176, 74.7219),
    (["kupwara", "کپواڑہ"], "Kupwara", 34.5262, 74.2546),
    (["bandipora", "بانڈی پورہ"], "Bandipora", 34.4225, 74.6431),
    (["ganderbal", "گاندربل"], "Ganderbal", 34.2266, 74.7719),
    (["pahalgam", "پہلگام"], "Pahalgam", 34.0161, 75.3150),
    (["gulmarg", "گلمرگ"], "Gulmarg", 34.0484, 74.3805),
    (["jammu", "جموں", "जम्मू"], "Jammu", 32.7266, 74.8570),
    (["kashmir", "کشمیر", "कश्मीर"], "Srinagar", 34.0837, 74.7973),
]

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

NEWS_STOPWORDS = {
    "news", "khabar", "khabrein", "khabren", "khabrain", "khabrai", "khabaren", "headlines", "live", "today", "aaj",
    "ki", "ka", "ke", "taza", "taaza", "latest", "breaking", "batao", "bataiye", "sunao",
    "sunawo", "tell", "me", "about", "what", "is", "the", "please", "abhi", "kya",
    "hai", "hain", "آج", "کی", "کا", "کے", "تازہ", "خبریں", "خبر", "سناؤ", "سنائیں",
    "بتاؤ", "بتائیں", "نیوز", "لائیو", "کیا", "ہے", "ہیں", "ابھی", "أخبار",
    "आज", "की", "का", "के", "ताजा", "खबर", "खबरें", "सुनाओ", "बताओ", "न्यूज", "लाइव",
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

# Clean pronunciation rules for ur-PK-AsadNeural (no comma before یا!)
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
    (r"(?<!\w)موتر(?!\w)", "پیشاب"),
    (r"(?<!\w)سواد(?!\w)", "ذائقہ"),
    (r"(?<!\w)ادے(?!\w)", "طلوع"),
    (r"سجدہ\s*سہو", "سجدۂ سَہو"),
    (r"سجود\s*سہو", "سجدۂ سَہو"),
    (r"(?<!\w)سہو(?!\w)", "سَہو"),
    (r"ٹھنڈی\s+ہوا", "ٹھنڈی ہَوا"),
    (r"تیز\s+ہوا", "تیز ہَوا"),
    (r"گرم\s+ہوا", "گرم ہَوا"),
    (r"ہوا\s+چل", "ہَوا چل"),
    (r"ہوا\s+نکل", "ہَوا نکل"),
    (r"ہوا\s+خارج", "ہَوا خارج"),
    (r"ہوا\s+کی", "ہَوا کی"),
    (r"ہوا\s+کا", "ہَوا کا"),
    (r"اور\s+ہوا\s+", "اور ہَوا "),
    (r"(\d+)\s*%", r"\1 فیصد"),
    (r"\b(?:IST|UTC|GMT)\b", ""),
    (r"(?<!\w)اِسٹ(?!\w)", ""),
    (r"(?<!\w)آئی\s*ایس\s*ٹی(?!\w)", ""),
]


def get_mp3_duration_ms(data: bytes) -> int:
    """Exact pure-Python MPEG Layer III frame parser so OLED pages NEVER outrun audio."""
    n = len(data)
    if n < 16:
        return 0
    pos = 0
    if data[:3] == b"ID3" and n >= 10:
        tag_size = (
            ((data[6] & 0x7F) << 21)
            | ((data[7] & 0x7F) << 14)
            | ((data[8] & 0x7F) << 7)
            | (data[9] & 0x7F)
        )
        pos = 10 + tag_size

    br_v1_l3 = (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 0)
    br_v2_l3 = (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160, 0)
    sr_table = {
        3: (44100, 48000, 32000),  # MPEG1
        2: (22050, 24000, 16000),  # MPEG2
        0: (11025, 12000, 8000),   # MPEG2.5
    }

    total_sec = 0.0
    frames = 0
    while pos + 4 <= n:
        if data[pos] == 0xFF and (data[pos + 1] & 0xE0) == 0xE0:
            ver = (data[pos + 1] >> 3) & 0x03
            layer = (data[pos + 1] >> 1) & 0x03
            br_idx = (data[pos + 2] >> 4) & 0x0F
            sr_idx = (data[pos + 2] >> 2) & 0x03
            pad = (data[pos + 2] >> 1) & 0x01
            if ver in sr_table and layer == 1 and 1 <= br_idx <= 14 and sr_idx <= 2:
                br_kbps = br_v1_l3[br_idx] if ver == 3 else br_v2_l3[br_idx]
                sr = sr_table[ver][sr_idx]
                samples = 1152 if ver == 3 else 576
                coef = 144 if ver == 3 else 72
                frame_len = (coef * br_kbps * 1000) // sr + pad
                if frame_len >= 4 and pos + frame_len <= n:
                    total_sec += samples / sr
                    frames += 1
                    pos += frame_len
                    continue
        pos += 1

    if frames >= 3 and total_sec > 0.2:
        return int(round(total_sec * 1000))
    return 0


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


URDU_WEEKDAYS = {
    "Monday": "پیر",
    "Tuesday": "منگل",
    "Wednesday": "بدھ",
    "Thursday": "جمعرات",
    "Friday": "جمعہ",
    "Saturday": "ہفتہ",
    "Sunday": "اتوار",
}
URDU_MONTHS = {
    1: "جنوری",
    2: "فروری",
    3: "مارچ",
    4: "اپریل",
    5: "مئی",
    6: "جون",
    7: "جولائی",
    8: "اگست",
    9: "ستمبر",
    10: "اکتوبر",
    11: "نومبر",
    12: "دسمبر",
}


def format_urdu_gregorian_date(now: datetime.datetime) -> str:
    return f"{URDU_WEEKDAYS.get(now.strftime('%A'), '')}، {now.day} {URDU_MONTHS.get(now.month, '')} {now.year}"


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
    if not keep_clock_colons:
        s = re.sub(r"(?<!\d)(2[0-3]|[01]?\d)\s*:\s*00(?!\d)", r"\1 بجے", s)
        s = re.sub(r"(?<!\d)(2[0-3]|[01]?\d)\s*:\s*([0-5]\d)(?!\d)", r"\1 بج کر \2 منٹ", s)
    s = re.sub(r"[\(\[]\s*[\d\u0660-\u0669\u06F0-\u06F9]+\s*[\)\]]", "", s)
    s = re.sub(r"[\u064B-\u065F\u0670\u06D6-\u06ED\u0640]", "", s)
    s = s.replace("\u0671", "ا").replace("أ", "ا").replace("إ", "ا")
    s = s.replace("الله", "اللہ").replace("لله", "للہ")
    s = s.replace("(", " ").replace(")", " ").replace("[", " ").replace("]", " ")
    s = re.sub(r"[*_#`~•\"']+", "", s)
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
        "meeqat", "miqat", "prayer time", "prayer times", "prayer timing", "prayer timings",
        "salah time", "salah times", "namaz timing", "namaz timings", "namaz time", "namaz times",
        "namaz ka waqt", "namaz ke waqt", "namaz ka time", "namaz ke auqat", "auqat e namaz",
        "nimaz ka waqt", "nimaz time", "nimaz timing", "nimaz timings",
        "اوقات نماز", "نماز کا وقت", "نماز کے اوقات", "نماز کے وقت", "نماز کا ٹائم", "نماز ٹائمنگ",
        "नमाज का वक्त", "नमाज़ का वक्त", "नमाज टाइम", "नमाज टाइमिंग",
    ]):
        return True
    prayer_names = [
        "namaz", "nimaz", "salah", "salat", "fajr", "fajar", "zohar", "zuhr", "dhuhr",
        "asr", "asar", "maghrib", "magrib", "esha", "isha",
        "نماز", "فجر", "ظہر", "عصر", "مغرب", "عشاء", "नमाज", "नमाज़",
    ]
    time_hints = [
        "time", "times", "timing", "timings", "waqt", "auqat", "awqat", "schedule",
        "kab", "kitne baje", "baje", "وقت", "اوقات", "ٹائم", "ٹائمنگ", "کب", "بجے",
        "वक्त", "टाइम", "टाइमिंग", "कब", "बजे",
    ]
    masail_hints = [
        "masla", "masayil", "masail", "tareeqa", "tarika", "farz", "sunnat", "wajib",
        "qaza", "toot", "sajda", "rakat", "wuzu", "مسئلہ", "مسائل", "طریقہ", "رکعت", "قضا", "سجدہ",
    ]
    quran_hints = ["surah", "sura", "suratul", "tafseer", "tafsir", "tarjuma", "ayat", "aayat", "سورہ", "سورۃ", "سورة", "تفسیر", "ترجمہ", "آیت"]
    if has_word(text, prayer_names) and has_word(text, time_hints) and not has_word(text, masail_hints) and not has_word(text, quran_hints):
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
    if has_word(text, ["hijri", "ہجری", "islamic date", "اسلامی تاریخ", "التاريخ", "الساعة"]):
        return True
    clock_phrases = [
        "what time", "what is the time", "current time", "tell me the time",
        "time kya", "kya time", "waqt kya", "kya waqt", "kitna time", "kitna waqt",
        "kitne baje", "kya baje", "abhi time", "abhi waqt",
        "today date", "today's date", "what is the date", "what date", "what day is",
        "aaj ki date", "aaj ki tareekh", "aaj ki tarikh", "aaj kya date", "aaj kya tareekh",
        "aaj kaun sa din", "aaj kya din", "aaj ka din",
        "ٹائم کیا", "وقت کیا", "کتنے بجے", "آج کی تاریخ", "آج کیا تاریخ", "آج کون سا دن", "آج کا دن",
        "आज की तारीख", "कितने बजे", "टाइम क्या", "वक्त क्या", "आज कौन सा दिन",
    ]
    if has_word(text, clock_phrases):
        return True
    toks = re.findall(r"[\w\u0600-\u06FF\u0900-\u097F]+", (text or "").lower())
    if len(toks) <= 5 and has_word(text, ["time", "waqt", "date", "tareekh", "tarikh", "baje", "وقت", "ٹائم", "تاریخ", "بجے", "तारीख", "टाइम"]):
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
    if has_word(text, ["thand", "thandi", "sardi", "garmi", "dhoop", "barf", "ٹھنڈ", "سردی", "گرمی", "برف"]) and has_word(text, ["aaj", "abhi", "kal", "kaisi", "kaisa", "kitni", "shopian", "sofia", "srinagar", "pulwama", "kashmir", "bahar", "آج", "ابھی"]):
        return True
    return False


def is_cricket_query(text: str) -> bool:
    if has_word(text, ["cricket", "ipl", "t20", "odi", "test match", "virat", "rohit", "babar", "psl", "bbl", "world cup", "asia cup", "wicket", "کرکٹ", "क्रिकेट"]):
        return True
    if has_word(text, ["score", "live score", "match", "میچ", "اسکور"]) and has_word(text, ["india", "pakistan", "bangladesh", "sri lanka", "australia", "england", "match", "live", "team", "series", "today", "aaj", "انڈیا", "پاکستان", "بنگلہ دیش"]):
        return True
    return False


def is_news_query(text: str) -> bool:
    return has_word(text, [
        "news", "khabar", "khabrein", "khabren", "khabrain", "khabrai", "khabaren",
        "headlines", "taza khabar", "taaza khabar", "breaking news", "latest news",
        "خبر", "خبریں", "نیوز", "أخبار", "खबर", "खबरें", "न्यूज",
    ])


def get_verified_core_answer(heard: str, lang: str) -> Optional[Tuple[str, str, str]]:
    """Zero-hallucination verified answers for core Hanafi Masayil & Arabic Grammar."""
    low = (heard or "").lower()

    # 1) Sajda-e-Sahw (Authentic Sunni Hanafi Fiqh)
    if has_word(low, ["sajda sahw", "sajda e sahw", "sajdah sahw", "sajda-e-sahw", "سجدہ سہو", "سجدۂ سہو", "सजदा सहव", "सजदा-ए-सहव"]):
        if lang == "EN":
            ans = (
                "In Hanafi Fiqh, to perform Sajda-e-Sahw, recite Tashahhud in the final sitting, then turn your head and say one salam to the right only. "
                "Next, say Allahu Akbar and perform two sajdahs just like regular sajdahs. "
                "Then sit back up, recite Tashahhud, Durood Ibrahim, and Dua again, and complete the prayer with salam to both the right and the left."
            )
            return "MASAYIL", ans, ans
        disp = (
            "Hanafi fiqh mein Sajda-e-Sahw ka tareeqa yeh hai: aakhri qa'dah mein Tashahhud padhne ke baad sirf daayein taraf ek salam pherein. "
            "Phir Allahu Akbar keh kar do sajde karein. "
            "Uske baad dobara baith kar Tashahhud, Durood Shareef aur Dua padhein, aur aakhir mein daayein aur baayein dono taraf salam pher kar namaz mukammal karein."
        )
        tts = (
            "حنفی فقہ میں سجدۂ سَہو کا طریقہ یہ ہے۔ آخری قعدہ میں تشہد پڑھنے کے بعد صرف دائیں طرف ایک سلام پھیریں۔ "
            "پھر اللہ اکبر کہہ کر دو سجدے کریں۔ "
            "اس کے بعد دوبارہ بیٹھ کر تشہد، درود شریف اور دعا پڑھیں، اور آخر میں دائیں اور بائیں دونوں طرف سلام پھیر کر نماز مکمل کریں۔"
        )
        return "MASAYIL", disp, tts

    # 2) Wuzu tootne wali cheezein (Authentic Sunni Hanafi Nawaqiz-e-Wuzu)
    if has_word(low, ["wuzu", "wudu", "wazoo", "وضو", "वुजू", "वुज़ू"]) and has_word(low, ["toot", "tut", "torne", "todne", "break", "breaks", "ٹوٹ", "توڑ", "टूट", "तोड़"]):
        if lang == "EN":
            ans = (
                "In Hanafi Fiqh, Wuzu breaks due to six main things: anything coming out of the private parts including urine, stool, or passing wind; "
                "blood or pus flowing from a wound on the body; a mouthful of vomiting; "
                "deep sleep while lying down or leaning on something; fainting, insanity, or intoxication; "
                "and laughing out loud during a prayer that includes ruku and sajdah."
            )
            return "MASAYIL", ans, ans
        disp = (
            "Hanafi fiqh mein wuzu in cheezon se toot jata hai: peshab, pakhana ya hawa kharij hona; "
            "jism ke kisi hisse se khoon ya peep nikal kar beh jana; munh bhar kar qai yani ulti hona; "
            "let kar ya tek laga kar gehri neend so jana; behoshi ya nasha; aur ruku sajde wali namaz mein qahqaha laga kar hansna."
        )
        tts = (
            "حنفی فقہ میں وضو ان چیزوں سے ٹوٹ جاتا ہے۔ پیشاب، پاخانہ یا ہَوا خارج ہونا۔ "
            "جسم کے کسی حصے سے خون یا پیپ نکل کر بہہ جانا۔ منہ بھر کر قے یعنی الٹی آنا۔ "
            "لیٹ کر یا ٹیک لگا کر گہری نیند سو جانا۔ بے ہوشی یا نشہ طاری ہونا۔ اور رکوع سجدے والی نماز میں قہقہہ لگا کر ہنسنا۔"
        )
        return "MASAYIL", disp, tts

    # 3) Af'al-e-Naqisa (Arabic Grammar — Incomplete Verbs)
    if has_word(low, ["af'al naqisa", "afal naqisa", "afal e naqisa", "afal-e-naqisa", "afal e naqsa", "afal naqsa", "afeel naqisa", "افعال ناقصہ", "أفعال ناقصة", "अफआल नाकिसा"]):
        if lang == "EN":
            ans = (
                "In Arabic grammar, Af'al-e-Naqisa are incomplete verbs like Kaana, Saara, Laysa, Asbaha, and Amsa. "
                "They enter a nominal sentence (Jumlah Ismiyyah), giving Raf' (nominative case) to the subject called their Ism, and Nasb (accusative case) to the predicate called their Khabar. "
                "For example: Kaana Allahu Aleeman (Allah is All-Knowing), and Laysa al-ilmu sahlan (Knowledge is not easy)."
            )
            return "GRAMMAR", ans, ans
        disp = (
            "Arabic grammar mein Af'al-e-Naqisa woh afaal hain jo sirf faail se baat poori nahi karte balki khabar ke mohtaj hote hain, jaise Kaana, Saara, Laysa, Asbaha aur Amsa. "
            "Yeh Jumlah Ismiyyah par dakhil ho kar mubtada ko raf' dete hain jise unka Ism kehte hain, aur khabar ko nasb dete hain. "
            "Misal ke taur par: Kaana Allahu Aleeman, aur Laysa al-ilmu sahlan."
        )
        tts = (
            "عربی گرامر میں افعالِ ناقصہ وہ افعال ہیں جو صرف فاعل سے بات مکمل نہیں کرتے بلکہ خبر کے محتاج ہوتے ہیں، جیسے کانَ، صارَ، لیسَ، اصبحَ اور امسیٰ۔ "
            "یہ جملہ اسمیہ پر داخل ہو کر مبتدا کو رفع دیتے ہیں جسے ان کا اسم کہتے ہیں، اور خبر کو نصب دیتے ہیں۔ "
            "مثال کے طور پر، کانَ اللہُ علیماً، یعنی اللہ سب کچھ جاننے والا ہے، اور لیسَ العلمُ سہلاً، یعنی علم آسان نہیں ہے۔"
        )
        return "GRAMMAR", disp, tts

    return None


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
        "nikah", "talaq", "khula", "iddat", "mehr", "mahr", "virasat", "wirasat", "miras",
        "islamic inheritance", "inheritance in islam", "inheritance under islam", "hanafi inheritance",
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
        "surah", "sura", "sury", "surat", "suratul", "soorah", "soora",
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
    low = (text or "").lower()
    if has_word(low, ["english mein", "in english", "speak english", "انگریزی میں"]):
        return "EN"
    if has_word(low, ["urdu mein", "in urdu", "hindi mein", "اردو میں"]):
        return "UR"

    if re.search(r"[\u0900-\u097F\u0600-\u06FF]", text):
        return "UR"

    urdu_markers = {
        "kya", "hai", "hain", "tha", "thi", "hota", "hoti", "hote", "hoga", "hogi",
        "batao", "bataiye", "sunao", "sunawo", "sunahu", "samjhao", "samjhaiye",
        "kaise", "kaisa", "kaisi", "aaj", "kal", "abhi", "kab", "kyun", "kyu", "kaun", "kahan",
        "kitna", "kitni", "kitne", "kisne", "kisko", "kise", "nahi", "nhi", "aur", "lekin",
        "mausam", "mosam", "khabar", "khabrein", "khabren", "khabrain", "khabrai", "tarjuma", "tarjama",
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


def voice_code_for_lang(lang: str) -> str:
    return "EN-M" if lang == "EN" else "UR-M"


def fmt_12_short(mins: int) -> str:
    mins = max(0, min(1439, int(round(mins))))
    h24, m = divmod(mins, 60)
    h12 = h24 % 12 or 12
    return f"{h12}:{m:02d}"


def fmt_urdu_time(mins: int) -> str:
    mins = max(0, min(1439, int(round(mins))))
    h24, m = divmod(mins, 60)
    h12 = h24 % 12 or 12
    if m == 0:
        return f"{h12} بجے"
    return f"{h12} بج کر {m} منٹ"


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
            f"آج کشمیر کے اوقاتِ نماز یہ ہیں۔ "
            f"فجر {fmt_urdu_time(f)}۔ "
            f"ظہر {fmt_urdu_time(z)}۔ "
            f"عصر {fmt_urdu_time(a)}۔ "
            f"مغرب {fmt_urdu_time(mg)}۔ "
            f"عشاء {fmt_urdu_time(e)}۔"
        )
        return disp, ur_tts
    return disp, f"Today's prayer timings in Kashmir: {disp}"


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
    m_surah = re.search(r"(?<!\w)(?:surah|sura|sury|surat|suratul|soorah|soora|سوره|سورہ|سورۃ|سورة|सूरह)\s*(?:number|num\.?|no\.?|نمبر|نंबर)?\s*(\d{1,3})(?!\w)", rem)
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
        except Exception as ex:
            print(f"Custom Seerat archive error: {ex}")
    if chapter_num in SEERAT_MP3_CACHE:
        return await _resolve_final_mp3_url(SEERAT_MP3_CACHE[chapter_num])
    return None


async def fetch_tafheem_tafseer_mp3(surah_num: int) -> Optional[str]:
    for item_id in (ARCHIVE_TAFHEEM_FULL_ID, ARCHIVE_TAFHEEM_SHORT_ID):
        smap = await _load_archive_surah_map(item_id)
        if surah_num in smap:
            return await _resolve_final_mp3_url(smap[surah_num])
    return None


async def fetch_tafheem_urdu_translation_mp3(surah_num: int, part: int = 1) -> Optional[str]:
    qmap = await _load_quranurdu_mishari_map()
    if surah_num in qmap:
        url = qmap[surah_num].get(part) or qmap[surah_num].get(1)
        if url:
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
    if "120b" in m:
        return 2
    if "llama-4" in m and "maverick" in m:
        return 3
    if "qwen" in m and ("32b" in m or "27b" in m):
        return 4
    if "20b" in m:
        return 5
    if "llama-4" in m and "scout" in m:
        return 6
    if "llama-3.1-8b" in m:
        return 12
    if "llama" in m:
        return 9
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
                    MODEL_CACHE[key] = (now_t, chat_models[:7])
                    return chat_models[:7]
    except Exception as ex:
        print(f"Groq /models error: {ex}")

    return FALLBACK_GROQ_MODELS


async def call_groq_pure_urdu_text(system_prompt: str, user_prompt: str, max_tokens: int = 1600) -> str:
    if not RAW_KEYS:
        return ""

    for key in RAW_KEYS:
        models = await get_available_groq_models(key)
        for model in models:
            try:
                tok_limit = max(max_tokens, 2000) if ("qwen" in model.lower() or "oss" in model.lower()) else max_tokens
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
                            "max_tokens": tok_limit,
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
                    else:
                        print(f"Pure Urdu Groq {model} ({resp.status_code}): {resp.text[:140]}")
            except Exception as ex:
                print(f"Pure Urdu Groq {model} exception: {ex}")
    return ""


async def build_seerat_chapter_narration_mp3(chapter_num: int, lang: str) -> Tuple[str, str, int, str]:
    """Bounded, original chapter narration for synchronous ESP32; sentinel denotes failure."""
    if chapter_num not in SEERAT_CHAPTERS:
        chapter_num = 1
    lang = "EN" if lang == "EN" else "UR"
    en_title, ur_title, _ = SEERAT_CHAPTERS[chapter_num]
    cache_fid = f"seerat_v3_ch{chapter_num:02d}_{lang.lower()}"
    cache_path = os.path.join(AUDIO_DIR, f"{cache_fid}.mp3")
    if os.path.exists(cache_path) and os.path.getsize(cache_path) > 2000:
        with open(cache_path, "rb") as f:
            audio = f.read()
        title = en_title if lang == "EN" else ur_title
        return f"SEERAT CH{chapter_num:02d}", f"/audio/{cache_fid}.mp3", get_mp3_duration_ms(audio) or int(len(audio) / 6), title

    timeout_msg = (
        "The Seerat chapter could not be prepared in time. Please retry."
        if lang == "EN"
        else "سیرت کا باب مقررہ وقت میں تیار نہیں ہو سکا۔ دوبارہ کوشش کریں۔"
    )
    try:
        if lang == "EN":
            sys_p = (
                "Write an accurate, original Seerah study narration in 12 to 16 short, clear spoken-English sentences. "
                "Cover essential setting, chronology, people, outcome, and lessons; do not invent quotations, dates, or disputed claims. "
                "This is a concise paraphrase based on Ar-Raheeq Al-Makhtum, not verbatim text. No headings or filler."
            )
            user_p = f"Chapter {chapter_num}: {en_title}. Explain the essential topic for a learner."
            res = await asyncio.wait_for(call_groq_bilingual(sys_p, user_p, "EN", "chapter"), timeout=14.0)
            body = res[1].strip() if res and len(res) > 1 else ""
            if not body:
                return f"SEERAT CH{chapter_num:02d}", "", 0, "__SEERAT_ERROR__:The detailed chapter is unavailable right now. Please retry."
            script = f"Seerah study narration. Chapter {chapter_num}: {en_title}. {body}"
            voice = "EN-M"
        else:
            sys_p = (
                "سیرت پر 12 سے 16 مختصر، واضح اور رواں جملوں میں اصل تعلیمی خلاصہ لکھیں۔ بنیادی تاریخی پس منظر، ترتیب، شخصیات، نتیجہ اور سبق بیان کریں۔ "
                "غیر ثابت اقوال، تاریخیں یا اختلافی باتیں قطعی انداز میں نہ کہیں۔ یہ الرحیق المختوم کے مطالعے پر مبنی خلاصہ ہے، اصل کتاب کا لفظ بہ لفظ مکمل متن نہیں۔ "
                "صرف سلیس اردو رسم الخط، بغیر عنوان، انگریزی یا غیر ضروری تمہید۔"
            )
            user_p = f"سیرت کا باب {chapter_num}، موضوع {ur_title}۔ سیکھنے والے کے لیے ضروری باتیں بیان کریں۔"
            body = await asyncio.wait_for(call_groq_pure_urdu_text(sys_p, user_p, max_tokens=1400), timeout=14.0)
            if not body:
                return f"SEERAT CH{chapter_num:02d}", "", 0, "__SEERAT_ERROR__:تفصیلی باب ابھی دستیاب نہیں۔ تھوڑی دیر بعد دوبارہ کوشش کریں۔"
            script = f"سیرت کا تعلیمی خلاصہ، باب نمبر {chapter_num}، {ur_title}۔ {body}"
            voice = "UR-M"
        audio_rel, duration = await asyncio.wait_for(make_tts_with_duration(script, voice, fixed_fid=cache_fid), timeout=12.0)
        if not audio_rel:
            err = "Audio generation failed; please retry." if lang == "EN" else "آڈیو تیار نہیں ہو سکی۔ دوبارہ کوشش کریں۔"
            return f"SEERAT CH{chapter_num:02d}", "", 0, "__SEERAT_ERROR__:" + err
        return f"SEERAT CH{chapter_num:02d}", audio_rel, duration, script
    except asyncio.TimeoutError:
        return f"SEERAT CH{chapter_num:02d}", "", 0, "__SEERAT_ERROR__:" + timeout_msg
    except Exception as exc:
        print(f"Seerat chapter {chapter_num} generation failed: {type(exc).__name__}: {exc}")
        return f"SEERAT CH{chapter_num:02d}", "", 0, "__SEERAT_ERROR__:" + timeout_msg


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
            tafseer_part = await call_groq_pure_urdu_text(sys_p, usr_p, max_tokens=1500)
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


async def fetch_rss_items(urls: List[str], limit: int = 5) -> List[str]:
    """Fetch feeds concurrently, preserve Urdu/Arabic Unicode, deduplicate by title, and interleave feeds."""
    headers = {"User-Agent": "Mozilla/5.0 (compatible; ESP32Assistant/2.3)"}
    async with httpx.AsyncClient(timeout=httpx.Timeout(6.0, connect=3.0), follow_redirects=True, headers=headers) as client:
        async def fetch_one(url: str) -> List[str]:
            try:
                resp = await client.get(url)
                resp.raise_for_status()
                if len(resp.content) > 2_000_000:
                    return []
                root = ET.fromstring(resp.content)
                entries = root.findall(".//item")
                if not entries:
                    entries = [e for e in root.iter() if e.tag.rsplit("}", 1)[-1] == "entry"]
                found = []
                for entry in entries:
                    vals = {e.tag.rsplit("}", 1)[-1]: (e.text or "").strip() for e in entry.iter()}
                    title = re.sub(r"<[^>]*>", "", vals.get("title", ""))
                    title = re.sub(r"\s+", " ", title).strip()
                    pub = vals.get("pubDate") or vals.get("published") or vals.get("updated") or ""
                    if title:
                        found.append(f"{title} ({pub[:16]})" if pub else title)
                return found
            except Exception as exc:
                print(f"RSS feed unavailable ({url}): {type(exc).__name__}: {exc}")
                return []
        batches = await asyncio.gather(*(fetch_one(url) for url in urls))

    def dedupe_key(item: str) -> str:
        title = re.sub(r"\s+\([^()]*\)$", "", item).casefold()
        return " ".join("".join(ch if ch.isalnum() else " " for ch in title).split())

    merged, seen = [], set()
    max_items = max(1, min(limit, 20))
    for idx in range(max((len(batch) for batch in batches), default=0)):
        for batch in batches:
            if idx >= len(batch):
                continue
            item = batch[idx]
            key = dedupe_key(item)
            if key and key not in seen:
                seen.add(key)
                merged.append(item)
                if len(merged) >= max_items:
                    return merged
    return merged


async def fetch_cricket_context(query: str, lang: str = "UR") -> List[str]:
    q = (query or "").strip()
    q = re.sub(r"\b(cricket|news|live|score|scores|match|aaj|ki|kya|hai|batao|bataiye|sunao|sunawo)\b", " ", q, flags=re.I)
    q = re.sub(r"(?:کرکٹ|خبر|خبریں|لائیو|اسکور|سکور|میچ|آج|کی|کیا|ہے|بتاؤ|بتائیں|سناؤ|سنائیں)", " ", q)
    q = re.sub(r"\s+", " ", q).strip()
    has_urdu_script = bool(re.search(r"[\u0600-\u06FF]", q))
    hl, gl, ceid = ("ur", "IN", "IN:ur") if has_urdu_script else ("en-IN", "IN", "IN:en")
    search_q = ("کرکٹ لائیو اسکور " + q) if has_urdu_script else ("cricket live score match " + q)
    gnews = f"https://news.google.com/rss/search?q={urllib.parse.quote(search_q.strip())}&hl={hl}&gl={gl}&ceid={ceid}"
    return await fetch_rss_items([
        "https://www.espncricinfo.com/rss/content/story/feeds/0.xml",
        gnews,
    ], limit=5)


async def fetch_news_context(query: str, lang: str = "UR") -> List[str]:
    toks = re.findall(r"[\w\u0600-\u06FF\u0900-\u097F]+", (query or "").lower())
    topic_toks = [t for t in toks if t not in NEWS_STOPWORDS and len(t) >= 2]
    topic = " ".join(topic_toks).strip()
    is_urdu = lang == "UR" or bool(re.search(r"[\u0600-\u06FF]", query or ""))
    feeds: List[str] = []
    if topic:
        hl, gl, ceid = ("ur", "IN", "IN:ur") if is_urdu else ("en-IN", "IN", "IN:en")
        feeds.append(f"https://news.google.com/rss/search?q={urllib.parse.quote(topic)}&hl={hl}&gl={gl}&ceid={ceid}")
    if is_urdu:
        feeds.extend([
            "https://feeds.bbci.co.uk/urdu/rss.xml",
            "https://news.google.com/rss?hl=ur&gl=IN&ceid=IN:ur",
        ])
    else:
        feeds.extend([
            "https://feeds.bbci.co.uk/news/world/asia/rss.xml",
            "https://news.google.com/rss?hl=en-IN&gl=IN&ceid=IN:en",
            "https://www.aljazeera.com/xml/rss/all.xml",
        ])
    return await fetch_rss_items(feeds, limit=6)


def _weather_code_desc(code: Optional[int], lang: str) -> str:
    if code is None:
        return ""
    if code == 0:
        return "آسمان صاف ہے" if lang == "UR" else "clear skies"
    if code in (1, 2, 3):
        return "آسمان پر بادل چھائے ہوئے ہیں" if lang == "UR" else "partly cloudy skies"
    if code in (45, 48):
        return "دھند چھائی ہوئی ہے" if lang == "UR" else "foggy conditions"
    if code in (51, 53, 55, 61, 63, 65, 80, 81, 82):
        return "بارش ہو رہی ہے" if lang == "UR" else "rain"
    if code in (71, 73, 75, 77, 85, 86):
        return "برف باری ہو رہی ہے" if lang == "UR" else "snowfall"
    if code in (95, 96, 99):
        return "گرج چمک کے ساتھ بارش ہے" if lang == "UR" else "thunderstorms"
    return ""


async def fetch_weather_info(query: str) -> Tuple[str, Optional[float], Optional[float], Optional[int]]:
    lat, lon, place = 34.0837, 74.7973, "Srinagar"
    low_q = (query or "").lower()
    matched_local = False

    if not has_word(low_q, ["bulgaria", "بلغاریہ"]):
        for aliases, pname, plat, plon in KASHMIR_WEATHER_PLACES:
            if has_word(low_q, aliases):
                place, lat, lon = pname, plat, plon
                matched_local = True
                break

    if not matched_local:
        cand = ""
        m_ur = re.search(
            r"([a-zA-Z\u0600-\u06FF\u0900-\u097F]{3,20}(?:\s+[a-zA-Z\u0600-\u06FF\u0900-\u097F]{3,20})?)\s+(?:ka|ki|ke|mein|me|کا|کی|کے|میں|का|की|के)\s+(?:mausam|mosam|weather|temperature|barish|baarish|thand|sardi|garmi|موسم|بارش|मौसम)",
            query,
            flags=re.I,
        )
        if m_ur:
            cand = m_ur.group(1).strip()
        else:
            m_en = re.search(r"\b(?:in|at|of|for)\s+([a-zA-Z]{3,20}(?:\s+[a-zA-Z]{3,20})?)\b", query, flags=re.I)
            if m_en:
                cand = m_en.group(1).strip()

        if cand:
            cand = " ".join(w for w in cand.split() if w.lower() not in WEATHER_STOPWORDS).strip()

        if cand:
            try:
                async with httpx.AsyncClient(timeout=6.0) as client:
                    g = await client.get(
                        f"https://geocoding-api.open-meteo.com/v1/search?name={urllib.parse.quote(cand)}&count=1"
                    )
                    if g.status_code == 200 and g.json().get("results"):
                        res = g.json()["results"][0]
                        lat, lon, place = res["latitude"], res["longitude"], res["name"]
            except Exception as ex:
                print(f"Geocoding error: {ex}")

    url = (
        f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
        "&current=temperature_2m,wind_speed_10m,weather_code&timezone=Asia%2FKolkata"
    )
    for _ in range(2):
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                r = await client.get(url)
                if r.status_code == 200:
                    cur = r.json().get("current", {})
                    return place, cur.get("temperature_2m"), cur.get("wind_speed_10m"), cur.get("weather_code")
        except Exception as ex:
            print(f"Weather error: {ex}")
        await asyncio.sleep(0.3)
    return place, None, None, None


async def call_groq_bilingual(system_prompt: str, user_prompt: str, lang: str, length_mode: str = "normal") -> Optional[Tuple[str, str]]:
    if not RAW_KEYS:
        return None

    history_ctx = format_history_for_prompt()

    if length_mode == "brief":
        length_rule = "LENGTH MODE: BRIEF. Give a short, crisp, direct answer in 1 to 2 sentences."
        max_tokens = 360
    elif length_mode == "chapter":
        length_rule = (
            "CHAPTER MODE: Create a clear, original, carefully bounded lesson narration in 12 to 16 sentences. "
            "Cover the essential historical setting, chronological events, key people, outcomes, and lessons. "
            "Do not claim verbatim quotation or invent unsupported details. Keep the response concise for a voice device."
        )
        max_tokens = 1300
    elif length_mode == "long":
        length_rule = "LENGTH MODE: DETAILED. Give a thorough, clear, well-structured answer in 6 to 8 natural sentences."
        max_tokens = 1500
    else:
        length_rule = "LENGTH MODE: NORMAL. Give a direct, clear, accurate answer in 3 to 5 natural sentences."
        max_tokens = 950

    if lang == "UR":
        format_rule = (
            "STRICT LANGUAGE LOCK: User spoke in URDU.\n"
            "Write natural, pure, everyday spoken Urdu — STRICTLY FORBIDDEN to use Hindi/Sanskrit words like 'mutra', 'uday', 'swad', 'turant', 'taapman', 'lagbhag', 'prakash'.\n"
            "NEVER invent fake facts, fake grammar definitions, or fake religious rulings.\n"
            "NEVER write colon verse references like '2:43' or English acronyms like 'IST' in VOICE.\n"
            "Reply using EXACTLY these two plain lines with NO markdown stars, NO intro filler, and NO extra text:\n"
            "DISPLAY: <Clean, accurate answer in Roman Urdu using A-Z and 0-9 only; NO Urdu script, NO DISPLAY/VOICE words>\n"
            "VOICE: <Exact same clean, accurate answer in natural Urdu script (اردو) with proper punctuation (۔); NO Arabic harakat, NO DISPLAY/VOICE words>\n"
            f"{length_rule}\n"
            "Always resolve follow-ups using Conversation History."
        )
    else:
        format_rule = (
            "STRICT LANGUAGE LOCK: User spoke in ENGLISH.\n"
            "Use SIMPLE, CLEAR, everyday Indian English that sounds natural when spoken aloud. "
            "Avoid jargon, markdown symbols, or filler.\n"
            f"{length_rule}\n"
            "Answer directly and accurately, using Conversation History for follow-ups."
        )

    full_sys = system_prompt + "\n\n" + history_ctx + format_rule

    for key in RAW_KEYS:
        models = await get_available_groq_models(key)
        for model in models:
            try:
                tok_limit = max(max_tokens, 1800) if ("qwen" in model.lower() or "oss" in model.lower()) else max_tokens
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
                            "max_tokens": tok_limit,
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
                                    if len(re.sub(r"[^A-Za-z]", "", disp)) < 3:
                                        disp = urdu_to_roman_fallback(voice)
                                    return disp, voice
                            voice_only = clean_urdu_for_tts(clean_voice_script(raw_clean_md))
                            ur_chars = len(re.findall(r"[\u0600-\u06FF]", voice_only))
                            meaningful_words = re.findall(r"[A-Za-z]{2,}", voice_only)
                            if ur_chars >= 3 or len(meaningful_words) >= 4:
                                disp_fb = urdu_to_roman_fallback(voice_only) if ur_chars >= 3 else clean_ascii(voice_only)
                                if len(re.sub(r"[^A-Za-z]", "", disp_fb)) >= 3:
                                    return disp_fb, voice_only
                            continue
                        else:
                            en_clean = clean_ascii(raw_clean_md)
                            if en_clean:
                                return en_clean, en_clean

                    print(f"Groq {model} ({resp.status_code}): {resp.text[:160]}")
            except Exception as ex:
                print(f"Groq {model} exception: {ex}")
    return None


async def wiki_fallback(query: str, lang: str = "EN") -> Tuple[str, str]:
    """Use language-matched Wikipedia; never send English output to an Urdu voice."""
    is_urdu = lang == "UR"
    raw_q = (query or "").strip()
    if is_urdu and not re.search(r"[\u0600-\u06FF]", raw_q):
        disp = "Abhi AI server masroof hai. Thodi der baad dobara koshish karein."
        msg = "ابھی اے آئی سرور مصروف ہے۔ تھوڑی دیر بعد دوبارہ کوشش کریں۔"
        return disp, msg

    clean_q = re.sub(
        r"\b(kya|hai|hain|he|ka|ki|ke|what|is|are|who|tell|me|about|batao|bataiye|sunao|maloomat)\b",
        "",
        raw_q,
        flags=re.I,
    ).strip()
    if not clean_q:
        clean_q = raw_q

    lang_code = "ur" if is_urdu else "en"
    headers = {"User-Agent": "ESP32VoiceAssistant/2.3"}
    try:
        async with httpx.AsyncClient(timeout=6.0, headers=headers) as client:
            s_url = (
                f"https://{lang_code}.wikipedia.org/w/api.php?action=query&list=search&srlimit=1&format=json&srsearch="
                + urllib.parse.quote(clean_q[:80])
            )
            sr = await client.get(s_url)
            title = clean_q
            if sr.status_code == 200:
                hits = sr.json().get("query", {}).get("search", [])
                if hits:
                    title = hits[0].get("title", clean_q)
            url = f"https://{lang_code}.wikipedia.org/api/rest_v1/page/summary/{urllib.parse.quote(title)}"
            r = await client.get(url)
            if r.status_code == 200:
                extract = (r.json().get("extract") or "").strip()
                if extract:
                    sents = re.split(r"(?<=[.!?؟۔])\s+", extract)
                    raw = "۔ ".join(sents[:3]) if is_urdu else " ".join(sents[:3])
                    if is_urdu:
                        return urdu_to_roman_fallback(raw)[:360], raw[:700]
                    ans = clean_ascii(raw)[:360]
                    return ans, ans
    except Exception as ex:
        print(f"Wiki fallback error: {type(ex).__name__}: {ex}")

    if is_urdu:
        disp = "Abhi AI server masroof hai. Thodi der baad dobara koshish karein."
        msg = "ابھی اے آئی سرور مصروف ہے۔ تھوڑی دیر بعد دوبارہ کوشش کریں۔"
        return disp, msg
    msg = "The information service is temporarily unavailable. Please try again shortly."
    return msg, msg


async def transcribe_wav(wav_bytes: bytes) -> str:
    if not RAW_KEYS:
        return ""
    hint_prompt = (
        "Shopian ka mausam, Pulwama, Srinagar, Namaz timing batao, Aaj ki taza khabrein sunao, "
        "Suratul Kahf tilawat sunao, Surah Yaseen, Surah Baqarah, Surah Rahman, Surah Mulk, "
        "Surah 45 ayat 20 tarjuma aur tafseer, Tafheem-ul-Quran, Maulana Maududi, "
        "Seerat un Nabi Ar-Raheeq Al-Makhtum, Ghazwa e Badr, Hijrat e Madina, "
        "Sajda e Sahw ka tareeqa, Wuzu kin cheezon se toot jata hai, Af'al-e-Naqisa, Asma-e-Khamsa, "
        "Bal-e-Jibril, Classical Conditioning, Python inheritance, Cricket live score."
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
    voice_code = "EN-M" if voice_code == "EN-M" else "UR-M"
    fid = fixed_fid or uuid.uuid4().hex[:10]
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", fid):
        return "", 2200
    out_path = os.path.join(AUDIO_DIR, f"{fid}.mp3")

    if STATE["speed"] == "Norm":
        rate = "-6%" if voice_code == "EN-M" else "-4%"
    else:
        rate = SPEED_MAP.get(STATE["speed"], "+0%")

    if voice_code == "EN-M":
        clean_text = clean_english_for_tts(text) or "Please ask again."
    else:
        clean_text = clean_urdu_for_tts(clean_voice_script(text), keep_clock_colons=keep_clock_colons) or "جی بتائیے۔"

    audio_bytes, last_end_ms, selected_voice = b"", 0, ""
    candidates = VOICE_CANDIDATES.get(voice_code, [])
    for voice in candidates:
        for attempt in range(1, 5):
            try:
                candidate_bytes, candidate_end_ms = await _run_edge_tts_once(clean_text, voice, rate)
                if len(candidate_bytes) >= 800:
                    audio_bytes, last_end_ms, selected_voice = candidate_bytes, candidate_end_ms, voice
                    break
            except Exception as ex:
                print(f"TTS attempt {attempt} ({voice}) failed: {type(ex).__name__}: {ex}")
            await asyncio.sleep(0.25)
        if audio_bytes:
            break

    if len(audio_bytes) < 800:
        print(f"TTS unavailable for {voice_code}; all retries failed.")
        return "", max(2200, len(clean_text) * 55)

    try:
        with open(out_path, "wb") as f:
            f.write(audio_bytes)
    except OSError as ex:
        print(f"TTS file write failed: {type(ex).__name__}: {ex}")
        return "", max(2200, len(clean_text) * 55)

    exact_mp3_ms = get_mp3_duration_ms(audio_bytes)
    fallback_ms = int((len(audio_bytes) / 6000.0) * 1000)
    total_ms = max(1200, exact_mp3_ms, last_end_ms, fallback_ms) + 400
    print(f"TTS [locked] voice={selected_voice} rate={rate} bytes={len(audio_bytes)} mp3_ms={exact_mp3_ms} final_ms={total_ms}")

    now_t = time.time()
    try:
        for fn in os.listdir(AUDIO_DIR):
            if fn.startswith("seerat_"):
                continue
            fp = os.path.join(AUDIO_DIR, fn)
            try:
                if os.path.isfile(fp) and now_t - os.path.getmtime(fp) > AUDIO_TTL_SEC:
                    os.remove(fp)
            except OSError:
                pass
    except OSError as ex:
        print(f"Audio cleanup skipped: {type(ex).__name__}: {ex}")
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
        ms = max(750, int(round(total_ms * (w / w_sum))))
        pages.append({"t": c, "ms": ms})
    return pages


def _public_base_url(request: Request) -> str:
    base = str(request.base_url).rstrip("/")
    if "onrender.com" in base and base.startswith("http://"):
        base = "https://" + base[len("http://"):]
    return base or "https://esp32-ai-voice.onrender.com"


def urdu_place_name(place: str) -> str:
    aliases = {
        "shopian": "شوپیاں", "shopiyan": "شوپیاں", "pinjoora, shopian": "پنجورہ، شوپیاں", "pinjoora": "پنجورہ",
        "srinagar": "سرینگر", "pulwama": "پلوامہ", "anantnag": "اننت ناگ", "kulgam": "کلگام",
        "baramulla": "بارہمولہ", "budgam": "بڈگام", "kupwara": "کپواڑہ", "bandipora": "بانڈی پورہ",
        "ganderbal": "گاندربل", "pahalgam": "پہلگام", "gulmarg": "گلمرگ", "jammu": "جموں", "kashmir": "کشمیر",
    }
    return aliases.get((place or "").strip().casefold(), place)


def headline_fallback(headlines: List[str], lang: str) -> Tuple[str, str]:
    raw = "۔ ".join(x.strip() for x in headlines[:2] if x.strip())
    if lang == "UR":
        return urdu_to_roman_fallback(raw)[:300], raw[:700]
    clean = clean_ascii(raw)[:300]
    return clean, clean


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
            tts = f"ابھی {period_ur}۔ آج تاریخ {format_urdu_gregorian_date(now)} ہے، اور ہجری تاریخ {hijri_ur} ہے۔"
            return "TIME", disp, tts, True
        ans = f"It is {t_12} on {d_en}. Hijri date is {hijri_en}."
        return "TIME", ans, ans, True

    if is_weather_query(heard):
        place, temp, wind, wcode = await fetch_weather_info(heard)
        if temp is not None:
            cond_ur = _weather_code_desc(wcode, "UR")
            cond_en = _weather_code_desc(wcode, "EN")
            if lang == "UR":
                extra_d = f" ({cond_en})" if cond_en else ""
                extra_t = f" اور {cond_ur}" if cond_ur else ""
                disp = f"{place}: {temp} C, hawa {wind} kmh{extra_d}."
                tts = f"{urdu_place_name(place)} میں ابھی درجہ حرارت {temp} ڈگری سینٹی گریڈ، ہَوا کی رفتار {wind} کلومیٹر فی گھنٹہ{extra_t} ہے۔"
                return "WEATHER", disp, tts, False
            extra_en = f" with {cond_en}" if cond_en else ""
            ans = f"In {place}, it is currently {temp} degrees Celsius with wind at {wind} kilometers per hour{extra_en}."
            return "WEATHER", ans, ans, False
        if lang == "UR":
            return "WEATHER", f"{place} ka live mausam abhi load nahi ho saka.", f"{urdu_place_name(place)} کا لائیو موسم ابھی لوڈ نہیں ہو سکا۔ تھوڑی دیر بعد دوبارہ کوشش کریں۔", False
        return "WEATHER", f"Could not fetch live weather for {place} right now.", f"Could not fetch live weather for {place} right now. Please try again shortly.", False

    if is_cricket_query(heard):
        headlines = await fetch_cricket_context(heard, lang)
        if headlines:
            sys_p = (
                f"You are a clear, direct live cricket presenter. Today is {now.strftime('%d %b %Y, %I:%M %p')}. "
                "Report ONLY verified facts from the live cricket headlines below in 2 to 3 simple, clear sentences."
            )
            usr_p = f"User question: {heard}\nLive Cricket Feeds:\n" + "\n".join(f"- {h}" for h in headlines)
            res = await call_groq_bilingual(sys_p, usr_p, lang, length_mode)
            if res:
                return "CRICKET", res[0], res[1], False
            fb, fb_tts = headline_fallback(headlines, lang)
            return "CRICKET", fb, fb_tts, False
        if lang == "UR":
            fail_d = "Cricket ki taza feed abhi dastiyab nahi; score verify nahi ho saka."
            fail_t = "کرکٹ کی تازہ فیڈ ابھی دستیاب نہیں، اس لیے میں اسکور کی تصدیق نہیں کر سکا۔ تھوڑی دیر بعد دوبارہ کوشش کریں۔"
        else:
            fail_d = "The live cricket feed is unavailable, so I could not verify the current score."
            fail_t = fail_d + " Please try again shortly."
        return "CRICKET", fail_d, fail_t, False

    if is_news_query(heard):
        headlines = await fetch_news_context(heard, lang)
        if headlines:
            sys_p = (
                f"You are a clear, crisp Urdu/English broadcast news anchor. Today is {now.strftime('%d %b %Y, %I:%M %p')}. "
                "Pick ONLY the 3 clearest, biggest, most important headlines from the list below (prefer major national, Kashmir, or world news; skip obscure local court/party names that are hard to pronounce). "
                "Present them in 3 short, smooth, easy-to-understand sentences."
            )
            usr_p = f"User asked: {heard}\nLive Headlines:\n" + "\n".join(f"- {h}" for h in headlines)
            res = await call_groq_bilingual(sys_p, usr_p, lang, "normal")
            if res:
                return "NEWS", res[0], res[1], False
            fb, fb_tts = headline_fallback(headlines, lang)
            return "NEWS", fb, fb_tts, False
        if lang == "UR":
            fail_d = "Taza khabron ki feed abhi load nahi hui; main ghair-tasdeeq-shuda khabar nahi bataunga."
            fail_t = "تازہ خبروں کی فیڈ ابھی لوڈ نہیں ہوئی، اس لیے میں غیر مصدقہ خبر نہیں بتاؤں گا۔ تھوڑی دیر بعد دوبارہ کوشش کریں۔"
        else:
            fail_d = "The live news feed did not load, so I will not guess or present unverified headlines."
            fail_t = fail_d + " Please try again shortly."
        return "NEWS", fail_d, fail_t, False

    # Zero-hallucination core Hanafi Masayil & Arabic Grammar check
    verified = get_verified_core_answer(heard, lang)
    if verified:
        return verified[0], verified[1], verified[2], False

    if is_islamic_masayil_query(heard):
        sys_p = (
            f"You are an authentic, respectful, deeply knowledgeable Sunni Hanafi Islamic scholar (Aalim) based in Kashmir, India. "
            f"Today is {now.strftime('%d %B %Y')} (Hijri: {hijri_en}).\n"
            "STRICT RULES FOR ISLAMIC MASAYIL & FULL ISLAMIC KNOWLEDGE:\n"
            "1) Answer strictly from authentic Quran, Sahih Sunnah, and established Sunni Hanafi Fiqh clearly and accurately.\n"
            "2) NEVER invent rulings, NEVER use Hindi words (like mutra, uday, swad), and NEVER guess Quran verse numbers or Hadith numbers.\n"
            "3) Start directly with the exact ruling or answer in clean, clear, natural language."
        )
        res = await call_groq_bilingual(sys_p, heard, lang, length_mode)
        if res:
            hdr = "MASAYIL" if has_word(heard, ["masla", "masail", "masayil", "wuzu", "ghusl", "namaz", "roza", "zakat", "talaq", "nikah", "halal", "haram", "jaiz", "farz", "wajib", "sunnat", "sajda", "qaza", "مسئلہ", "مسائل", "وضو", "نماز", "روزہ", "زکوۃ", "طلاق"]) else "ISLAM"
            return hdr, res[0], res[1], False

    sys_p = (
        f"You are a highly intelligent, accurate, warm all-knowledge voice peer based in Kashmir, India. "
        f"Today is {now.strftime('%A, %d %B %Y, %I:%M %p')} (Hijri: {hijri_en}).\n"
        "Answer ANY question accurately across every field: Arabic grammar (Nahw & Sarf like Af'al-e-Naqisa, Asma-e-Khamsa, I'rab), "
        "Urdu literature & Allama Iqbal's poetry (Bal-e-Jibril), science, math, Python coding, electronics/ESP32, education/psychology, history, geography, and daily life.\n"
        "Core rules:\n"
        "1) Understand technical, academic, and Islamic terms by their TRUE scholarly meaning — NEVER guess literally from word sounds.\n"
        "2) Start directly with the exact answer in sentence 1. Zero robotic filler, zero 'As an AI', zero hallucination.\n"
        "3) Speak in crisp, natural, easy-to-follow spoken language."
    )
    res = await call_groq_bilingual(sys_p, heard, lang, length_mode)
    if res:
        return "ANSWER", res[0], res[1], False

    disp, tts = await wiki_fallback(heard, lang)
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
    if not (
        re.fullmatch(r"[a-fA-F0-9]{8,32}", fid)
        or re.fullmatch(r"seerat_ch(?:0[1-9]|1[0-9]|20)_(?:ur|en)", fid)
        or re.fullmatch(r"seerat_v2_ch(?:0[1-9]|1[0-9]|20)_(?:ur|en)", fid)
        or re.fullmatch(r"seerat_v3_ch(?:0[1-9]|1[0-9]|20)_(?:ur|en)", fid)
    ):
        return JSONResponse({"error": "not found"}, status_code=404)
    fp = os.path.join(AUDIO_DIR, f"{fid}.mp3")
    if os.path.exists(fp) and os.path.getsize(fp) > 200:
        return FileResponse(fp, media_type="audio/mpeg", filename=f"{fid}.mp3")
    return JSONResponse({"error": "not found"}, status_code=404)


@app.post("/chat")
async def chat(request: Request):
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > 15 * 1024 * 1024:
                return JSONResponse({"error": "Audio upload too large (15 MB maximum)."}, status_code=413)
        except ValueError:
            return JSONResponse({"error": "Invalid Content-Length header."}, status_code=400)
    wav_bytes = await request.body()
    if not wav_bytes:
        return JSONResponse({"error": "Empty audio upload."}, status_code=400)
    if len(wav_bytes) > 15 * 1024 * 1024:
        return JSONResponse({"error": "Audio upload too large (15 MB maximum)."}, status_code=413)
    try:
        heard = await transcribe_wav(wav_bytes)
    except Exception as exc:
        print(f"Transcription failed: {type(exc).__name__}: {exc}")
        return JSONResponse({"error": "Speech recognition is temporarily unavailable. Please try again."}, status_code=503)
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
        msg_d = f"Volume {STATE['volume']} chalu ho gaya." if lang == "UR" else f"Unmuted. Volume {STATE['volume']}."
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
        msg_d = "Purani chat history saaf ho gayi." if lang == "UR" else "Chat history cleared."
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
        volume_text = replace_number_words(low)
        m_vol = re.search(
            r"(?:volume|awaaz|آواز)(?:\s+(?:ko|set|to|at|on|par|pe|karo|krdo|kardo|rakh|rakho)){0,3}\s+(\d{1,2})\b",
            volume_text,
        )
        if m_vol:
            STATE["volume"] = max(2, min(16, int(m_vol.group(1))))
            STATE["last_nonzero_volume"] = STATE["volume"]
            msg_d = f"Volume {STATE['volume']} set ho gaya." if lang == "UR" else f"Volume set to {STATE['volume']}."
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
        msg_d = f"Volume badha kar {STATE['volume']} kar diya." if lang == "UR" else f"Volume increased to {STATE['volume']}."
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
        msg_d = f"Volume kam kar ke {STATE['volume']} kar diya." if lang == "UR" else f"Volume decreased to {STATE['volume']}."
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
        msg_d = "Speed slow set ho gayi." if lang == "UR" else "Speaking speed set to slow."
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
        msg_d = "Speed fast set ho gayi." if lang == "UR" else "Speaking speed set to fast."
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
        msg_d = "Speed normal set ho gayi." if lang == "UR" else "Speaking speed set to normal."
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
            msg_d = "Monster face band ho gaya." if lang == "UR" else "Monster face turned off."
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
            msg_d = "Monster face chalu ho gaya." if lang == "UR" else "Monster face turned on."
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
        if script_txt.startswith("__SEERAT_ERROR__:"):
            error_text = script_txt.split(":", 1)[1]
            error_audio, error_ms = await make_tts_with_duration(error_text, s_voice)
            disp_err = clean_ascii(error_text) if s_lang == "EN" else urdu_to_roman_fallback(error_text)
            return {
                "action": "SPEAK",
                "lang": s_lang,
                "header": "SEERAT ERROR",
                "heard": clean_ascii(heard_norm)[:16],
                "display": disp_err,
                "pages": build_synced_pages(disp_err, error_ms, s_lang),
                "audio_url": error_audio,
                "voice": s_voice,
                "volume": STATE["volume"],
                "monster": STATE["monster"],
            }
        if not audio_rel:
            disp_no_audio = (
                "Seerat audio could not be prepared; please retry."
                if s_lang == "EN"
                else "Seerat audio tayyar nahi ho saki, dobara koshish karein."
            )
            return {
                "action": "SPEAK",
                "lang": s_lang,
                "header": "SEERAT AUDIO",
                "heard": clean_ascii(heard_norm)[:16],
                "display": disp_no_audio,
                "pages": build_synced_pages(disp_no_audio, 2500, s_lang),
                "audio_url": "",
                "voice": s_voice,
                "volume": STATE["volume"],
                "monster": STATE["monster"],
            }
        append_history({
            "u": heard_norm,
            "a": f"Ar-Raheeq study narration chapter {ch_num} ({en_title})",
            "surah_num": None,
            "last_ayah": None,
            "seerat_chapter": ch_num,
        })
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

    # 10) ISLAMIC MASAYIL + ALL-ROUNDER SMART AI ASSISTANT (Strictly UR-M or EN-M)
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
