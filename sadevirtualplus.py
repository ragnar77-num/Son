# -*- coding: utf-8 -*-
# ════════════════════════ AYARLAR (yalnız bu bloku dəyişin) ════════════════════════
BOT_TOKEN = "9220088363:AAEbbFmhmxHwapEsNd1hOcMuzy47E3R1SOc"
ADMIN_ID = 8813958289                      # sahib (həmişə admin, çıxarıla bilməz)
PANELPLUS_URL = "https://panelplus.onrender.com/api/v2"
PANELPLUS_API_KEY = "pp_SRwNcHN58ZNLkFtd6Tt_pdxbIgFca3uinNQ32JhN"
SMS_MARKUP_AZN = 0.50                    # hər nömrəyə sabit əlavə (AZN)
SMS_TIMEOUT_MIN = 3                        # SMS gözləmə müddəti (dəqiqə)
TOPUP_CONTACTS = ["@RagnarOwner", "@CrashAndMee"]   # "Balans Artır" üçün əlaqə
MAX_ACTIVE_PER_USER = 7                   # eyni anda aktiv nömrə limiti (adminlərə aid deyil)
POLL_SECONDS = 6                           # sms_status yoxlama aralığı
REQUIRED_CHANNEL = "@Yetinir"              # məcburi kanal (bot kanalda ADMİN olmalıdır)
REFERRAL_BONUS_AZN = 0.10                  # hər dəvət olunan adam üçün (AZN)
DAILY_BONUS_AZN = 0.10                     # gündəlik bonus (AZN)
DB_PATH = "bot.db"                         # Render-də Disk qoşsan: "/data/bot.db"
LOG_PATH = "bot.log"
# ═══════════════════════════════════════════════════════════════════════════════════
# Yalnız Python standart kitabxanası istifadə olunur (aiogram / aiosqlite / requests YOXDUR).

import difflib
import html
import json
import logging
import os
import re
import sqlite3
import tempfile
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal, InvalidOperation

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s",
                    handlers=[logging.FileHandler(LOG_PATH, encoding="utf-8"), logging.StreamHandler()])
log = logging.getLogger("numbot")

AZT = timezone(timedelta(hours=4))
START_TIME = time.time()
ACTIVE = ("BUYING", "WAITING", "CHECKING", "UNCERTAIN")
ICON = {"BUYING": "🛒", "WAITING": "⏳", "CHECKING": "🔎", "DONE": "✅", "CANCELED": "❌",
        "FAILED": "⚠️", "UNCERTAIN": "❓"}
UNAVAILABLE = "⚠️ Xidmət müvəqqəti əlçatmazdır. Bir az sonra yenidən cəhd edin."
BTN_BUY, BTN_BAL, BTN_TOPUP, BTN_ADMIN = "📱 Nömrə Al", "💰 Balansım", "➕ Balans Artır", "🛠 Admin Panel"
BTN_BONUS, BTN_REF = "🎁 Bonus", "👥 Referans Sistemi"
BOT_USERNAME = ""
TG_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/"


# ═════════════════════════════ KÖMƏKÇİ FUNKSİYALAR ═════════════════════════════
class InsufficientFunds(Exception):
    pass


class ApiError(Exception):
    """PanelPlus {"error": ...} qaytardı — əməliyyat icra olunmayıb (qəti)."""


class ApiUncertain(Exception):
    """Timeout / şəbəkə / JSON olmayan cavab — nəticə naməlum."""


class TgError(Exception):
    pass


def esc(x):
    return html.escape(str("" if x is None else x))


def now():
    return int(time.time())


def fmt_ts(ts):
    return datetime.fromtimestamp(ts, AZT).strftime("%d.%m.%Y %H:%M") if ts else "—"


def day_start():
    return int(datetime.now(AZT).replace(hour=0, minute=0, second=0, microsecond=0).timestamp())


def to_qep(x, ceil=False):
    """Məbləği qəpiyə (tam ədəd) çevirir. Float istifadə olunmur."""
    try:
        d = Decimal(str(x).replace(",", ".").strip())
    except (InvalidOperation, ValueError):
        return None
    if not d.is_finite():
        return None
    return int((d * 100).to_integral_value(ROUND_CEILING if ceil else ROUND_HALF_UP))


def fmt_qep(n):
    sign = "-" if n < 0 else ""
    n = abs(int(n))
    return f"{sign}{n // 100}.{n % 100:02d}"


def money(n):
    return f"{fmt_qep(n)} ₼"


def raw_price(x):
    return format(Decimal(str(x)), "f")


def fold(s):
    s = str(s or "").replace("İ", "i").replace("ı", "i").replace("ə", "e").replace("Ə", "e")
    s = unicodedata.normalize("NFKD", s.lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def plus(n):
    n = str(n or "").strip()
    return "+" + n if n.isdigit() else n


def mmss(sec):
    sec = max(0, int(sec))
    return f"{sec // 60:02d}:{sec % 60:02d}"


def flag(iso):
    iso = (iso or "").upper()
    if len(iso) == 2 and iso.isalpha():
        return "".join(chr(0x1F1E6 + ord(c) - 65) for c in iso)
    return "🌍"


def kb(rows):
    """Inline klaviatura (dict). 'url:' ilə başlayan data = link düyməsi."""
    out = []
    for row in rows:
        r = []
        for text, data in row:
            if data.startswith("url:"):
                r.append({"text": text, "url": data[4:]})
            else:
                r.append({"text": text, "callback_data": data})
        out.append(r)
    return {"inline_keyboard": out}


def spawn(fn, *args):
    t = threading.Thread(target=fn, args=args, daemon=True)
    t.start()
    return t


# ══════════════════════════════ ÖLKƏ / SERVİS AXTARIŞI ══════════════════════════════
# Bütün ölkələr (ISO kodu → bayraq avtomatik yaranır)
COUNTRY_DATA = """AF Afghanistan
AX Aland Islands
AL Albania
DZ Algeria
AS American Samoa
AD Andorra
AO Angola
AI Anguilla
AQ Antarctica
AG Antigua and Barbuda
AR Argentina
AM Armenia
AW Aruba
AU Australia
AT Austria
AZ Azerbaijan
BS Bahamas
BH Bahrain
BD Bangladesh
BB Barbados
BY Belarus
BE Belgium
BZ Belize
BJ Benin
BM Bermuda
BT Bhutan
BO Bolivia
BQ Bonaire
BA Bosnia and Herzegovina
BW Botswana
BR Brazil
IO British Indian Ocean Territory
BN Brunei
BG Bulgaria
BF Burkina Faso
BI Burundi
CV Cape Verde
KH Cambodia
CM Cameroon
CA Canada
KY Cayman Islands
CF Central African Republic
TD Chad
CL Chile
CN China
CX Christmas Island
CC Cocos Islands
CO Colombia
KM Comoros
CG Congo
CD DR Congo
CK Cook Islands
CR Costa Rica
CI Ivory Coast
HR Croatia
CU Cuba
CW Curacao
CY Cyprus
CZ Czech Republic
DK Denmark
DJ Djibouti
DM Dominica
DO Dominican Republic
EC Ecuador
EG Egypt
SV El Salvador
GQ Equatorial Guinea
ER Eritrea
EE Estonia
SZ Eswatini
ET Ethiopia
FK Falkland Islands
FO Faroe Islands
FJ Fiji
FI Finland
FR France
GF French Guiana
PF French Polynesia
GA Gabon
GM Gambia
GE Georgia
DE Germany
GH Ghana
GI Gibraltar
GR Greece
GL Greenland
GD Grenada
GP Guadeloupe
GU Guam
GT Guatemala
GG Guernsey
GN Guinea
GW Guinea-Bissau
GY Guyana
HT Haiti
VA Vatican
HN Honduras
HK Hong Kong
HU Hungary
IS Iceland
IN India
ID Indonesia
IR Iran
IQ Iraq
IE Ireland
IM Isle of Man
IL Israel
IT Italy
JM Jamaica
JP Japan
JE Jersey
JO Jordan
KZ Kazakhstan
KE Kenya
KI Kiribati
KP North Korea
KR South Korea
XK Kosovo
KW Kuwait
KG Kyrgyzstan
LA Laos
LV Latvia
LB Lebanon
LS Lesotho
LR Liberia
LY Libya
LI Liechtenstein
LT Lithuania
LU Luxembourg
MO Macao
MG Madagascar
MW Malawi
MY Malaysia
MV Maldives
ML Mali
MT Malta
MH Marshall Islands
MQ Martinique
MR Mauritania
MU Mauritius
YT Mayotte
MX Mexico
FM Micronesia
MD Moldova
MC Monaco
MN Mongolia
ME Montenegro
MS Montserrat
MA Morocco
MZ Mozambique
MM Myanmar
NA Namibia
NR Nauru
NP Nepal
NL Netherlands
NC New Caledonia
NZ New Zealand
NI Nicaragua
NE Niger
NG Nigeria
NU Niue
NF Norfolk Island
MK North Macedonia
MP Northern Mariana Islands
NO Norway
OM Oman
PK Pakistan
PW Palau
PS Palestine
PA Panama
PG Papua New Guinea
PY Paraguay
PE Peru
PH Philippines
PN Pitcairn
PL Poland
PT Portugal
PR Puerto Rico
QA Qatar
RE Reunion
RO Romania
RU Russia
RW Rwanda
BL Saint Barthelemy
SH Saint Helena
KN Saint Kitts and Nevis
LC Saint Lucia
MF Saint Martin
PM Saint Pierre and Miquelon
VC Saint Vincent and the Grenadines
WS Samoa
SM San Marino
ST Sao Tome and Principe
SA Saudi Arabia
SN Senegal
RS Serbia
SC Seychelles
SL Sierra Leone
SG Singapore
SX Sint Maarten
SK Slovakia
SI Slovenia
SB Solomon Islands
SO Somalia
ZA South Africa
SS South Sudan
ES Spain
LK Sri Lanka
SD Sudan
SR Suriname
SJ Svalbard
SE Sweden
CH Switzerland
SY Syria
TW Taiwan
TJ Tajikistan
TZ Tanzania
TH Thailand
TL Timor-Leste
TG Togo
TK Tokelau
TO Tonga
TT Trinidad and Tobago
TN Tunisia
TR Turkey
TM Turkmenistan
TC Turks and Caicos
TV Tuvalu
UG Uganda
UA Ukraine
AE United Arab Emirates
GB United Kingdom
US United States
UY Uruguay
UZ Uzbekistan
VU Vanuatu
VE Venezuela
VN Vietnam
VG British Virgin Islands
VI US Virgin Islands
WF Wallis and Futuna
EH Western Sahara
YE Yemen
ZM Zambia
ZW Zimbabwe"""

# alternativ adlar (Azərbaycan / Türk / API-də rast gəlinən qısa adlar)
COUNTRY_ALT = {
    "AZ": "azerbaycan,azərbaycan,azeri", "TR": "turkiye,türkiyə,turk,turkei", "RU": "rusiya,rusya,russian federation",
    "UA": "ukrayna", "GE": "gurcustan,gürcüstan,gurcistan", "AM": "ermenistan,ermənistan", "KZ": "qazaxistan,kazakistan",
    "UZ": "ozbekistan,özbəkistan", "KG": "qirgizistan,kirgizistan,kyrgyz", "TJ": "tacikistan", "TM": "türkmənistan",
    "BY": "belarusiya,belarussia", "DE": "almaniya,almanya", "FR": "fransa", "GB": "ingiltere,ingiltərə,britaniya,uk,england,great britain",
    "US": "abs,abd,amerika,usa,america,united states of america", "CA": "kanada", "ES": "ispaniya,ispanya",
    "IT": "italiya,italya", "NL": "niderland,hollanda,holland", "PL": "polsa,polşa,polonya", "RO": "rumıniya,romanya",
    "BG": "bolqarıstan,bulgaristan", "GR": "yunanistan,yunanıstan", "PT": "portuqaliya,portekiz", "SE": "isvec,isveç",
    "IN": "hindistan", "CN": "cin,çin", "JP": "yaponiya,japonya", "ID": "indoneziya,endonezya", "VN": "vyetnam",
    "TH": "tailand", "MY": "malayziya,malezya", "PH": "filippin", "IQ": "irak", "SA": "seudiyye,səudiyyə,suudi arabistan",
    "AE": "bae,emiratlar,uae,dubai,emirates", "EG": "misir", "IL": "israil", "BR": "braziliya,brezilya", "MX": "meksika",
    "ZA": "cenubi afrika,cənubi afrika", "MA": "fas,mərakeş", "CY": "kipr,kibris", "LV": "latviya", "LT": "litva",
    "EE": "estoniya", "RS": "serbiya", "CZ": "czech,çexiya,cekya", "CH": "isvicre,isveçrə", "AT": "avstriya,avusturya",
    "BE": "belcika,belçika", "DK": "danimarka", "NO": "norvec,norveç", "FI": "finlandiya", "IE": "irlandiya",
    "HU": "macaristan,macarıstan", "KR": "korea,cenubi koreya,guney kore", "CI": "cote d ivoire", "CD": "congo dr,democratic republic of the congo",
    "MK": "macedonia,makedoniya", "BA": "bosnia,bosniya", "MM": "burma", "LA": "lao", "SY": "suriye,suriyya",
    "AF": "afganistan,əfqanıstan", "AU": "avstraliya,avustralya", "NZ": "yeni zelandiya", "SG": "sinqapur,singapur",
    "HK": "honkonq", "TW": "tayvan", "KE": "keniya", "ET": "efiopiya,etiyopya", "DZ": "cezayir,əlcəzair", "TN": "tunis",
    "LY": "liviya", "JO": "iordaniya,urdun", "LB": "liven,livan", "KW": "kuveyt", "QA": "katar", "OM": "uman",
    "BD": "banqladesh", "LK": "şri lanka", "MN": "monqolustan", "CU": "kuba", "CO": "kolumbiya", "VE": "venesuela",
    "CL": "cili,sili", "HR": "xorvatiya,hirvatistan", "SK": "slovakiya,slovakya", "SI": "sloveniya,slovenya",
    "AL": "albaniya,arnavutluk", "ME": "qaradag,karadag", "XK": "kosova", "IS": "islandiya,izlanda",
    "LU": "luksemburq,luksemburg", "VA": "vatikan", "PS": "fələstin,filistin", "NG": "nigeriya", "GH": "qana",
}
NAME2ISO, ISO_NAMES = {}, {}
for _ln in COUNTRY_DATA.splitlines():
    _iso, _nm = _ln.strip().split(" ", 1)
    _names = [_nm] + [a for a in COUNTRY_ALT.get(_iso, "").split(",") if a]
    ISO_NAMES[_iso] = sorted({fold(n) for n in _names})
    for _n in _names:
        NAME2ISO[fold(_n)] = _iso

SERVICE_ALIAS = {"wp": "whatsapp", "wa": "whatsapp", "whatsap": "whatsapp", "watsap": "whatsapp",
                 "tg": "telegram", "tik tok": "tiktok", "ig": "instagram", "insta": "instagram",
                 "fb": "facebook", "vk": "vkontakte"}


def iso_of_name(name):
    """Ölkə adından ISO tapır (dəqiq → fuzzy)."""
    f = fold(name)
    if not f:
        return ""
    if f in NAME2ISO:
        return NAME2ISO[f]
    m = difflib.get_close_matches(f, list(NAME2ISO), n=1, cutoff=0.86)
    return NAME2ISO[m[0]] if m else ""


def iso_for(code, cname, iso_map):
    i = (iso_map.get(code) or "").upper()
    return i if len(i) == 2 else iso_of_name(cname)


def to_int(x):
    try:
        return int(Decimal(str(x)))
    except (InvalidOperation, ValueError, TypeError):
        return None


def as_list(j):
    if isinstance(j, list):
        return [x for x in j if isinstance(x, dict)]
    if isinstance(j, dict):
        for k in ("data", "result", "items", "services", "countries", "prices"):
            if isinstance(j.get(k), list):
                return [x for x in j[k] if isinstance(x, dict)]
    return []


def norm_services(j):
    out = []
    for s in as_list(j):
        code = str(s.get("code") or "").strip()
        if code:
            out.append({"code": code, "name": str(s.get("name") or code)})
    return out


def norm_prices(j):
    out = []
    for p in as_list(j):
        code = str(p.get("country") or "").strip()
        cost = to_qep(p.get("price"), ceil=True)
        if not code or cost is None:
            continue
        out.append({"code": code, "cname": str(p.get("cname") or code), "raw": raw_price(p.get("price")),
                    "cost": cost, "count": to_int(p.get("count")) if p.get("count") is not None else None})
    return out


def norm_countries(j):
    out = []
    for c in as_list(j):
        code = str(c.get("country") or c.get("code") or "").strip()
        if code:
            out.append({"code": code, "cname": str(c.get("cname") or c.get("name") or code),
                        "iso": str(c.get("iso") or "").upper()})
    return out


def match_services(query, services):
    q = fold(query)
    q = SERVICE_ALIAS.get(q, q)
    blocked = set(get_list("blocked_services"))
    scored = []
    for s in services:
        n, c = fold(s["name"]), fold(s["code"])
        if n in blocked or c in blocked:
            continue
        if q in (n, c):
            sc = 3
        elif q and len(q) >= 2 and (q in n or n.startswith(q) or q in c):
            sc = 2
        elif q and difflib.SequenceMatcher(None, q, n).ratio() >= 0.78:
            sc = 1
        else:
            continue
        scored.append((-sc, n, s))
    scored.sort(key=lambda t: (t[0], t[1]))
    return [t[2] for t in scored]


def country_targets(query):
    """Sorğudan hədəf adlar toplusu (ISO + bütün alternativ adlar)."""
    q = fold(query)
    targets = {q} if q else set()
    iso = ""
    if len(q) == 2 and q.upper() in ISO_NAMES:
        iso = q.upper()
    else:
        iso = iso_of_name(q)
    if iso:
        targets.update(ISO_NAMES[iso])
    return targets, iso


def match_countries(query, rows):
    targets, _ = country_targets(query)
    if not targets:
        return []
    blocked = set(get_list("blocked_countries"))
    out = []
    for r in rows:
        c = fold(r["cname"])
        if c in blocked or (r["count"] is not None and r["count"] <= 0):
            continue
        for tg in targets:
            if tg == c or (len(tg) >= 3 and (tg in c or c in tg)) or difflib.SequenceMatcher(None, tg, c).ratio() >= 0.85:
                out.append(r)
                break
    out.sort(key=lambda r: r["cost"])
    return out


# ═════════════════════════════════════ VERİLƏNLƏR BAZASI ═════════════════════════════════════
SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
 id INTEGER PRIMARY KEY, first_name TEXT DEFAULT '', last_name TEXT DEFAULT '', username TEXT DEFAULT '',
 balance INTEGER NOT NULL DEFAULT 0, is_admin INTEGER NOT NULL DEFAULT 0, banned INTEGER NOT NULL DEFAULT 0,
 ban_reason TEXT DEFAULT '', muted_until INTEGER NOT NULL DEFAULT 0, warns INTEGER NOT NULL DEFAULT 0,
 joined INTEGER NOT NULL DEFAULT 0, last_seen INTEGER NOT NULL DEFAULT 0,
 ref_by INTEGER NOT NULL DEFAULT 0, ref_paid INTEGER NOT NULL DEFAULT 0, last_bonus INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS orders(
 id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, request_id TEXT UNIQUE NOT NULL,
 api_order TEXT DEFAULT '', service_code TEXT, service_name TEXT, country_code TEXT, country_name TEXT,
 iso TEXT DEFAULT '', number TEXT DEFAULT '', code TEXT DEFAULT '', cost_raw TEXT, cost_qep INTEGER,
 sell_qep INTEGER, charged_qep INTEGER, status TEXT NOT NULL, refunded INTEGER NOT NULL DEFAULT 0,
 reason TEXT DEFAULT '', created INTEGER, deadline INTEGER DEFAULT 0, done_at INTEGER DEFAULT 0,
 chat_id INTEGER, msg_id INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS txs(
 id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, amount INTEGER, reason TEXT,
 ref TEXT UNIQUE, created INTEGER);
CREATE TABLE IF NOT EXISTS notes(
 id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, text TEXT, admin_id INTEGER, created INTEGER);
CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY, v TEXT);
CREATE INDEX IF NOT EXISTS ix_orders_user ON orders(user_id);
CREATE INDEX IF NOT EXISTS ix_orders_status ON orders(status);
CREATE INDEX IF NOT EXISTS ix_txs_user ON txs(user_id);
"""
conn = None
DBL = threading.RLock()


def init_db():
    global conn
    conn = sqlite3.connect(DB_PATH, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    _migrate()
    conn.executescript(SCHEMA)


def _migrate():
    """Köhnə bot.db başqa sxemlə yaradılıbsa: çatışmayan sütunları əlavə edir,
    əsas sütun (id/k) yoxdursa köhnə cədvəli adını dəyişib saxlayır (məlumat silinmir)."""
    mem = sqlite3.connect(":memory:")
    mem.executescript(SCHEMA)
    for (t,) in mem.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall():
        want = mem.execute(f"PRAGMA table_info({t})").fetchall()
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({t})").fetchall()}
        if not have:
            continue
        pk = next(w[1] for w in want if w[5])
        if pk not in have:
            for ix in conn.execute(f"PRAGMA index_list({t})").fetchall():
                if ix[3] == "c":
                    conn.execute(f"DROP INDEX IF EXISTS {ix[1]}")
            conn.execute(f"ALTER TABLE {t} RENAME TO {t}_old_{int(time.time())}")
            log.warning("Köhnə '%s' cədvəli sxemə uyğun deyildi, adı dəyişdirildi.", t)
            continue
        for _, name, typ, notnull, default, _pk in want:
            if name not in have:
                dflt = f" DEFAULT {default}" if default is not None else ""
                conn.execute(f"ALTER TABLE {t} ADD COLUMN {name} {typ}{dflt}")
                log.warning("'%s' cədvəlinə '%s' sütunu əlavə olundu.", t, name)
    mem.close()


def q1(sql, *a):
    with DBL:
        r = conn.execute(sql, a).fetchone()
        return dict(r) if r else None


def qa(sql, *a):
    with DBL:
        return [dict(r) for r in conn.execute(sql, a).fetchall()]


def ex(sql, *a):
    with DBL:
        return conn.execute(sql, a)


@contextmanager
def tx():
    with DBL:
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise


def get_setting(k, default=None):
    r = q1("SELECT v FROM settings WHERE k=?", k)
    return r["v"] if r else default


def set_setting(k, v):
    ex("INSERT INTO settings(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", k, str(v))


def del_setting(k):
    ex("DELETE FROM settings WHERE k=?", k)


def get_list(k):
    try:
        return json.loads(get_setting(k, "[]"))
    except ValueError:
        return []


def markup_qep():
    v = get_setting("markup_qep")
    return int(v) if v is not None else to_qep(SMS_MARKUP_AZN)


def timeout_min():
    return int(get_setting("timeout_min", SMS_TIMEOUT_MIN))


def max_active():
    return int(get_setting("max_active", MAX_ACTIVE_PER_USER))


def sell_price(cost_qep):
    return cost_qep + markup_qep()


def announce_block():
    a = get_setting("announce")
    return f"\n\n📣 <b>Duyuru</b>\n{a}" if a else ""


def touch_user(tu):
    ts = now()
    fn, ln, un = tu.get("first_name") or "", tu.get("last_name") or "", tu.get("username") or ""
    if q1("SELECT 1 FROM users WHERE id=?", tu["id"]):
        ex("UPDATE users SET first_name=?, last_name=?, username=?, last_seen=? WHERE id=?", fn, ln, un, ts, tu["id"])
    else:
        ex("INSERT INTO users(id,first_name,last_name,username,joined,last_seen) VALUES(?,?,?,?,?,?)",
           tu["id"], fn, ln, un, ts, ts)
    return q1("SELECT * FROM users WHERE id=?", tu["id"])


def get_user(uid):
    return q1("SELECT * FROM users WHERE id=?", uid)


def is_admin(uid):
    return uid == ADMIN_ID or bool(q1("SELECT 1 FROM users WHERE id=? AND is_admin=1", uid))


def is_muted(u):
    return u["muted_until"] == -1 or u["muted_until"] > now()


def resolve(tok, create=False):
    tok = (tok or "").strip()
    if tok.startswith("@"):
        return q1("SELECT * FROM users WHERE lower(username)=?", tok[1:].lower())
    if re.fullmatch(r"\d{3,15}", tok):
        u = get_user(int(tok))
        if u or not create:
            return u
        ex("INSERT OR IGNORE INTO users(id,joined,last_seen) VALUES(?,?,?)", int(tok), now(), now())
        return get_user(int(tok))
    return None


def uname(u):
    return "@" + u["username"] if u.get("username") else "—"


def full_name(u):
    return " ".join(x for x in (u.get("first_name"), u.get("last_name")) if x) or "—"


def U(u):
    return f"<code>{u['id']}</code> {esc(uname(u) if u.get('username') else full_name(u))}"


def bal_text(uid, u=None):
    if is_admin(uid):
        return "♾ Limitsiz"
    u = u or get_user(uid)
    return money(u["balance"] if u else 0)


def _adjust(uid, delta, reason, ref, allow_neg=False):
    u = q1("SELECT balance FROM users WHERE id=?", uid)
    if u is None:
        raise ValueError("user yoxdur")
    nb = u["balance"] + delta
    if nb < 0 and not allow_neg:
        raise InsufficientFunds()
    ex("UPDATE users SET balance=? WHERE id=?", nb, uid)
    ex("INSERT INTO txs(user_id,amount,reason,ref,created) VALUES(?,?,?,?,?)", uid, delta, reason, ref, now())
    return nb


def adjust_balance(uid, delta, reason, ref=None, allow_neg=False):
    with tx():
        return _adjust(uid, delta, reason, ref or f"{reason}:{uuid.uuid4().hex}", allow_neg)


def get_order(oid):
    return q1("SELECT * FROM orders WHERE id=?", oid)


def count_active(uid):
    return q1(f"SELECT COUNT(*) c FROM orders WHERE user_id=? AND status IN ({','.join('?' * len(ACTIVE))})",
              uid, *ACTIVE)["c"]


def create_order(uid, svc, cc, cname, iso, cost_qep, cost_raw, chat_id):
    sell = sell_price(cost_qep)
    charged = 0 if is_admin(uid) else sell
    rid = "r" + uuid.uuid4().hex
    with tx():
        oid = ex("""INSERT INTO orders(user_id,request_id,service_code,service_name,country_code,country_name,iso,
                    cost_raw,cost_qep,sell_qep,charged_qep,status,created,chat_id)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,'BUYING',?,?)""",
                 uid, rid, svc["code"], svc["name"], cc, cname, iso, cost_raw, cost_qep, sell, charged,
                 now(), chat_id).lastrowid
        if charged:
            _adjust(uid, -charged, f"buy #{oid}", f"buy:{oid}", False)
    return oid


def refund_order(oid, new_status, reason):
    with tx():
        o = q1("SELECT * FROM orders WHERE id=?", oid)
        if not o or o["refunded"] or o["status"] == "DONE":
            return False
        ex("UPDATE orders SET status=?, reason=?, refunded=1 WHERE id=?", new_status, reason, oid)
        if o["charged_qep"] > 0:
            _adjust(o["user_id"], o["charged_qep"], f"refund #{oid}", f"refund:{oid}", True)
    return True


def mark_done(oid, code):
    cur = ex("""UPDATE orders SET status='DONE', code=?, done_at=? WHERE id=? AND refunded=0
                AND status IN ('WAITING','CHECKING','UNCERTAIN','BUYING')""", code, now(), oid)
    return cur.rowcount == 1


# ═══════════════════════════════════ PANELPLUS API (urllib) ═══════════════════════════════════
# Şəbəkə: sistem proxy-si → birbaşa → PythonAnywhere proxy-si ardıcıllığı (işləyən yadda qalır)
_OPENERS = [urllib.request.build_opener(),
            urllib.request.build_opener(urllib.request.ProxyHandler({})),
            urllib.request.build_opener(urllib.request.ProxyHandler(
                {"http": "http://proxy.server:3128", "https": "http://proxy.server:3128"}))]
_net_i = [0]
_net_log = {}


def net_open(req, timeout):
    last = None
    n = len(_OPENERS)
    for k in range(n):
        i = (_net_i[0] + k) % n
        try:
            r = _OPENERS[i].open(req, timeout=timeout)
            _net_i[0] = i
            return r
        except urllib.error.HTTPError:
            _net_i[0] = i
            raise
        except (urllib.error.URLError, OSError) as e:
            last = e
            msg = f"{i}:{getattr(e, 'reason', e)}"
            if time.time() - _net_log.get(msg, 0) > 60:
                _net_log[msg] = time.time()
                log.warning("şəbəkə xətası (yol %s): %s", i, getattr(e, "reason", e))
    raise last


_calls = deque()
_calls_lock = threading.Lock()
_cache = {}
_cache_lock = threading.Lock()


def _gate():
    """Dəqiqədə 120 limitinə qarşı qoruma (100-də dayanır)."""
    while True:
        with _calls_lock:
            t = time.time()
            while _calls and t - _calls[0] > 60:
                _calls.popleft()
            if len(_calls) < 100:
                _calls.append(t)
                return
        time.sleep(1)


def api(action, **params):
    _gate()
    data = {"key": PANELPLUS_API_KEY, "action": action}
    data.update({k: v for k, v in params.items() if v is not None})
    req = urllib.request.Request(PANELPLUS_URL, data=urllib.parse.urlencode(data).encode(),
                                 headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
    try:
        with net_open(req, 45) as r:
            code, raw = r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        if e.code >= 500 or e.code == 429:
            raise ApiUncertain(f"HTTP {e.code}")
        code, raw = e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        raise ApiUncertain(f"şəbəkə: {e.__class__.__name__}: {getattr(e, 'reason', e)}")
    try:
        j = json.loads(raw)
    except ValueError:
        raise ApiUncertain(f"JSON deyil (HTTP {code})")
    if isinstance(j, dict) and j.get("error"):
        raise ApiError(str(j["error"]))
    return j


def cached(key, fetch, ttl=90, force=False):
    with _cache_lock:
        hit = _cache.get(key)
    if hit and not force and time.time() - hit[0] < ttl:
        return hit[1]
    data = fetch()
    with _cache_lock:
        _cache[key] = (time.time(), data)
    return data


def api_services(force=False):
    return cached("services", lambda: norm_services(api("sms_services")), 120, force)


def api_prices(service, force=False):
    return cached(f"prices:{service}", lambda: norm_prices(api("sms_prices", service=service)), 90, force)


def api_countries(force=False):
    try:
        return cached("countries", lambda: norm_countries(api("sms_countries")), 900, force)
    except (ApiError, ApiUncertain):
        return []


def api_iso_map():
    return {c["code"]: c["iso"] for c in api_countries() if c["iso"]}


def api_balance_qep():
    j = api("balance")
    return to_qep(j.get("balance")) if isinstance(j, dict) else None


# ═══════════════════════════════════ TELEGRAM (urllib) ═══════════════════════════════════
def tg(method, _timeout=35, **p):
    body = json.dumps({k: v for k, v in p.items() if v is not None}).encode()
    req = urllib.request.Request(TG_URL + method, data=body, headers={"Content-Type": "application/json"})
    try:
        with net_open(req, _timeout) as r:
            j = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            j = json.loads(e.read().decode())
        except Exception:
            raise TgError(f"HTTP {e.code}")
    except Exception as e:
        raise TgError(f"{e.__class__.__name__}: {getattr(e, 'reason', e)}")
    if not j.get("ok"):
        raise TgError(str(j.get("description", "?")))
    return j["result"]


def send(chat, text, markup=None):
    p = dict(chat_id=chat, text=text, parse_mode="HTML", reply_markup=markup, disable_web_page_preview=True)
    try:
        return tg("sendMessage", **p)
    except TgError as e:
        if "parse entities" in str(e):
            p["parse_mode"] = None
            try:
                return tg("sendMessage", **p)
            except TgError:
                return None
        log.warning("send xətası (%s): %s", chat, e)
        return None


def notify(uid, text, markup=None):
    return send(uid, text, markup) is not None


def notify_admin(text, markup=None):
    notify(ADMIN_ID, text, markup)


def edit(chat, mid, text, markup=None):
    if not mid:
        return
    try:
        tg("editMessageText", chat_id=chat, message_id=mid, text=text, parse_mode="HTML",
           reply_markup=markup, disable_web_page_preview=True)
    except TgError as e:
        if "not modified" not in str(e):
            log.warning("edit xətası: %s", e)


def answer_cb(cid, text=None, alert=False):
    try:
        tg("answerCallbackQuery", callback_query_id=cid, text=text, show_alert=alert or None)
    except TgError:
        pass


def delete_msg(chat, mid):
    try:
        tg("deleteMessage", chat_id=chat, message_id=mid)
    except TgError:
        pass


def send_document(chat, filename, data):
    boundary = uuid.uuid4().hex
    parts = [f"--{boundary}\r\nContent-Disposition: form-data; name=\"chat_id\"\r\n\r\n{chat}\r\n".encode(),
             (f"--{boundary}\r\nContent-Disposition: form-data; name=\"document\"; filename=\"{filename}\"\r\n"
              "Content-Type: application/octet-stream\r\n\r\n").encode(), data, f"\r\n--{boundary}--\r\n".encode()]
    req = urllib.request.Request(TG_URL + "sendDocument", data=b"".join(parts),
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        with net_open(req, 120) as r:
            return json.loads(r.read().decode()).get("ok", False)
    except Exception as e:
        log.warning("sendDocument: %s", e)
        return False


def send_long(chat, text):
    while text:
        chunk, text = text[:3900], text[3900:]
        if text and "\n" in chunk:
            cut = chunk.rfind("\n")
            chunk, text = chunk[:cut], chunk[cut + 1:] + text
        send(chat, chunk)


# ═════════════════════════════════ SİFARİŞ MƏNTİQİ ═════════════════════════════════
WATCHING, _watch_lock = set(), threading.Lock()


def order_flag(o):
    return flag(o.get("iso"))


def wait_text(o, remaining):
    num = esc(plus(o["number"])) if o["number"] else "hazırlanır…"
    return (f"📱 <b>Nömrə hazırdır!</b>\n\n"
            f"📞 Nömrə: <code>{num}</code>\n"
            f"🔧 Servis: <b>{esc(o['service_name'])}</b>\n"
            f"🌍 Ölkə: {order_flag(o)} {esc(o['country_name'])}\n"
            f"💸 Məbləğ: <b>{money(o['charged_qep'])}</b>\n\n"
            f"⏳ SMS gözlənilir… <b>{mmss(remaining)}</b>\n"
            f"ℹ️ {timeout_min()} dəqiqə ərzində SMS gəlməsə balans avtomatik geri qaytarılacaq.")


def cancel_kb(oid):
    return kb([[("❌ Ləğv et", f"cancel:{oid}")]])


def show_waiting(oid):
    o = get_order(oid)
    if not o["number"]:
        for _ in range(4):
            try:
                st = api("sms_status", order=o["api_order"])
                if isinstance(st, dict) and st.get("number"):
                    ex("UPDATE orders SET number=? WHERE id=?", str(st["number"]), oid)
                    break
            except (ApiError, ApiUncertain):
                pass
            time.sleep(1.5)
        o = get_order(oid)
    m = send(o["chat_id"], wait_text(o, o["deadline"] - now()), cancel_kb(oid))
    if m:
        ex("UPDATE orders SET msg_id=? WHERE id=?", m["message_id"], oid)


def start_watch(oid):
    with _watch_lock:
        if oid in WATCHING:
            return
        WATCHING.add(oid)

    def run():
        try:
            watch_order(oid)
        except Exception:
            log.exception("watch_order #%s", oid)
        finally:
            with _watch_lock:
                WATCHING.discard(oid)
    spawn(run)


def perform_buy(oid):
    """sms_buy: şəbəkə xətasında EYNİ request_id ilə təkrar. True=alındı, False=rədd+refund, None=naməlum."""
    o = get_order(oid)
    attempts = 0
    while attempts < 3:
        attempts += 1
        try:
            j = api("sms_buy", service=o["service_code"], country=o["country_code"],
                    price=o["cost_raw"], request_id=o["request_id"])
            api_order = str(j.get("order") or "") if isinstance(j, dict) else ""
            if not api_order:
                raise ApiUncertain("order ID yoxdur")
            cur = ex("UPDATE orders SET api_order=?, status='WAITING', deadline=? WHERE id=? "
                     "AND status IN ('BUYING','UNCERTAIN')", api_order, now() + timeout_min() * 60, oid)
            if cur.rowcount != 1:
                return True
            show_waiting(oid)
            start_watch(oid)
            return True
        except ApiError as e:
            log.warning("sms_buy rədd #%s: %s", oid, e)
            if refund_order(oid, "FAILED", f"API: {e}"):
                u = get_user(o["user_id"])
                notify(o["chat_id"], f"❌ <b>Nömrə alına bilmədi</b>\n📝 Səbəb: <i>{esc(e)}</i>\n"
                       f"💳 Balansın: <b>{bal_text(o['user_id'], u)}</b>")
            return False
        except ApiUncertain as e:
            log.warning("sms_buy naməlum #%s (cəhd %s): %s", oid, attempts, e)
            if attempts < 3:
                time.sleep(2 * attempts)
    ex("UPDATE orders SET status='UNCERTAIN' WHERE id=? AND status='BUYING'", oid)
    notify(o["chat_id"], "🔎 Sifarişin nəticəsi yoxlanılır. Admin baxacaq, pul avtomatik silinmir/qaytarılmır.")
    notify_admin(f"❓ <b>Naməlum alış #{oid}</b> (user <code>{o['user_id']}</code>, {esc(o['service_name'])}/"
                 f"{esc(o['country_name'])})\nPanelPlus cavab vermədi. Eyni request_id ilə yenidən yoxla "
                 f"(ikinci dəfə pul çıxmır) və ya geri qaytar.",
                 kb([[("🔁 Yenidən yoxla", f"unc:retry:{oid}"), ("💸 Geri qaytar", f"unc:refund:{oid}")]]))
    return None


def finalize_cancel(oid, reason):
    o = get_order(oid)
    edit(o["chat_id"], o["msg_id"], f"📞 <code>{esc(plus(o['number']) or '—')}</code>\n❌ Sifariş bağlandı.")
    notify(o["chat_id"],
           f"❌ <b>Nömrə ləğv edildi</b>\n\n📞 Nömrə: <code>{esc(plus(o['number']) or '—')}</code>\n"
           f"📝 Səbəb: <b>{esc(reason)}</b>\n💰 Geri qaytarılan: <b>{money(o['charged_qep'])}</b>\n"
           f"💳 Balansın: <b>{bal_text(o['user_id'])}</b>",
           kb([[("📱 Yeni Nömrə Al", "newnum")]]))


def complete_order(oid, code):
    if not mark_done(oid, code):
        return
    o = get_order(oid)
    edit(o["chat_id"], o["msg_id"], f"📞 <code>{esc(plus(o['number']) or '—')}</code>\n✅ SMS gəldi.")
    notify(o["chat_id"],
           f"🎉 <b>Smsin gəldi, xeyirli olsun!</b>\n\n"
           f"📞 Nömrə: <code>{esc(plus(o['number']))}</code>\n"
           f"💬 Sms: <code>{esc(code)}</code>\n"
           f"💸 Xərclənən Məbləğ: <b>{money(o['charged_qep'])}</b>\n"
           f"🌍 Ölkə: {order_flag(o)} {esc(o['country_name'])}\n"
           f"🔧 Servis: {esc(o['service_name'])}\n"
           f"💳 Qalan Balansın: <b>{bal_text(o['user_id'])}</b>",
           kb([[("📱 Yeni Nömrə Al", "newnum")]]))


def watch_order(oid):
    last_edit = 0
    notified_checking = False
    while True:
        o = get_order(oid)
        if not o or o["status"] not in ("WAITING", "CHECKING"):
            return
        t = now()
        if t > o["deadline"] + 6 * 3600:
            ex("UPDATE orders SET status='UNCERTAIN' WHERE id=? AND status IN ('WAITING','CHECKING')", oid)
            notify_admin(f"❓ Sifariş #{oid} çox uzandı (status naməlum). Əl ilə yoxla.",
                         kb([[("🔁 Yenidən yoxla", f"unc:retry:{oid}"), ("💸 Geri qaytar", f"unc:refund:{oid}")]]))
            return
        status, st = "", None
        try:
            st = api("sms_status", order=o["api_order"])
            status = str(st.get("status", "")).upper() if isinstance(st, dict) else ""
        except (ApiError, ApiUncertain) as e:
            log.warning("sms_status #%s: %s", oid, e)
        if isinstance(st, dict):
            num = str(st.get("number") or "")
            if num and not o["number"]:
                ex("UPDATE orders SET number=? WHERE id=?", num, oid)
            code = str(st.get("code") or "").strip()
            if status == "OK" and code:
                complete_order(oid, code)
                return
            if status == "CANCELED":
                if refund_order(oid, "CANCELED", "Provayder nömrəni ləğv etdi"):
                    finalize_cancel(oid, "Provayder nömrəni ləğv etdi")
                return
            if status == "CHECKING":
                if o["status"] != "CHECKING":
                    ex("UPDATE orders SET status='CHECKING' WHERE id=? AND status='WAITING'", oid)
                if not notified_checking:
                    notified_checking = True
                    notify(o["chat_id"], "🔎 Nömrə yoxlanılır. Nəticə bəlli olana qədər gözləyin.")
                    notify_admin(f"🔎 Sifariş #{oid} CHECKING statusundadır (pul avtomatik qaytarılmır).")
                time.sleep(30)
                continue
            if o["status"] == "CHECKING":
                ex("UPDATE orders SET status='WAITING' WHERE id=? AND status='CHECKING'", oid)
        if status == "WAITING" and t >= o["deadline"]:
            cancelled = False
            try:
                r = api("sms_cancel", order=o["api_order"])
                cancelled = isinstance(r, dict) and str(r.get("status", "")).upper() == "CANCELED"
            except (ApiError, ApiUncertain) as e:
                log.warning("sms_cancel #%s: %s", oid, e)
            if cancelled:
                reason = f"{timeout_min()} dəqiqə içində SMS gəlmədi"
                if refund_order(oid, "CANCELED", reason):
                    finalize_cancel(oid, reason)
                return
            time.sleep(15)
            continue
        if t - last_edit >= 20 and o["deadline"] > t:
            last_edit = t
            edit(o["chat_id"], o["msg_id"], wait_text(get_order(oid), o["deadline"] - t), cancel_kb(oid))
        time.sleep(POLL_SECONDS)


# ═══════════════════════════ DÖVLƏT (FSM) / GUARD / DISPATCH ═══════════════════════════
STATE = {}                # uid -> (ad, data)
_locks, _hits = {}, {}
_glock = threading.Lock()


def set_state(uid, name, **data):
    STATE[uid] = (name, data)


def get_state(uid):
    return STATE.get(uid, (None, {}))


def clear_state(uid):
    STATE.pop(uid, None)


def lock_for(key):
    with _glock:
        return _locks.setdefault(key, threading.Lock())


def guard(fr, chat, cb_id=None, allow_unjoined=False):
    """Ban / təmir / spam yoxlaması. İstifadəçi qaytarır, bloklanıbsa None."""
    u = touch_user(fr)
    uid = fr["id"]
    admin = is_admin(uid)
    block = None
    if u["banned"] and not admin:
        block = f"🚫 Siz botdan banlanmısınız.\n📝 Səbəb: {esc(u['ban_reason'] or '—')}"
    elif get_setting("maintenance") == "1" and not admin:
        block = "🛠 Bot hazırda təmirdədir. Bir az sonra yenidən yoxlayın."
    elif not admin:
        with _glock:
            dq = _hits.setdefault(uid, deque())
            t = time.time()
            while dq and t - dq[0] > 10:
                dq.popleft()
            dq.append(t)
            n = len(dq)
        if n > 15:
            block = "⏳ Çox sürətlisən, bir az yavaş!" if n == 16 else ""
    if block is None:
        if not admin and not allow_unjoined:
            if not is_member(uid):
                send_gate(chat)
                if cb_id:
                    answer_cb(cb_id)
                return None
            if u["ref_by"] and not u["ref_paid"]:
                pay_referral(uid)
        return u
    if cb_id:
        answer_cb(cb_id, re.sub(r"<[^>]+>", "", block) or None, alert=bool(block))
    elif block:
        send(chat, block)
    return None


def main_kb(uid):
    rows = [[{"text": BTN_BUY}, {"text": BTN_BAL}], [{"text": BTN_BONUS}, {"text": BTN_REF}], [{"text": BTN_TOPUP}]]
    if is_admin(uid):
        rows.append([{"text": BTN_ADMIN}])
    return {"keyboard": rows, "resize_keyboard": True}


def topup_kb():
    return kb([[(c, f"url:https://t.me/{c.lstrip('@')}")] for c in TOPUP_CONTACTS])


# ═══════════════════ MƏCBURİ KANAL / REFERANS / BONUS ═══════════════════
_member_ok = {}
_chan_warned = [False]


def is_member(uid, force=False):
    """Məcburi kanal üzvlüyü. Kanal/bot xətasında bloklamır (admin xəbərdar olunur)."""
    if not REQUIRED_CHANNEL:
        return True
    t = time.time()
    if not force and t - _member_ok.get(uid, 0) < 600:
        return True
    try:
        r = tg("getChatMember", chat_id=REQUIRED_CHANNEL, user_id=uid)
    except TgError as e:
        log.warning("getChatMember: %s", e)
        if any(x in str(e).lower() for x in ("chat not found", "not enough rights", "forbidden", "administrator")) \
                and not _chan_warned[0]:
            _chan_warned[0] = True
            notify_admin(f"⚠️ Məcburi kanal yoxlanıla bilmir ({esc(REQUIRED_CHANNEL)}): <i>{esc(e)}</i>\n"
                         "Botu kanala <b>admin</b> əlavə edin. Bu vaxta qədər yoxlama söndürülüb.")
        return True
    st = r.get("status")
    ok = st in ("creator", "administrator", "member") or (st == "restricted" and r.get("is_member"))
    if ok:
        _member_ok[uid] = t
    else:
        _member_ok.pop(uid, None)
    return ok


def send_gate(chat):
    send(chat, f"📢 <b>Botdan istifadə etmək üçün kanalımıza qoşulun:</b>\n{esc(REQUIRED_CHANNEL)}\n\n"
               "Qoşulduqdan sonra <b>✅ Yoxla</b> düyməsinə basın.",
         kb([[("📢 Kanala qoşul", f"url:https://t.me/{REQUIRED_CHANNEL.lstrip('@')}")], [("✅ Yoxla", "chk")]]))


def capture_ref(fr, arg):
    """/start ref_ID — yalnız yeni istifadəçi və yalnız bir dəfə."""
    touch_user(fr)
    if not arg.startswith("ref_") or not arg[4:].isdigit():
        return
    rid, uid = int(arg[4:]), fr["id"]
    u = get_user(uid)
    ref = get_user(rid)
    if rid == uid or not ref or ref["ref_by"] == uid or u["ref_by"] or u["ref_paid"] or u["joined"] < now() - 600:
        return
    ex("UPDATE users SET ref_by=? WHERE id=? AND ref_by=0", rid, uid)


def pay_referral(uid):
    """Dəvət olunan kanala qoşulduqdan sonra dəvət edənə bir dəfə bonus verir."""
    amt = to_qep(REFERRAL_BONUS_AZN)
    try:
        with tx():
            r = q1("SELECT ref_by FROM users WHERE id=? AND ref_paid=0 AND ref_by>0", uid)
            if not r:
                return
            ex("UPDATE users SET ref_paid=1 WHERE id=?", uid)
            _adjust(r["ref_by"], amt, f"referral {uid}", f"ref:{uid}")
    except Exception:
        log.exception("pay_referral")
        return
    notify(r["ref_by"], f"👥 <b>Yeni referans!</b>\nDəvətinlə yeni istifadəçi qoşuldu.\n"
                        f"💰 Əlavə olundu: <b>{money(amt)}</b>\n💳 Balansın: <b>{bal_text(r['ref_by'])}</b>")


def btn_bonus(uid, chat):
    clear_state(uid)
    amt, ds = to_qep(DAILY_BONUS_AZN), day_start()
    nb = None
    try:
        with tx():
            r = q1("SELECT last_bonus FROM users WHERE id=?", uid)
            if r["last_bonus"] < ds:
                ex("UPDATE users SET last_bonus=? WHERE id=?", ds, uid)
                nb = _adjust(uid, amt, "bonus", f"bonus:{uid}:{ds}")
    except Exception:
        log.exception("bonus")
        return send(chat, UNAVAILABLE)
    if nb is None:
        left = ds + 86400 - now()
        return send(chat, f"⏳ Bu gün bonusu artıq almısan.\n🕒 Növbəti bonus: <b>{left // 3600} saat {left % 3600 // 60} dəq</b> sonra.")
    send(chat, f"🎁 <b>Gündəlik bonus!</b>\n💰 Əlavə olundu: <b>{money(amt)}</b>\n💳 Balansın: <b>{bal_text(uid)}</b>\n\n"
               "Hər gün yenidən ala bilərsən. 🔁")


def btn_ref(uid, chat):
    clear_state(uid)
    amt = to_qep(REFERRAL_BONUS_AZN)
    n = q1("SELECT COUNT(*) c FROM users WHERE ref_by=? AND ref_paid=1", uid)["c"]
    link = f"https://t.me/{BOT_USERNAME}?start=ref_{uid}"
    send(chat, f"👥 <b>Referans Sistemi</b>\n\n🔗 Sənin linkin:\n<code>{link}</code>\n\n"
               f"💰 Hər dəvət olunan adam üçün: <b>{money(amt)}</b>\n"
               f"👤 Dəvət etdiyin: <b>{n}</b>\n💸 Qazandığın: <b>{money(n * amt)}</b>\n\n"
               "ℹ️ Dəvət olunan adam botu açıb kanala qoşulduqda bonus balansına əlavə olunur.",
         kb([[("📤 Linki paylaş", "url:https://t.me/share/url?url=" + urllib.parse.quote(link))]]))


def cb_check(cb):
    uid, chat, mid = cb["from"]["id"], cb["message"]["chat"]["id"], cb["message"]["message_id"]
    if is_member(uid, force=True):
        answer_cb(cb["id"], "✅ Təsdiqləndi!")
        delete_msg(chat, mid)
        pay_referral(uid)
        cmd_start(uid, chat, cb["from"])
    else:
        answer_cb(cb["id"], "❌ Hələ kanala qoşulmamısınız.", True)


# ═══════════════════════════════════ İSTİFADƏÇİ ═══════════════════════════════════
def cmd_start(uid, chat, fr):
    clear_state(uid)
    welcome = get_setting("welcome") or (
        f"👋 Salam, <b>{esc(fr.get('first_name'))}</b>!\n\n"
        "📱 Virtual nömrə botuna xoş gəlmisən.\nWhatsApp, Telegram, TikTok və digər xidmətlər üçün SMS kodu al.\n\n"
        "👇 Aşağıdakı menyudan seç.")
    send(chat, welcome + announce_block(), main_kb(uid))


def cmd_help(uid, chat):
    t = ("ℹ️ <b>Kömək</b>\n\n📱 Nömrə Al — servis və ölkə seç, SMS kodunu al\n💰 Balansım — hesab məlumatların\n"
         "➕ Balans Artır — balans artırmaq üçün əlaqə")
    if is_admin(uid):
        t += "\n\n🛠 Admin əmrləri: /adminhelp"
    send(chat, t)


def ask_service(uid, chat):
    clear_state(uid)
    set_state(uid, "service")
    send(chat, "🔎 <b>Servis adını yazın</b>\n\nMəsələn: <code>WhatsApp</code>, <code>Telegram</code>, "
               "<code>TikTok</code>" + announce_block())


def btn_balance(uid, chat, fr):
    clear_state(uid)
    u = get_user(uid)
    done = q1("SELECT COUNT(*) c FROM orders WHERE user_id=? AND status='DONE'", uid)["c"]
    un = "@" + fr["username"] if fr.get("username") else "—"
    send(chat,
         "👤 <b>Hesab məlumatların</b>\n\n"
         f"📛 Ad: <b>{esc(fr.get('first_name') or '—')}</b>\n"
         f"📛 Soyad: <b>{esc(fr.get('last_name') or '—')}</b>\n"
         f"🔗 Telegram İstifadəçi Adı: <b>{esc(un)}</b>\n"
         f"💰 Balansım: <b>{bal_text(uid, u)}</b>\n"
         f"🆔 Id: <code>{uid}</code>\n"
         f"📦 Alınan Nömrələrin Sayı: <b>{done}</b>")


def btn_topup(uid, chat):
    clear_state(uid)
    c = " və ya ".join(TOPUP_CONTACTS)
    send(chat, f"💳 <b>Balans Artır</b>\n\nBalansı artırmaq üçün zəhmət olmasa {esc(c)} ilə əlaqə saxlayın.\n\n"
               f"🆔 ID-niz: <code>{uid}</code> (adminə göndərin)", topup_kb())


def pick_service(uid, chat, mid, svc):
    set_state(uid, "country", service_code=svc["code"], service_name=svc["name"])
    text = (f"✅ Servis: <b>{esc(svc['name'])}</b>\n\n🌍 İndi <b>ölkə adını</b> yazın\n"
            "Məsələn: <code>Azerbaycan</code> və ya <code>Azerbaijan</code>")
    if mid:
        edit(chat, mid, text)
    else:
        send(chat, text)


def probe_service(text):
    """Siyahıda tapılmasa belə, API-dən birbaşa servis kodu kimi yoxla."""
    blocked = set(get_list("blocked_services"))
    cands = []
    for c in (fold(text).replace(" ", ""), text.strip().lower(), text.strip().lower().replace(" ", "_")):
        if c and c not in cands:
            cands.append(c)
    for c in cands:
        if fold(c) in blocked:
            continue
        try:
            rows = api_prices(c, force=True)
        except (ApiError, ApiUncertain):
            continue
        if rows:
            return {"code": c, "name": text.strip().title()}
    return None


def on_service_text(uid, chat, text):
    wait = send(chat, "🔎 Axtarılır…")
    wid = wait["message_id"] if wait else 0
    try:
        services = api_services()
        res = match_services(text, services)
        if not res:                       # keş köhnə ola bilər → API-dən təzələ
            services = api_services(force=True)
            res = match_services(text, services)
        if not res:                       # hələ də yoxdursa servis kodu kimi API-də yoxla
            p = probe_service(text)
            if p:
                res = [p]
    except (ApiError, ApiUncertain) as e:
        log.warning("sms_services: %s", e)
        edit(chat, wid, UNAVAILABLE)
        return
    if not res:
        edit(chat, wid, "😕 Belə servis tapılmadı. Başqa ad yazın (məs: <code>Telegram</code>).")
        return
    q = SERVICE_ALIAS.get(fold(text), fold(text))
    if len(res) == 1 or fold(res[0]["name"]) == q:
        pick_service(uid, chat, wid, res[0])
        return
    rows = [[(f"🔧 {s['name']}", f"svc:{s['code']}")] for s in res[:8]]
    edit(chat, wid, "🔧 Hansını nəzərdə tutursan?", kb(rows))


def cb_svc(uid, chat, mid, code):
    try:
        services = api_services()
    except (ApiError, ApiUncertain):
        send(chat, UNAVAILABLE)
        return
    svc = next((s for s in services if s["code"] == code), None)
    if not svc:
        send(chat, "😕 Servis tapılmadı, yenidən axtarın.")
        return
    pick_service(uid, chat, mid, svc)


def find_country_rows(text, scode):
    """Əvvəl keş, sonra təzə qiymətlər, sonra sms_countries siyahısı ilə axtarış."""
    rows = api_prices(scode)
    res = match_countries(text, rows)
    if res:
        return res
    rows = api_prices(scode, force=True)
    res = match_countries(text, rows)
    if res:
        return res
    targets, iso = country_targets(text)
    blocked = set(get_list("blocked_countries"))
    codes = set()
    for c in api_countries(force=True):
        cn = fold(c["cname"])
        if cn in blocked:
            continue
        if (iso and c["iso"] == iso) or cn in targets or any(len(t) >= 3 and (t in cn) for t in targets):
            codes.add(c["code"])
    res = [r for r in rows if r["code"] in codes and not (r["count"] is not None and r["count"] <= 0)]
    res.sort(key=lambda r: r["cost"])
    return res


def on_country_text(uid, chat, text):
    _, d = get_state(uid)
    scode, sname = d.get("service_code"), d.get("service_name")
    if not scode:
        ask_service(uid, chat)
        return
    wait = send(chat, "🔎 Axtarılır…")
    wid = wait["message_id"] if wait else 0
    try:
        res = find_country_rows(text, scode)
        iso_map = api_iso_map()
    except (ApiError, ApiUncertain) as e:
        log.warning("sms_prices: %s", e)
        edit(chat, wid, UNAVAILABLE)
        return
    nav = [("🔧 Servisi dəyiş", "chsvc")]
    if not res:
        edit(chat, wid, f"😕 <b>{esc(sname)}</b> üçün «{esc(text)}» tapılmadı və ya stok yoxdur.\n"
                        "Başqa ölkə adı yazın.", kb([nav]))
        return
    buttons = []
    for r in res[:10]:
        sell = sell_price(r["cost"])
        cnt = f" • {r['count']} ədəd" if r["count"] is not None else ""
        data = f"buy:{scode}:{r['code']}:{sell}"
        if len(data.encode()) <= 64:
            buttons.append([(f"{flag(iso_for(r['code'], r['cname'], iso_map))} {r['cname']} • {money(sell)}{cnt} ➜ Al", data)])
    buttons.append(nav)
    edit(chat, wid, f"🌍 <b>{esc(sname)}</b> • «{esc(text)}» nəticələri:\n👇 Almaq istədiyini seç. "
                    "Başqa ölkə üçün yenidən yaz.", kb(buttons))


def cb_buy(cb):
    uid, chat, mid = cb["from"]["id"], cb["message"]["chat"]["id"], cb["message"]["message_id"]
    answer_cb(cb["id"], "⏳ Yoxlanılır…")
    try:
        _, scode, ccode, shown = cb["data"].split(":")
        shown = int(shown)
    except ValueError:
        return
    say = lambda t, m=None: send(chat, t, m)
    u, admin = get_user(uid), is_admin(uid)
    if is_muted(u) and not admin:
        say("🔇 Sizin hesabınız susdurulub.")
        return
    if not admin and count_active(uid) >= max_active():
        say(f"⚠️ Eyni anda maksimum {max_active()} aktiv nömrən ola bilər. Əvvəlki nömrələrin bitməsini gözlə.")
        return
    with lock_for(("buy", uid)):
        try:
            rows = api_prices(scode, force=True)
            services = api_services()
            iso_map = api_iso_map()
        except (ApiError, ApiUncertain) as e:
            log.warning("buy hazırlıq: %s", e)
            say(UNAVAILABLE)
            return
        svc = next((s for s in services if s["code"] == scode), {"code": scode, "name": scode})
        row = next((r for r in rows if r["code"] == ccode), None)
        if not row or (row["count"] is not None and row["count"] <= 0):
            say("😕 Bu ölkə üçün nömrə qalmayıb. Başqa ölkə seçin.")
            return
        if fold(row["cname"]) in set(get_list("blocked_countries")):
            say("🚫 Bu ölkə müvəqqəti bağlıdır.")
            return
        sell = sell_price(row["cost"])
        if sell != shown:
            edit(chat, mid, f"ℹ️ Qiymət dəyişdi: <b>{esc(row['cname'])}</b> • <b>{money(sell)}</b>\n"
                            "Davam etmək üçün <b>Al</b> düyməsinə basın.",
                 kb([[(f"{money(sell)} ➜ Al", f"buy:{scode}:{ccode}:{sell}")], [("🔧 Servisi dəyiş", "chsvc")]]))
            return
        if not admin and u["balance"] < sell:
            say(f"❌ <b>Balansın kifayət etmir!</b>\n\n💳 Balansın: <b>{money(u['balance'])}</b>\n"
                f"💸 Qiymət: <b>{money(sell)}</b>\n\n➕ Balans artırmaq üçün: {esc(' / '.join(TOPUP_CONTACTS))}", topup_kb())
            return
        try:
            pp = api_balance_qep()
        except (ApiError, ApiUncertain) as e:
            log.warning("balance: %s", e)
            say(UNAVAILABLE)
            return
        if pp is not None and pp < row["cost"]:
            say("⚠️ Xidmət müvəqqəti dayandırılıb. Bir az sonra yenidən cəhd edin.")
            notify_admin(f"🚨 <b>PanelPlus balansı bitir!</b>\nBalans: {money(pp)} • lazım: {money(row['cost'])}\n"
                         "Satış dayandırıldı, balansı artırın.")
            return
        try:
            oid = create_order(uid, svc, ccode, row["cname"], iso_for(ccode, row["cname"], iso_map),
                               row["cost"], row["raw"], chat)
        except InsufficientFunds:
            say("❌ Balansın kifayət etmir.")
            return
        perform_buy(oid)


def cb_cancel(cb):
    uid, chat = cb["from"]["id"], cb["message"]["chat"]["id"]
    answer_cb(cb["id"], "⏳ Ləğv edilir…")
    try:
        oid = int(cb["data"].split(":")[1])
    except ValueError:
        return
    o = get_order(oid)
    if not o or (o["user_id"] != uid and not is_admin(uid)):
        return
    if o["status"] == "DONE":
        send(chat, "✅ SMS artıq gəlib, nömrə təsdiqlənib — ləğv etmək olmur.")
        return
    if o["status"] != "WAITING":
        send(chat, "ℹ️ Bu sifariş artıq bağlanıb və ya yoxlanılır.")
        return
    try:
        r = api("sms_cancel", order=o["api_order"])
    except ApiError as e:
        send(chat, f"⚠️ Provayder hələ ləğvə icazə vermir: <i>{esc(e)}</i>\n"
                   f"Bir az sonra yenidən cəhd et. {timeout_min()} dəqiqə dolanda avtomatik geri qayıdacaq.")
        return
    except ApiUncertain:
        send(chat, UNAVAILABLE)
        return
    if isinstance(r, dict) and str(r.get("status", "")).upper() == "CANCELED":
        if refund_order(oid, "CANCELED", "İstifadəçi ləğv etdi (SMS gəlmədi)"):
            finalize_cancel(oid, "İstifadəçi ləğv etdi (SMS gəlmədi)")
    else:
        send(chat, "⚠️ Ləğv alınmadı, nömrə hələ aktivdir.")


# ═══════════════════════════════════ ADMİN PANEL ═══════════════════════════════════
def panel_view():
    n_u = q1("SELECT COUNT(*) c FROM users")["c"]
    n_o = q1("SELECT COUNT(*) c FROM orders")["c"]
    n_a = q1(f"SELECT COUNT(*) c FROM orders WHERE status IN ({','.join('?' * len(ACTIVE))})", *ACTIVE)["c"]
    t = f"🛠 <b>Admin Panel</b>\n\n👥 İstifadəçi: <b>{n_u}</b>  📦 Sifariş: <b>{n_o}</b>  ⏳ Aktiv: <b>{n_a}</b>\n\n👇 Əməliyyat seçin"
    k = kb([
        [("➕ Admin Əlavə Et", "adm:addadmin"), ("➖ Admin Çıxar", "adm:deladmin")],
        [("💰 Balans Əlavə Et", "adm:addbal"), ("💸 Balans Sil", "adm:delbal")],
        [("🏦 Depozit Et", "adm:deposit"), ("📡 API Durumu", "adm:api")],
        [("📋 Çəkilən Nömrələr", "adm:orders"), ("📊 İstatistikalar", "adm:stats")],
        [("🚫 Banla", "adm:ban"), ("✅ Ban Aç", "adm:unban")],
        [("🔇 Mute Et", "adm:mute"), ("🔊 Mute Aç", "adm:unmute")],
        [("📢 Elan Göndər", "adm:broadcast"), ("📣 Duyuru", "adm:annc")],
        [("👥 İstifadəçilər", "adm:users"), ("⚙️ Tənzimləmələr", "adm:settings")],
        [("📜 Bütün Əmrlər", "adm:cmds"), ("❌ Bağla", "adm:close")]])
    return t, k


ADMIN_CMDS = {}
GROUPS = ["👑 Adminlər", "💰 Balans", "🚫 Moderasiya", "👥 İstifadəçilər", "📦 Sifarişlər",
          "📊 Statistika", "📢 Mesaj", "⚙️ Sistem"]


def acmd(name, usage, desc, group, owner=False, min_args=0, aliases=()):
    def deco(fn):
        meta = dict(fn=fn, usage=usage, desc=desc, group=group, owner=owner, min_args=min_args, name=name)
        ADMIN_CMDS[name] = meta
        for a in aliases:
            ADMIN_CMDS[a] = meta
        return fn
    return deco


def run_cmd(name, args, actor, raw=""):
    meta = ADMIN_CMDS[name]
    if meta["owner"] and actor != ADMIN_ID:
        return "⛔ Bu əmr yalnız sahib üçündür."
    if len(args) < meta["min_args"]:
        return f"ℹ️ İstifadə: <code>{esc(meta['usage'])}</code>"
    return meta["fn"](actor, args, raw)


ERR_U = "❌ İstifadəçi tapılmadı (ID və ya @username düzgün olmalı, istifadəçi botu /start etməlidir)."
ERR_A = "❌ Məbləğ səhvdir (məs: 5 və ya 2.5)."
WIZ = {
    "addadmin": ("addadmin", ["target"]), "deladmin": ("deladmin", ["target"]),
    "addbal": ("addbal", ["target", "amount"]), "delbal": ("delbal", ["target", "amount"]),
    "deposit": ("deposit", ["target", "amount"]), "ban": ("ban", ["target", "reason"]),
    "unban": ("unban", ["target"]), "mute": ("mute", ["target", "minutes"]),
    "unmute": ("unmute", ["target"]), "broadcast": ("broadcast", ["text"]),
    "annc_write": ("duyurusave", ["text"]),
}
PROMPTS = {
    "target": "🆔 İstifadəçinin <b>ID</b> və ya <b>@username</b> göndərin:",
    "amount": "💵 Məbləği yazın (AZN, məs: <code>5</code> və ya <code>2.5</code>):",
    "reason": "📝 Səbəbi yazın (keçmək üçün <code>-</code>):",
    "minutes": "⏱ Neçə dəqiqə? (daimi üçün <code>0</code>):",
    "text": "✉️ Mətni göndərin:",
}


def _settings_view():
    mt = get_setting("maintenance") == "1"
    t = (f"⚙️ <b>Tənzimləmələr</b>\n\n🛠 Təmir rejimi: <b>{'AÇIQ' if mt else 'bağlı'}</b>\n"
         f"💲 Markup: <b>{money(markup_qep())}</b>\n⏱ SMS gözləmə: <b>{timeout_min()} dəq</b>\n"
         f"📦 Maks. aktiv nömrə: <b>{max_active()}</b>\n\n<i>Dəyişmək üçün: /setmarkup /settimeout /setmaxactive</i>")
    return t, kb([[("🛠 Təmir: söndür" if mt else "🛠 Təmir: yandır", "adm:maint")], [("◀️ Panel", "adm:back")]])


def _annc_view():
    a = get_setting("announce")
    t = ("📣 <b>Duyuru</b>\n\nDuyuru /start və «Nömrə Al» zamanı bütün istifadəçilərə göstərilir.\n\n"
         f"<b>Cari duyuru:</b>\n{a if a else '— yoxdur —'}")
    return t, kb([[("✍️ Yaz / Dəyiş", "adm:annc_write")],
                  [("📢 Hamıya göndər", "ann:send"), ("🗑 Sil", "ann:del")],
                  [("◀️ Panel", "adm:back")]])


def orders_page(page, per=8):
    total = q1("SELECT COUNT(*) c FROM orders")["c"]
    pages = max(1, (total + per - 1) // per)
    page = max(0, min(page, pages - 1))
    rows = qa("SELECT * FROM orders ORDER BY id DESC LIMIT ? OFFSET ?", per, page * per)
    t = f"📋 <b>Çəkilən Nömrələr</b> ({page + 1}/{pages})\n\n" + ("\n".join(order_line(o) for o in rows) or "— boşdur —")
    nav = []
    if page > 0:
        nav.append(("◀️", f"ordp:{page - 1}"))
    nav.append((f"{page + 1}/{pages}", "noop"))
    if page < pages - 1:
        nav.append(("▶️", f"ordp:{page + 1}"))
    return t, kb([nav, [("◀️ Panel", "adm:back")]])


def order_line(o):
    u = get_user(o["user_id"]) or {}
    who = "@" + u["username"] if u.get("username") else str(o["user_id"])
    return (f"{ICON.get(o['status'], '•')} <b>#{o['id']}</b> <code>{esc(plus(o['number']) or '—')}</code> • "
            f"{esc(o['service_name'])}/{esc(o['country_name'])} • {money(o['charged_qep'])} • {esc(who)} • {fmt_ts(o['created'])}")


def adm_cb(cb):
    uid, chat, mid = cb["from"]["id"], cb["message"]["chat"]["id"], cb["message"]["message_id"]
    if not is_admin(uid):
        answer_cb(cb["id"], "⛔", True)
        return
    answer_cb(cb["id"])
    key = cb["data"][4:]
    if key in WIZ:
        cmd, params = WIZ[key]
        set_state(uid, "wiz", cmd=cmd, params=params, vals=[])
        send(chat, PROMPTS[params[0]] + "\n\n<i>Ləğv etmək: /cancel</i>")
    elif key == "api":
        send_long(chat, run_cmd("apistatus", [], uid))
    elif key == "stats":
        send_long(chat, run_cmd("stats", [], uid))
    elif key == "users":
        send_long(chat, run_cmd("users", [], uid))
    elif key == "orders":
        t, k = orders_page(0)
        send(chat, t, k)
    elif key == "settings":
        t, k = _settings_view()
        send(chat, t, k)
    elif key == "annc":
        t, k = _annc_view()
        send(chat, t, k)
    elif key == "maint":
        set_setting("maintenance", "0" if get_setting("maintenance") == "1" else "1")
        t, k = _settings_view()
        edit(chat, mid, t, k)
    elif key == "cmds":
        send_long(chat, run_cmd("adminhelp", [], uid))
    elif key == "back":
        t, k = panel_view()
        edit(chat, mid, t, k)
    elif key == "close":
        delete_msg(chat, mid)


def ann_cb(cb):
    uid, chat, mid = cb["from"]["id"], cb["message"]["chat"]["id"], cb["message"]["message_id"]
    if not is_admin(uid):
        answer_cb(cb["id"], "⛔", True)
        return
    answer_cb(cb["id"])
    act = cb["data"][4:]
    if act == "del":
        del_setting("announce")
        t, k = _annc_view()
        edit(chat, mid, t, k)
    elif act == "send":
        a = get_setting("announce")
        if not a:
            send(chat, "ℹ️ Əvvəlcə duyuru yazın.")
            return
        s, f = broadcast(qa("SELECT id FROM users WHERE banned=0"), f"📣 <b>Duyuru</b>\n\n{a}")
        send(chat, f"📢 Duyuru göndərildi: ✅ {s} • ❌ {f}")


def ord_cb(cb):
    if not is_admin(cb["from"]["id"]):
        answer_cb(cb["id"], "⛔", True)
        return
    answer_cb(cb["id"])
    t, k = orders_page(int(cb["data"][5:]))
    edit(cb["message"]["chat"]["id"], cb["message"]["message_id"], t, k)


def unc_cb(cb):
    uid, chat = cb["from"]["id"], cb["message"]["chat"]["id"]
    if not is_admin(uid):
        answer_cb(cb["id"], "⛔", True)
        return
    _, act, oid = cb["data"].split(":")
    oid = int(oid)
    o = get_order(oid)
    if not o or o["status"] not in ("UNCERTAIN", "BUYING", "WAITING", "CHECKING"):
        answer_cb(cb["id"], "Sifariş artıq bağlıdır.", True)
        return
    answer_cb(cb["id"], "⏳")
    if act == "retry":
        if o["api_order"]:
            ex("UPDATE orders SET status='WAITING' WHERE id=? AND status='UNCERTAIN'", oid)
            start_watch(oid)
            send(chat, f"🔁 #{oid} yenidən izlənir.")
        else:
            r = perform_buy(oid)
            send(chat, f"🔁 #{oid}: {'alındı' if r else 'rədd edildi' if r is False else 'yenə naməlum'}.")
    else:
        if refund_order(oid, "FAILED", f"Admin geri qaytardı ({uid})"):
            notify(o["chat_id"], f"💸 #{oid} sifarişi üzrə {money(o['charged_qep'])} balansına qaytarıldı.")
            send(chat, f"💸 #{oid} geri qaytarıldı.")
        else:
            send(chat, "ℹ️ Artıq qaytarılıb və ya sifariş tamamlanıb.")


def wiz_step(uid, chat, text):
    if not is_admin(uid):
        clear_state(uid)
        return
    _, d = get_state(uid)
    vals, params = d["vals"] + [text.strip()], d["params"]
    if len(vals) < len(params):
        set_state(uid, "wiz", cmd=d["cmd"], params=params, vals=vals)
        send(chat, PROMPTS[params[len(vals)]])
        return
    clear_state(uid)
    args = [v for p, v in zip(params, vals) if not (p == "reason" and v == "-")]
    raw = vals[-1] if params == ["text"] else " ".join(args)
    send_long(chat, run_cmd(d["cmd"], args, uid, raw))


def admin_command(uid, chat, text):
    parts = text.split(maxsplit=1)
    name = parts[0][1:].split("@")[0].lower()
    meta = ADMIN_CMDS.get(name)
    if not meta or not is_admin(uid):
        return False
    raw = parts[1] if len(parts) > 1 else ""
    try:
        out = run_cmd(meta["name"], raw.split(), uid, raw)
    except Exception:
        log.exception("admin əmr xətası: %s", text)
        out = "⚠️ Xəta baş verdi (bot.log-a bax)."
    send_long(chat, out)
    return True


# ════════════════════════════ ADMİN ƏMRLƏRİ (70) ════════════════════════════
def _amt(s):
    v = to_qep(s)
    return v if v and v > 0 else None


# ── 👑 Adminlər
@acmd("addadmin", "/addadmin ID|@user", "Admin əlavə et", GROUPS[0], owner=True, min_args=1)
def c_addadmin(actor, args, raw):
    u = resolve(args[0], create=True)
    if not u:
        return ERR_U
    if is_admin(u["id"]):
        return "ℹ️ Artıq adminidir."
    ex("UPDATE users SET is_admin=1 WHERE id=?", u["id"])
    notify(u["id"], "👑 Siz admin təyin edildiniz! /start yazın — menyuda <b>Admin Panel</b> görünəcək.")
    return f"✅ {U(u)} admin edildi."


@acmd("deladmin", "/deladmin ID|@user", "Admini çıxar", GROUPS[0], owner=True, min_args=1)
def c_deladmin(actor, args, raw):
    u = resolve(args[0])
    if not u:
        return ERR_U
    if u["id"] == ADMIN_ID:
        return "⛔ Sahib çıxarıla bilməz."
    ex("UPDATE users SET is_admin=0 WHERE id=?", u["id"])
    notify(u["id"], "ℹ️ Admin səlahiyyətiniz alındı.")
    return f"✅ {U(u)} adminlikdən çıxarıldı."


@acmd("admins", "/admins", "Adminlərin siyahısı", GROUPS[0])
def c_admins(actor, args, raw):
    rows = qa("SELECT * FROM users WHERE is_admin=1")
    head = f"👑 <b>Sahib:</b> <code>{ADMIN_ID}</code>\n"
    return head + ("\n".join(f"🛡 {U(u)}" for u in rows) if rows else "Başqa admin yoxdur.")


# ── 💰 Balans
@acmd("addbal", "/addbal ID|@user MƏBLƏĞ", "Balans əlavə et", GROUPS[1], min_args=2, aliases=("balans_ver",))
def c_addbal(actor, args, raw):
    u, a = resolve(args[0]), _amt(args[1])
    if not u:
        return ERR_U
    if a is None:
        return ERR_A
    nb = adjust_balance(u["id"], a, f"admin {actor} addbal")
    return f"✅ {U(u)} • +{money(a)} • yeni balans: <b>{money(nb)}</b>"


@acmd("delbal", "/delbal ID|@user MƏBLƏĞ", "Balans sil", GROUPS[1], min_args=2, aliases=("balans_al",))
def c_delbal(actor, args, raw):
    u, a = resolve(args[0]), _amt(args[1])
    if not u:
        return ERR_U
    if a is None:
        return ERR_A
    try:
        nb = adjust_balance(u["id"], -a, f"admin {actor} delbal")
    except InsufficientFunds:
        return f"❌ Balans kifayət etmir (cari: {money(u['balance'])})."
    return f"✅ {U(u)} • -{money(a)} • yeni balans: <b>{money(nb)}</b>"


@acmd("setbal", "/setbal ID|@user MƏBLƏĞ", "Balansı təyin et", GROUPS[1], min_args=2)
def c_setbal(actor, args, raw):
    u, a = resolve(args[0]), to_qep(args[1])
    if not u:
        return ERR_U
    if a is None or a < 0:
        return ERR_A
    nb = adjust_balance(u["id"], a - u["balance"], f"admin {actor} setbal", allow_neg=True)
    return f"✅ {U(u)} balansı: <b>{money(nb)}</b>"


@acmd("deposit", "/deposit ID|@user MƏBLƏĞ", "Depozit (istifadəçiyə bildiriş gedir)", GROUPS[1], min_args=2)
def c_deposit(actor, args, raw):
    u, a = resolve(args[0]), _amt(args[1])
    if not u:
        return ERR_U
    if a is None:
        return ERR_A
    nb = adjust_balance(u["id"], a, f"deposit by {actor}")
    notify(u["id"], f"✅ <b>Depozit təsdiqləndi!</b>\n💰 Əlavə olundu: <b>{money(a)}</b>\n💳 Yeni balans: <b>{money(nb)}</b>")
    return f"🏦 {U(u)} • depozit +{money(a)} • balans: <b>{money(nb)}</b>"


@acmd("bal", "/bal ID|@user", "İstifadəçi balansı", GROUPS[1], min_args=1)
def c_bal(actor, args, raw):
    u = resolve(args[0])
    return f"💳 {U(u)} • <b>{bal_text(u['id'], u)}</b>" if u else ERR_U


@acmd("resetbal", "/resetbal ID|@user", "Balansı sıfırla", GROUPS[1], min_args=1)
def c_resetbal(actor, args, raw):
    u = resolve(args[0])
    if not u:
        return ERR_U
    adjust_balance(u["id"], -u["balance"], f"admin {actor} resetbal", allow_neg=True)
    return f"✅ {U(u)} balansı sıfırlandı."


@acmd("addball", "/addball MƏBLƏĞ", "Hamıya balans əlavə et", GROUPS[1], min_args=1)
def c_addball(actor, args, raw):
    a = _amt(args[0])
    if a is None:
        return ERR_A
    batch = uuid.uuid4().hex[:8]
    users = qa("SELECT id FROM users WHERE banned=0")
    with tx():
        for u in users:
            _adjust(u["id"], a, f"admin {actor} addball", f"addball:{batch}:{u['id']}")
    return f"✅ {len(users)} istifadəçiyə +{money(a)} əlavə olundu."


@acmd("transfer", "/transfer KİMDƏN KİMƏ MƏBLƏĞ", "Balans köçür", GROUPS[1], min_args=3)
def c_transfer(actor, args, raw):
    a, b, amt = resolve(args[0]), resolve(args[1]), _amt(args[2])
    if not a or not b:
        return ERR_U
    if amt is None:
        return ERR_A
    ref = uuid.uuid4().hex
    try:
        with tx():
            _adjust(a["id"], -amt, f"transfer to {b['id']}", f"tr-out:{ref}")
            _adjust(b["id"], amt, f"transfer from {a['id']}", f"tr-in:{ref}")
    except InsufficientFunds:
        return "❌ Göndərənin balansı kifayət etmir."
    return f"✅ {money(amt)}: {U(a)} ➜ {U(b)}"


@acmd("deposits", "/deposits [sayı]", "Son depozitlər", GROUPS[1])
def c_deposits(actor, args, raw):
    n = int(args[0]) if args and args[0].isdigit() else 15
    rows = qa("SELECT * FROM txs WHERE reason LIKE 'deposit%' ORDER BY id DESC LIMIT ?", n)
    return "🏦 <b>Son depozitlər</b>\n" + ("\n".join(f"• <code>{r['user_id']}</code> +{money(r['amount'])} • {fmt_ts(r['created'])}" for r in rows) or "— yoxdur —")


@acmd("richest", "/richest [sayı]", "Ən çox balansı olanlar", GROUPS[1])
def c_richest(actor, args, raw):
    n = int(args[0]) if args and args[0].isdigit() else 10
    rows = qa("SELECT * FROM users WHERE is_admin=0 ORDER BY balance DESC LIMIT ?", n)
    return "💎 <b>Ən yüksək balanslar</b>\n" + "\n".join(f"{i}. {U(u)} • {money(u['balance'])}" for i, u in enumerate(rows, 1))


@acmd("txs", "/txs ID|@user [sayı]", "Balans tarixçəsi", GROUPS[1], min_args=1)
def c_txs(actor, args, raw):
    u = resolve(args[0])
    if not u:
        return ERR_U
    n = int(args[1]) if len(args) > 1 and args[1].isdigit() else 15
    rows = qa("SELECT * FROM txs WHERE user_id=? ORDER BY id DESC LIMIT ?", u["id"], n)
    return f"🧾 <b>{U(u)}</b>\n" + ("\n".join(f"• {'+' if r['amount'] >= 0 else ''}{money(r['amount'])} • {esc(r['reason'])} • {fmt_ts(r['created'])}" for r in rows) or "— yoxdur —")


# ── 🚫 Moderasiya
@acmd("ban", "/ban ID|@user [səbəb]", "Banla", GROUPS[2], min_args=1, aliases=("blokla",))
def c_ban(actor, args, raw):
    u = resolve(args[0], create=True)
    if not u:
        return ERR_U
    if is_admin(u["id"]):
        return "⛔ Adminləri banlamaq olmaz."
    reason = " ".join(args[1:]) or "—"
    ex("UPDATE users SET banned=1, ban_reason=? WHERE id=?", reason, u["id"])
    notify(u["id"], f"🚫 Siz botdan banlandınız.\n📝 Səbəb: {esc(reason)}")
    return f"🚫 {U(u)} banlandı. Səbəb: {esc(reason)}"


@acmd("unban", "/unban ID|@user", "Banı aç", GROUPS[2], min_args=1)
def c_unban(actor, args, raw):
    u = resolve(args[0])
    if not u:
        return ERR_U
    ex("UPDATE users SET banned=0, ban_reason='' WHERE id=?", u["id"])
    notify(u["id"], "✅ Banınız açıldı. /start yazın.")
    return f"✅ {U(u)} bani açıldı."


@acmd("mute", "/mute ID|@user [dəqiqə]", "Susdur (0/boş = daimi)", GROUPS[2], min_args=1)
def c_mute(actor, args, raw):
    u = resolve(args[0], create=True)
    if not u:
        return ERR_U
    if is_admin(u["id"]):
        return "⛔ Adminləri susdurmaq olmaz."
    mins = int(args[1]) if len(args) > 1 and args[1].isdigit() else 0
    ex("UPDATE users SET muted_until=? WHERE id=?", now() + mins * 60 if mins else -1, u["id"])
    notify(u["id"], "🔇 Hesabınız susdurulub — nömrə ala bilməzsiniz" + (f" ({mins} dəq)." if mins else "."))
    return f"🔇 {U(u)} susdurulub ({'daimi' if not mins else str(mins) + ' dəq'})."


@acmd("unmute", "/unmute ID|@user", "Susdurmanı aç", GROUPS[2], min_args=1)
def c_unmute(actor, args, raw):
    u = resolve(args[0])
    if not u:
        return ERR_U
    ex("UPDATE users SET muted_until=0 WHERE id=?", u["id"])
    notify(u["id"], "🔊 Susdurulmanız açıldı.")
    return f"🔊 {U(u)} susdurulması açıldı."


@acmd("banned", "/banned", "Banlı istifadəçilər", GROUPS[2])
def c_banned(actor, args, raw):
    rows = qa("SELECT * FROM users WHERE banned=1")
    return "🚫 <b>Banlılar</b>\n" + ("\n".join(f"• {U(u)} — {esc(u['ban_reason'])}" for u in rows) or "— yoxdur —")


@acmd("muted", "/muted", "Susdurulanlar", GROUPS[2])
def c_muted(actor, args, raw):
    rows = qa("SELECT * FROM users WHERE muted_until=-1 OR muted_until>?", now())
    return "🔇 <b>Susdurulanlar</b>\n" + ("\n".join(f"• {U(u)}" for u in rows) or "— yoxdur —")


@acmd("warn", "/warn ID|@user [səbəb]", "Xəbərdarlıq (3-də avto-ban)", GROUPS[2], min_args=1)
def c_warn(actor, args, raw):
    u = resolve(args[0], create=True)
    if not u:
        return ERR_U
    if is_admin(u["id"]):
        return "⛔ Adminlərə warn verilmir."
    w = u["warns"] + 1
    reason = " ".join(args[1:]) or "—"
    ex("UPDATE users SET warns=? WHERE id=?", w, u["id"])
    if w >= 3:
        ex("UPDATE users SET banned=1, ban_reason=? WHERE id=?", "3 xəbərdarlıq", u["id"])
        notify(u["id"], "🚫 3 xəbərdarlıq topladınız və banlandınız.")
        return f"⚠️ {U(u)} warn 3/3 — avtomatik banlandı."
    notify(u["id"], f"⚠️ Xəbərdarlıq {w}/3\n📝 Səbəb: {esc(reason)}")
    return f"⚠️ {U(u)} warn {w}/3"


@acmd("unwarn", "/unwarn ID|@user", "Xəbərdarlığı azalt", GROUPS[2], min_args=1)
def c_unwarn(actor, args, raw):
    u = resolve(args[0])
    if not u:
        return ERR_U
    ex("UPDATE users SET warns=MAX(0,warns-1) WHERE id=?", u["id"])
    return f"✅ {U(u)} warn: {max(0, u['warns'] - 1)}/3"


@acmd("note", "/note ID|@user MƏTN", "İstifadəçiyə qeyd yaz", GROUPS[2], min_args=2)
def c_note(actor, args, raw):
    u = resolve(args[0])
    if not u:
        return ERR_U
    ex("INSERT INTO notes(user_id,text,admin_id,created) VALUES(?,?,?,?)", u["id"], " ".join(args[1:]), actor, now())
    return f"📝 {U(u)} üçün qeyd əlavə olundu."


@acmd("notes", "/notes ID|@user", "İstifadəçi qeydləri", GROUPS[2], min_args=1)
def c_notes(actor, args, raw):
    u = resolve(args[0])
    if not u:
        return ERR_U
    rows = qa("SELECT * FROM notes WHERE user_id=? ORDER BY id DESC LIMIT 20", u["id"])
    return f"📝 <b>{U(u)}</b>\n" + ("\n".join(f"• {esc(n['text'])} <i>({fmt_ts(n['created'])})</i>" for n in rows) or "— qeyd yoxdur —")


# ── 👥 İstifadəçilər
@acmd("user", "/user ID|@user", "İstifadəçi məlumatı", GROUPS[3], min_args=1)
def c_user(actor, args, raw):
    u = resolve(args[0])
    if not u:
        return ERR_U
    done = q1("SELECT COUNT(*) c, COALESCE(SUM(charged_qep),0) s FROM orders WHERE user_id=? AND status='DONE'", u["id"])
    mu = "yox" if not u["muted_until"] else ("daimi" if u["muted_until"] == -1 else ("bəli, " + fmt_ts(u["muted_until"]) if u["muted_until"] > now() else "yox"))
    return (f"👤 <b>İstifadəçi</b>\n🆔 <code>{u['id']}</code>\n📛 {esc(full_name(u))}\n🔗 {esc(uname(u))}\n"
            f"💰 Balans: <b>{bal_text(u['id'], u)}</b>\n📦 Nömrə: <b>{done['c']}</b> • 💸 Xərclənən: <b>{money(done['s'])}</b>\n"
            f"⚠️ Warn: {u['warns']}/3\n🚫 Ban: {'bəli — ' + esc(u['ban_reason']) if u['banned'] else 'yox'}\n🔇 Mute: {mu}\n"
            f"👑 Admin: {'bəli' if is_admin(u['id']) else 'yox'}\n📅 Qeydiyyat: {fmt_ts(u['joined'])}\n🕒 Son aktivlik: {fmt_ts(u['last_seen'])}")


@acmd("users", "/users [səhifə]", "İstifadəçi siyahısı", GROUPS[3])
def c_users(actor, args, raw):
    p = max(1, int(args[0])) if args and args[0].isdigit() else 1
    total = q1("SELECT COUNT(*) c FROM users")["c"]
    rows = qa("SELECT * FROM users ORDER BY id DESC LIMIT 15 OFFSET ?", (p - 1) * 15)
    return f"👥 <b>İstifadəçilər</b> ({total}) • səhifə {p}\n" + "\n".join(f"• {U(u)} • {money(u['balance'])}" for u in rows)


@acmd("find", "/find MƏTN", "Ad/username üzrə axtar", GROUPS[3], min_args=1)
def c_find(actor, args, raw):
    like = f"%{raw.strip().lstrip('@').lower()}%"
    rows = qa("SELECT * FROM users WHERE lower(username) LIKE ? OR lower(first_name) LIKE ? OR lower(last_name) LIKE ? "
              "OR CAST(id AS TEXT) LIKE ? LIMIT 20", like, like, like, like)
    return "🔎 <b>Nəticə</b>\n" + ("\n".join(f"• {U(u)}" for u in rows) or "— tapılmadı —")


@acmd("newusers", "/newusers [sayı]", "Ən yeni istifadəçilər", GROUPS[3])
def c_newusers(actor, args, raw):
    n = int(args[0]) if args and args[0].isdigit() else 10
    rows = qa("SELECT * FROM users ORDER BY joined DESC LIMIT ?", n)
    return "🆕 <b>Yeni istifadəçilər</b>\n" + "\n".join(f"• {U(u)} • {fmt_ts(u['joined'])}" for u in rows)


@acmd("active24", "/active24", "Son 24 saatda aktiv olanlar", GROUPS[3])
def c_active24(actor, args, raw):
    rows = qa("SELECT * FROM users WHERE last_seen>? ORDER BY last_seen DESC", now() - 86400)
    return f"🟢 <b>Son 24 saat: {len(rows)}</b>\n" + "\n".join(f"• {U(u)} • {fmt_ts(u['last_seen'])}" for u in rows[:25])


# ── 📦 Sifarişlər
@acmd("orders", "/orders ID|@user [sayı]", "İstifadəçinin sifarişləri", GROUPS[4], min_args=1)
def c_orders(actor, args, raw):
    u = resolve(args[0])
    if not u:
        return ERR_U
    n = int(args[1]) if len(args) > 1 and args[1].isdigit() else 10
    rows = qa("SELECT * FROM orders WHERE user_id=? ORDER BY id DESC LIMIT ?", u["id"], n)
    return f"📦 <b>{U(u)}</b>\n" + ("\n".join(order_line(o) for o in rows) or "— yoxdur —")


@acmd("lastorders", "/lastorders [sayı]", "Son sifarişlər", GROUPS[4])
def c_lastorders(actor, args, raw):
    n = int(args[0]) if args and args[0].isdigit() else 15
    return "📋 <b>Son sifarişlər</b>\n" + "\n".join(order_line(o) for o in qa("SELECT * FROM orders ORDER BY id DESC LIMIT ?", n))


@acmd("order", "/order ID", "Sifariş detalları", GROUPS[4], min_args=1)
def c_order(actor, args, raw):
    o = get_order(int(args[0])) if args[0].isdigit() else None
    if not o:
        return "❌ Sifariş tapılmadı."
    return (f"📦 <b>Sifariş #{o['id']}</b> {ICON.get(o['status'], '')} {o['status']}\n👤 <code>{o['user_id']}</code>\n"
            f"📞 {esc(plus(o['number']) or '—')}\n🔧 {esc(o['service_name'])} • 🌍 {esc(o['country_name'])}\n"
            f"💬 SMS: {esc(o['code'] or '—')}\n💸 Satış: {money(o['sell_qep'])} • Maya: {money(o['cost_qep'])} • Çıxılan: {money(o['charged_qep'])}\n"
            f"🔁 Qaytarılıb: {'bəli' if o['refunded'] else 'xeyr'}\n📝 {esc(o['reason'] or '—')}\n"
            f"🆔 API: <code>{esc(o['api_order'] or '—')}</code>\n🧾 request_id: <code>{esc(o['request_id'])}</code>\n🕒 {fmt_ts(o['created'])}")


@acmd("active", "/active", "Aktiv (gözləyən) sifarişlər", GROUPS[4])
def c_active(actor, args, raw):
    rows = qa(f"SELECT * FROM orders WHERE status IN ({','.join('?' * len(ACTIVE))}) ORDER BY id DESC LIMIT 30", *ACTIVE)
    return f"⏳ <b>Aktiv: {len(rows)}</b>\n" + "\n".join(order_line(o) for o in rows)


@acmd("refund", "/refund SİFARİŞ_ID", "Sifarişin pulunu qaytar (yalnız DB, bir dəfə)", GROUPS[4], min_args=1)
def c_refund(actor, args, raw):
    o = get_order(int(args[0])) if args[0].isdigit() else None
    if not o:
        return "❌ Sifariş tapılmadı."
    if refund_order(o["id"], "FAILED", f"Admin geri qaytardı ({actor})"):
        notify(o["chat_id"], f"💸 #{o['id']} üzrə {money(o['charged_qep'])} balansınıza qaytarıldı.")
        return f"✅ #{o['id']} qaytarıldı: {money(o['charged_qep'])}"
    return "ℹ️ Artıq qaytarılıb və ya sifariş DONE-dur."


@acmd("cancelorder", "/cancelorder SİFARİŞ_ID", "Provayderdə ləğv et + qaytar", GROUPS[4], min_args=1)
def c_cancelorder(actor, args, raw):
    o = get_order(int(args[0])) if args[0].isdigit() else None
    if not o or not o["api_order"]:
        return "❌ Sifariş tapılmadı və ya API ID yoxdur."
    try:
        r = api("sms_cancel", order=o["api_order"])
    except (ApiError, ApiUncertain) as e:
        return f"⚠️ Provayder: {esc(e)}"
    if isinstance(r, dict) and str(r.get("status", "")).upper() == "CANCELED":
        if refund_order(o["id"], "CANCELED", f"Admin ləğv etdi ({actor})"):
            finalize_cancel(o["id"], "Admin ləğv etdi")
        return f"✅ #{o['id']} ləğv edildi."
    return "⚠️ Provayder ləğv etmədi."


@acmd("failed", "/failed [sayı]", "Ləğv/uğursuz sifarişlər", GROUPS[4])
def c_failed(actor, args, raw):
    n = int(args[0]) if args and args[0].isdigit() else 15
    rows = qa("SELECT * FROM orders WHERE status IN ('CANCELED','FAILED') ORDER BY id DESC LIMIT ?", n)
    return "❌ <b>Ləğv/uğursuz</b>\n" + ("\n".join(order_line(o) for o in rows) or "— yoxdur —")


@acmd("checking", "/checking", "CHECKING / naməlum sifarişlər", GROUPS[4])
def c_checking(actor, args, raw):
    rows = qa("SELECT * FROM orders WHERE status IN ('CHECKING','UNCERTAIN','BUYING') ORDER BY id DESC")
    return "🔎 <b>Yoxlanılanlar</b>\n" + ("\n".join(order_line(o) for o in rows) or "— yoxdur —")


# ── 📊 Statistika
def _sales(since):
    r = q1("""SELECT COUNT(*) n, COALESCE(SUM(charged_qep),0) rev,
              COALESCE(SUM(CASE WHEN charged_qep>0 THEN cost_qep ELSE 0 END),0) cost,
              COALESCE(SUM(CASE WHEN charged_qep=0 THEN cost_qep ELSE 0 END),0) admin_cost
              FROM orders WHERE status='DONE' AND created>=?""", since)
    return r, r["rev"] - r["cost"]


@acmd("stats", "/stats", "Ümumi statistika", GROUPS[5], aliases=("stat", "statistika"))
def c_stats(actor, args, raw):
    users = q1("SELECT COUNT(*) c FROM users")["c"]
    banned = q1("SELECT COUNT(*) c FROM users WHERE banned=1")["c"]
    cnt = {r["status"]: r["c"] for r in qa("SELECT status, COUNT(*) c FROM orders GROUP BY status")}
    tot, tprofit = _sales(0)
    td, dprofit = _sales(day_start())
    dep = q1("SELECT COALESCE(SUM(amount),0) s FROM txs WHERE reason LIKE 'deposit%'")["s"]
    return (f"📊 <b>İstatistika</b>\n\n👥 İstifadəçi: <b>{users}</b> (🚫 {banned})\n"
            f"📦 Sifariş: ✅ {cnt.get('DONE', 0)} • ❌ {cnt.get('CANCELED', 0) + cnt.get('FAILED', 0)} • ⏳ {sum(cnt.get(s, 0) for s in ACTIVE)}\n\n"
            f"📅 <b>Bu gün:</b> {td['n']} satış • gəlir {money(td['rev'])} • mənfəət <b>{money(dprofit)}</b>\n"
            f"📈 <b>Ümumi:</b> gəlir {money(tot['rev'])} • maya {money(tot['cost'])} • mənfəət <b>{money(tprofit)}</b>\n"
            f"👑 Admin sifarişlərinin xərci: {money(tot['admin_cost'])}\n🏦 Ümumi depozit: {money(dep)}")


@acmd("today", "/today", "Bugünkü satışlar", GROUPS[5])
def c_today(actor, args, raw):
    r, p = _sales(day_start())
    return f"📅 <b>Bu gün</b>\n📦 Satış: {r['n']}\n💵 Gəlir: {money(r['rev'])}\n🏷 Maya: {money(r['cost'])}\n💎 Mənfəət: <b>{money(p)}</b>"


@acmd("income", "/income [gün]", "Son N gün gəlir/mənfəət", GROUPS[5])
def c_income(actor, args, raw):
    d = int(args[0]) if args and args[0].isdigit() else 30
    r, p = _sales(now() - d * 86400)
    return f"💵 <b>Son {d} gün</b>\n📦 Satış: {r['n']}\n💵 Gəlir: {money(r['rev'])}\n🏷 Maya: {money(r['cost'])}\n💎 Mənfəət: <b>{money(p)}</b>"


@acmd("topusers", "/topusers [sayı]", "Ən çox xərcləyənlər", GROUPS[5])
def c_topusers(actor, args, raw):
    n = int(args[0]) if args and args[0].isdigit() else 10
    rows = qa("SELECT user_id, COUNT(*) c, SUM(charged_qep) s FROM orders WHERE status='DONE' GROUP BY user_id ORDER BY s DESC, c DESC LIMIT ?", n)
    return "🏆 <b>Top istifadəçilər</b>\n" + "\n".join(f"{i}. <code>{r['user_id']}</code> • {r['c']} nömrə • {money(r['s'])}" for i, r in enumerate(rows, 1))


@acmd("topcountries", "/topcountries [sayı]", "Ən çox satılan ölkələr", GROUPS[5])
def c_topcountries(actor, args, raw):
    n = int(args[0]) if args and args[0].isdigit() else 10
    rows = qa("SELECT country_name, iso, COUNT(*) c FROM orders WHERE status='DONE' GROUP BY country_name ORDER BY c DESC LIMIT ?", n)
    return "🌍 <b>Top ölkələr</b>\n" + "\n".join(f"{i}. {flag(r['iso'])} {esc(r['country_name'])} • {r['c']}" for i, r in enumerate(rows, 1))


@acmd("topservices", "/topservices [sayı]", "Ən çox satılan servislər", GROUPS[5])
def c_topservices(actor, args, raw):
    n = int(args[0]) if args and args[0].isdigit() else 10
    rows = qa("SELECT service_name, COUNT(*) c FROM orders WHERE status='DONE' GROUP BY service_name ORDER BY c DESC LIMIT ?", n)
    return "🔧 <b>Top servislər</b>\n" + "\n".join(f"{i}. {esc(r['service_name'])} • {r['c']}" for i, r in enumerate(rows, 1))


@acmd("apistatus", "/apistatus", "PanelPlus API durumu", GROUPS[5])
def c_apistatus(actor, args, raw):
    t, lines = time.time(), ["📡 <b>API Durumu</b>"]
    try:
        bal = api_balance_qep()
        lines.append(f"🟢 Balans sorğusu: OK • {money(bal) if bal is not None else '?'} ({int((time.time() - t) * 1000)} ms)")
    except (ApiError, ApiUncertain) as e:
        lines.append(f"🔴 Balans sorğusu: {esc(e)}")
    try:
        svc = api_services(force=True)
        lines.append(f"🟢 Servis siyahısı: {len(svc)} servis")
    except (ApiError, ApiUncertain) as e:
        lines.append(f"🔴 Servis siyahısı: {esc(e)}")
    with _calls_lock:
        last = len([x for x in _calls if time.time() - x < 60])
    lines.append(f"🔗 {esc(PANELPLUS_URL)}\n📊 Son 60 san. sorğu: {last}/120")
    return "\n".join(lines)


@acmd("apibal", "/apibal", "PanelPlus balansı", GROUPS[5], aliases=("pp_balans",))
def c_apibal(actor, args, raw):
    try:
        b = api_balance_qep()
        return f"💰 PanelPlus balansı: <b>{money(b) if b is not None else '?'}</b>"
    except (ApiError, ApiUncertain) as e:
        return f"🔴 {esc(e)}"


@acmd("ping", "/ping", "Bot gecikməsi", GROUPS[5])
def c_ping(actor, args, raw):
    t = time.time()
    tg("getMe")
    return f"🏓 Pong • {int((time.time() - t) * 1000)} ms"


@acmd("uptime", "/uptime", "Bot nə qədər işləyir", GROUPS[5])
def c_uptime(actor, args, raw):
    s = int(time.time() - START_TIME)
    return f"⏱ İşləmə müddəti: <b>{s // 86400}g {s % 86400 // 3600}s {s % 3600 // 60}d</b>"


# ── 📢 Mesaj
def broadcast(rows, text):
    sent = fail = 0
    for u in rows:
        if notify(u["id"], text):
            sent += 1
        else:
            fail += 1
        time.sleep(0.05)
    return sent, fail


@acmd("broadcast", "/broadcast MƏTN", "Hamıya mesaj göndər", GROUPS[6], min_args=1, aliases=("yayim",))
def c_broadcast(actor, args, raw):
    s, f = broadcast(qa("SELECT id FROM users WHERE banned=0"), raw.strip())
    return f"📢 Göndərildi: ✅ {s} • ❌ {f}"


@acmd("bcadmins", "/bcadmins MƏTN", "Yalnız adminlərə mesaj", GROUPS[6], min_args=1)
def c_bcadmins(actor, args, raw):
    rows = qa("SELECT id FROM users WHERE is_admin=1") + [{"id": ADMIN_ID}]
    s, f = broadcast(rows, raw.strip())
    return f"📢 Adminlərə: ✅ {s} • ❌ {f}"


@acmd("msg", "/msg ID|@user MƏTN", "İstifadəçiyə mesaj", GROUPS[6], min_args=2)
def c_msg(actor, args, raw):
    u = resolve(args[0])
    if not u:
        return ERR_U
    ok = notify(u["id"], raw.split(None, 1)[1])
    return "✅ Göndərildi." if ok else "❌ Göndərilmədi (istifadəçi botu bloklayıb ola bilər)."


@acmd("duyuru", "/duyuru MƏTN", "Duyuru yaz + hamıya göndər (/start-da da görünür)", GROUPS[6], min_args=1, aliases=("announce",))
def c_duyuru(actor, args, raw):
    set_setting("announce", raw.strip())
    s, f = broadcast(qa("SELECT id FROM users WHERE banned=0"), f"📣 <b>Duyuru</b>\n\n{raw.strip()}")
    return f"📣 Duyuru saxlandı və göndərildi: ✅ {s} • ❌ {f}"


@acmd("duyurusave", "/duyurusave MƏTN", "Duyuru yaz (göndərmədən saxla)", GROUPS[6], min_args=1)
def c_duyurusave(actor, args, raw):
    set_setting("announce", raw.strip())
    send(actor, f"✅ Duyuru saxlandı:\n\n{raw.strip()}",
         kb([[("📢 Hamıya göndər", "ann:send")], [("◀️ Duyuru menyusu", "adm:annc")]]))
    return ""


@acmd("duyurusil", "/duyurusil", "Duyurunu sil", GROUPS[6], aliases=("delannounce",))
def c_duyurusil(actor, args, raw):
    del_setting("announce")
    return "🗑 Duyuru silindi."


@acmd("duyurugor", "/duyurugor", "Cari duyuru", GROUPS[6])
def c_duyurugor(actor, args, raw):
    a = get_setting("announce")
    return f"📣 <b>Cari duyuru:</b>\n{a}" if a else "ℹ️ Duyuru yoxdur."


# ── ⚙️ Sistem
@acmd("maintenance", "/maintenance on|off", "Təmir rejimi", GROUPS[7], min_args=1)
def c_maintenance(actor, args, raw):
    on = args[0].lower() in ("on", "1", "aç", "ac", "yandır")
    set_setting("maintenance", "1" if on else "0")
    return f"🛠 Təmir rejimi: <b>{'AÇIQ' if on else 'bağlı'}</b>"


@acmd("setmarkup", "/setmarkup MƏBLƏĞ", "Sabit əlavə (AZN)", GROUPS[7], min_args=1)
def c_setmarkup(actor, args, raw):
    v = to_qep(args[0])
    if v is None or v < 0:
        return ERR_A
    set_setting("markup_qep", v)
    return f"💲 Markup: <b>{money(v)}</b> (yeni sifarişlərə aiddir)"


@acmd("settimeout", "/settimeout DƏQİQƏ", "SMS gözləmə müddəti", GROUPS[7], min_args=1)
def c_settimeout(actor, args, raw):
    if not args[0].isdigit() or not 1 <= int(args[0]) <= 120:
        return "❌ 1-120 arası dəqiqə yazın."
    set_setting("timeout_min", args[0])
    return f"⏱ SMS gözləmə: <b>{args[0]} dəq</b>"


@acmd("setmaxactive", "/setmaxactive SAY", "İstifadəçi başına aktiv nömrə limiti", GROUPS[7], min_args=1)
def c_setmaxactive(actor, args, raw):
    if not args[0].isdigit() or int(args[0]) < 1:
        return "❌ Müsbət ədəd yazın."
    set_setting("max_active", args[0])
    return f"📦 Maks. aktiv: <b>{args[0]}</b>"


def _block(key, name, add):
    lst = get_list(key)
    if key == "blocked_countries":
        iso = iso_of_name(name)
        name = fold(ISO_NAMES[iso][0]) if False else fold(name)
    else:
        name = fold(name)
    if add and name not in lst:
        lst.append(name)
    if not add and name in lst:
        lst.remove(name)
    set_setting(key, json.dumps(lst))
    return name


@acmd("blockcountry", "/blockcountry ÖLKƏ", "Ölkəni bağla", GROUPS[7], min_args=1)
def c_blockcountry(actor, args, raw):
    iso = iso_of_name(raw)
    names = ISO_NAMES.get(iso, [fold(raw)])
    for n in names:
        _block("blocked_countries", n, True)
    return f"🚫 Bağlandı: <b>{esc(', '.join(names))}</b>"


@acmd("unblockcountry", "/unblockcountry ÖLKƏ", "Ölkəni aç", GROUPS[7], min_args=1)
def c_unblockcountry(actor, args, raw):
    iso = iso_of_name(raw)
    names = ISO_NAMES.get(iso, [fold(raw)])
    for n in names:
        _block("blocked_countries", n, False)
    return f"✅ Açıldı: <b>{esc(', '.join(names))}</b>"


@acmd("blockservice", "/blockservice SERVİS", "Servisi bağla", GROUPS[7], min_args=1)
def c_blockservice(actor, args, raw):
    return f"🚫 Bağlandı: <b>{esc(_block('blocked_services', raw, True))}</b>"


@acmd("unblockservice", "/unblockservice SERVİS", "Servisi aç", GROUPS[7], min_args=1)
def c_unblockservice(actor, args, raw):
    return f"✅ Açıldı: <b>{esc(_block('blocked_services', raw, False))}</b>"


@acmd("blocked", "/blocked", "Bağlı ölkə/servislər", GROUPS[7])
def c_blocked(actor, args, raw):
    return (f"🚫 <b>Ölkələr:</b> {esc(', '.join(get_list('blocked_countries')) or '—')}\n"
            f"🚫 <b>Servislər:</b> {esc(', '.join(get_list('blocked_services')) or '—')}")


@acmd("refreshcache", "/refreshcache", "Keşi təmizlə", GROUPS[7])
def c_refreshcache(actor, args, raw):
    with _cache_lock:
        _cache.clear()
    return "♻️ Keş təmizləndi."


@acmd("services", "/services [axtarış]", "PanelPlus servisləri", GROUPS[7])
def c_services(actor, args, raw):
    try:
        svc = api_services()
    except (ApiError, ApiUncertain) as e:
        return f"🔴 {esc(e)}"
    if raw.strip():
        svc = match_services(raw, svc)
    return f"🔧 <b>Servislər ({len(svc)})</b>\n" + "\n".join(f"• {esc(s['name'])} — <code>{esc(s['code'])}</code>" for s in svc[:40])


@acmd("prices", "/prices SERVİS ÖLKƏ", "Qiymət yoxla (maya və satış)", GROUPS[7], min_args=2)
def c_prices(actor, args, raw):
    try:
        svc = match_services(args[0], api_services()) or ([p] if (p := probe_service(args[0])) else [])
        if not svc:
            return "❌ Servis tapılmadı."
        res = find_country_rows(" ".join(args[1:]), svc[0]["code"])
    except (ApiError, ApiUncertain) as e:
        return f"🔴 {esc(e)}"
    return f"💲 <b>{esc(svc[0]['name'])}</b>\n" + ("\n".join(f"• {esc(r['cname'])} — maya {money(r['cost'])} ➜ satış <b>{money(sell_price(r['cost']))}</b> • stok {r['count']}" for r in res[:15]) or "— tapılmadı —")


@acmd("setwelcome", "/setwelcome MƏTN", "Başlanğıc mesajını dəyiş", GROUPS[7], min_args=1)
def c_setwelcome(actor, args, raw):
    set_setting("welcome", raw.strip())
    return "✅ Salamlama mesajı yeniləndi."


@acmd("export", "/export", "Bazanı fayl kimi göndər", GROUPS[7], owner=True, aliases=("backup",))
def c_export(actor, args, raw):
    path = os.path.join(tempfile.gettempdir(), f"backup_{uuid.uuid4().hex[:8]}.db")
    dst = sqlite3.connect(path)
    with DBL:
        conn.backup(dst)
    dst.close()
    with open(path, "rb") as f:
        data = f.read()
    os.remove(path)
    ok = send_document(actor, f"backup_{datetime.now(AZT):%Y%m%d_%H%M}.db", data)
    return "📦 Baza göndərildi." if ok else "❌ Göndərilmədi."


@acmd("logs", "/logs [sətir]", "Son log sətirləri", GROUPS[7], owner=True)
def c_logs(actor, args, raw):
    n = int(args[0]) if args and args[0].isdigit() else 25
    try:
        with open(LOG_PATH, encoding="utf-8") as f:
            lines = f.readlines()[-n:]
    except OSError:
        return "— log yoxdur —"
    return "<pre>" + esc("".join(lines)[-3000:]) + "</pre>"


@acmd("panel", "/panel", "Admin panel", GROUPS[7])
def c_panel(actor, args, raw):
    t, k = panel_view()
    send(actor, t, k)
    return ""


@acmd("adminhelp", "/adminhelp", "Bütün admin əmrləri", GROUPS[7])
def c_adminhelp(actor, args, raw):
    seen, out = set(), ["📜 <b>Admin əmrləri</b>"]
    for g in GROUPS:
        items = [m for n, m in ADMIN_CMDS.items() if m["group"] == g and n == m["name"] and n not in seen]
        if items:
            out.append(f"\n<b>{g}</b>")
            out += [f"<code>{esc(m['usage'])}</code> — {esc(m['desc'])}" for m in items]
            seen.update(m["name"] for m in items)
    out.append(f"\n<i>Cəmi: {len(seen)} əmr</i>")
    return "\n".join(out)


# ═══════════════════════════════════ DISPATCH ═══════════════════════════════════
def on_message(m):
    chat = m["chat"]
    fr, text = m.get("from"), (m.get("text") or "").strip()
    if chat["type"] != "private" or not fr or fr.get("is_bot") or not text:
        return
    parts = text.split()
    if parts[0].split("@")[0].lower() == "/start" and len(parts) > 1:
        capture_ref(fr, parts[1])
    u = guard(fr, chat["id"])
    if not u:
        return
    uid, cid = fr["id"], chat["id"]
    if text.startswith("/"):
        name = text.split(maxsplit=1)[0][1:].split("@")[0].lower()
        if name == "start":
            return cmd_start(uid, cid, fr)
        if name in ("menu", "cancel"):
            clear_state(uid)
            return send(cid, "✅ Əməliyyat ləğv edildi.", main_kb(uid))
        if name == "id":
            return send(cid, f"🆔 ID-niz: <code>{uid}</code>")
        if name == "help":
            return cmd_help(uid, cid)
        admin_command(uid, cid, text)
        return
    if text == BTN_BUY:
        if is_muted(u) and not is_admin(uid):
            return send(cid, "🔇 Sizin hesabınız susdurulub, nömrə ala bilməzsiniz.")
        return ask_service(uid, cid)
    if text == BTN_BAL:
        return btn_balance(uid, cid, fr)
    if text == BTN_TOPUP:
        return btn_topup(uid, cid)
    if text == BTN_BONUS:
        return btn_bonus(uid, cid)
    if text == BTN_REF:
        return btn_ref(uid, cid)
    if text == BTN_ADMIN:
        clear_state(uid)
        if is_admin(uid):
            t, k = panel_view()
            send(cid, t, k)
        return
    st, _ = get_state(uid)
    if st == "wiz":
        return wiz_step(uid, cid, text)
    if st == "service":
        return on_service_text(uid, cid, text)
    if st == "country":
        return on_country_text(uid, cid, text)
    send(cid, "👇 Menyudan istifadə edin.", main_kb(uid))


def on_callback(cb):
    fr, msg, data = cb["from"], cb.get("message"), cb.get("data") or ""
    if not msg or msg["chat"]["type"] != "private" or fr.get("is_bot"):
        return
    if not guard(fr, msg["chat"]["id"], cb["id"], allow_unjoined=(data == "chk")):
        return
    uid, chat, mid = fr["id"], msg["chat"]["id"], msg["message_id"]
    if data == "noop":
        return answer_cb(cb["id"])
    if data == "chk":
        return cb_check(cb)
    if data in ("newnum", "chsvc"):
        answer_cb(cb["id"])
        return ask_service(uid, chat)
    if data.startswith("svc:"):
        answer_cb(cb["id"])
        return cb_svc(uid, chat, mid, data[4:])
    if data.startswith("buy:"):
        return cb_buy(cb)
    if data.startswith("cancel:"):
        return cb_cancel(cb)
    if data.startswith("adm:"):
        return adm_cb(cb)
    if data.startswith("ann:"):
        return ann_cb(cb)
    if data.startswith("ordp:"):
        return ord_cb(cb)
    if data.startswith("unc:"):
        return unc_cb(cb)
    answer_cb(cb["id"])


def safe_handle(upd):
    try:
        if "message" in upd:
            fr = upd["message"].get("from") or {}
            with lock_for(("seq", fr.get("id"))):
                on_message(upd["message"])
        elif "callback_query" in upd:
            cb = upd["callback_query"]
            if (cb.get("data") or "").startswith("buy:"):     # uzun sürən alış digər düymələri bloklamasın
                on_callback(cb)
            else:
                with lock_for(("seq", cb["from"]["id"])):
                    on_callback(cb)
    except Exception:
        log.exception("update xətası")


def recover_orders():
    for o in qa("SELECT id FROM orders WHERE status IN ('WAITING','CHECKING')"):
        start_watch(o["id"])
    for o in qa("SELECT * FROM orders WHERE status='BUYING'"):
        ex("UPDATE orders SET status='UNCERTAIN' WHERE id=?", o["id"])
        notify_admin(f"❓ Bot yenidən başladı: sifariş #{o['id']} alış zamanı yarımçıq qalıb.",
                     kb([[("🔁 Yenidən yoxla", f"unc:retry:{o['id']}"), ("💸 Geri qaytar", f"unc:refund:{o['id']}")]]))


def main():
    init_db()
    global BOT_USERNAME
    me = tg("getMe")
    BOT_USERNAME = me.get("username") or ""
    try:
        tg("deleteWebhook", drop_pending_updates=False)
    except TgError as e:
        log.warning("deleteWebhook: %s", e)
    recover_orders()
    log.info("Bot işə düşdü: @%s | admin komandaları: %d | ölkə: %d", me.get("username"),
             len({m['name'] for m in ADMIN_CMDS.values()}), len(ISO_NAMES))
    pool = ThreadPoolExecutor(max_workers=32)
    offset = 0
    while True:
        try:
            ups = tg("getUpdates", _timeout=35, offset=offset, timeout=15,
                     allowed_updates=["message", "callback_query"])
        except TgError as e:
            log.warning("getUpdates: %s", e)
            time.sleep(3)
            continue
        for up in ups:
            offset = up["update_id"] + 1
            pool.submit(safe_handle, up)


if __name__ == "__main__":
    main()
