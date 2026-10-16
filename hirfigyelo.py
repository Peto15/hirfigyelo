#!/usr/bin/env python3
"""Hírfigyelő: Telegram-értesítés a USA Tech 100-at, az aranyat és a Bitcoint
megmozgató eseményekről.

Háromféle értesítést küld:
  1. Gazdasági naptár: reggeli összefoglaló a napi nagy amerikai adatokról,
     és figyelmeztetés 15 perccel az esemény előtt.
  2. Friss hírek: nagy hírforrások (Reuters, Bloomberg, CNBC stb.) címei
     kulcsszavakra szűrve.
  3. Nagy árugrás: ha valamelyik eszköz 15 perc alatt szokatlanul nagyot mozdul.
  4. Napi helyzetkép (hétköznap 16:00, napon belüli kereskedéshez): szintek, hátralévő mozgás, scalping-stop,
     várható sáv és irány 21:00-ig (21:15 után a bot kiértékeli, bejött-e, és számolja a találati arányt),
     dollárindex és 10 éves hozam, Bitcoin-ETF pénzáramlás (hírcímekből), mai gyorsjelentések.
  A reggeli összefoglalóban a Fed-tagok beszédei és a nagy techcégek gyorsjelentései is benne vannak;
  a zárás utáni gyorsjelentések előtt 15 perccel figyelmeztetés jön.

Futtatás:
  python3 hirfigyelo.py              egy ellenőrzés (ezt hívja 5 percenként a Mac)
  python3 hirfigyelo.py --beallitas  új címzett(ek) felvétele: aki írt a botnak, megkapja az üzeneteket
  python3 hirfigyelo.py --teszt      próbaüzenet küldése
  python3 hirfigyelo.py --ciklus 3 5 háromszor ellenőriz 5 percenként (a felhőben így fut)
  python3 hirfigyelo.py --naptar     a mai nap eseményeinek kiírása (értesítés nélkül)
  python3 hirfigyelo.py --helyzetkep [--kuld]  a napi helyzetkép kiírása (és elküldése)
"""

import hashlib
import html as htmlmod
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from zoneinfo import ZoneInfo

MAPPA = Path(__file__).resolve().parent
BEALLITASOK = MAPPA / "beallitasok.env"
ALLAPOT = MAPPA / "allapot.json"
NAPLO = MAPPA / "hirfigyelo.log"
NAPTAR_CACHE = MAPPA / ".naptar_cache.json"

HELYI_IDO = ZoneInfo("Europe/Budapest")
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}

# --- Beállítások (itt lehet átírni) -----------------------------------------

REGGELI_OSSZEFOGLALO = "07:30"   # ekkor (vagy az első ébredéskor utána) jön a napi lista
FIGYELMEZTETES_PERC = 15         # ennyivel az esemény előtt szól
CSENDES_IDO = ("23:30", "07:00")  # ebben a sávban nem küld hírt és árugrást
NAPTAR_ORSZAGOK = {"USD"}        # mely országok adatai számítanak
NAPTAR_FONTOSSAG = {"High"}      # csak a nagy hatású események

# Árugrás: (név, forrás, jel, küszöb % 15 perc alatt)
ESZKOZOK = [
    ("USA Tech 100", "yahoo", "NQ=F", 0.6),
    ("Arany", "yahoo", "GC=F", 0.6),
    ("Bitcoin", "yahoo", "BTC-USD", 1.5),  # a Binance a GitHub szervereit letiltja (HTTP 451)
]
ARUGRAS_ABLAK_PERC = 15
ARUGRAS_SZUNET_PERC = 60         # ugyanarra az eszközre ennyi ideig nem szól újra

HELYZETKEP_IDO = "16:00"         # napi helyzetkép (hétköznap), amikor a nyitó sáv (15:30–16:00) kész
US_IDO = ZoneInfo("America/New_York")
US_NYITAS = "09:30"              # az amerikai tőzsde nyitása New York-i idő szerint (magyar idő: 15:30, az
US_ZARAS = "16:00"               # óraátállítások közti 1-2 hétben 14:30); zárás magyar idő szerint 22:00 / 21:00
HELYZETKEP_VEGE = "18:00"        # ha a gép aludt, eddig még elküldi
KERESKEDESI_SAV = ("16:00", "18:00")  # ebben a sávban méri a félórás mozgást
VARAKOZAS_VEGE = "21:00"         # a napvégi várakozás eddig szól; utána a bot kiértékeli
VARAKOZAS_SAV_ARANY = 0.70       # a várható sávot úgy méretezi, hogy a múltban ennyi napon belül maradjon
KIERTEKELES_KESES_PERC = 15      # a Yahoo kb. 10 percet késik, ezért 21:15 után értékel
# (név, Yahoo napi jel, Yahoo 5 perces jel, hírhangulat kulcsa)
HELYZETKEP_ESZKOZOK = [
    ("USA Tech 100", "NQ=F", "NQ=F", "nq"),
    ("Arany", "GC=F", "GC=F", "arany"),
    ("Bitcoin", "BTC-USD", "BTC-USD", "btc"),
]
# Háttér a helyzetképben: (név, Yahoo jel, mértékegység)
HATTER_JELEK = [
    ("Dollárindex (DXY)", "DX-Y.NYB", ""),
    ("10 éves USA-hozam", "^TNX", "%"),
]

# Gyorsjelentések: a USA Tech 100 legnagyobb cégei (Nasdaq-naptár). A *-osok súlya a legnagyobb.
NAGY_TECH = {
    "NVDA": "NVIDIA*", "AAPL": "Apple*", "MSFT": "Microsoft*", "AMZN": "Amazon*", "GOOGL": "Alphabet (Google)*",
    "META": "Meta*", "AVGO": "Broadcom*", "TSLA": "Tesla*", "NFLX": "Netflix", "COST": "Costco", "AMD": "AMD",
    "PLTR": "Palantir", "ASML": "ASML", "CSCO": "Cisco", "TMUS": "T-Mobile US", "MU": "Micron",
    "INTC": "Intel", "QCOM": "Qualcomm", "ADBE": "Adobe", "AMAT": "Applied Materials", "LRCX": "Lam Research",
    "TXN": "Texas Instruments",
}
JELENTES_FIGYELMEZTETES = True   # a zárás utáni jelentések előtt 15 perccel szól

# Hírek: Google Hírek keresések, megbízható forrásokra és erős szavakra szűrve
_CEGEK = "Nvidia OR Apple OR Microsoft OR Amazon OR Alphabet OR Google OR Meta OR Broadcom OR Tesla OR Netflix"
HIR_KERESESEK = [
    '"Federal Reserve" OR Fed OR "rate cut" OR "rate hike" OR Warsh',
    'tariff OR tariffs OR sanctions OR "trade war"',
    'bitcoin OR crypto',
    'Nasdaq OR "stock market" OR "Wall Street"',
    'gold price',
    'war OR missile OR invasion OR "military strike"',
    f'({_CEGEK}) (earnings OR results OR guidance OR revenue)',
    'bitcoin ETF (inflows OR outflows)',
]
MEGBIZHATO_FORRASOK = [
    "Reuters", "Bloomberg", "CNBC", "Wall Street Journal", "WSJ",
    "Financial Times", "Associated Press", "AP News", "MarketWatch",
    "Barron's", "CoinDesk", "The Block", "Yahoo Finance",
]
EROS_SZAVAK = [
    "fed", "federal reserve", "rate cut", "rate hike", "cuts rates", "raises rates",
    "holds rates", "emergency", "tariffs?", "sanctions?", "trade war", "bans?",
    "plunges?", "surges?", "soars?", "crash(es)?", "selloff", "sell-off",
    "tumbles?", "slumps?", "record high", "all-time high", "war", "invasion", "missiles?",
    "strikes? on", "attacks?", "default", "shutdown", "bankrupt(cy)?", "collapses?", "hack(ed)?",
    "sec", "etfs?", "halt(ed|s)?", "recession",
]
EROS_MINTA = re.compile(r"\b(" + "|".join(EROS_SZAVAK) + r")\b", re.IGNORECASE)
# Nagy techcég gyorsjelentéséről szóló cím (ez is átmegy a szűrőn)
_CEG_MINTA = r"(nvidia|apple|microsoft|amazon|alphabet|google|meta|broadcom|tesla|netflix)"
_JEL_SZO = r"(earnings|results|quarter\w*|guidance|outlook|revenue|profit|beats?|miss(es)?|forecasts?)"
JELENTES_MINTA = re.compile(rf"\b{_CEG_MINTA}\b.{{0,80}}\b{_JEL_SZO}\b|\b{_JEL_SZO}\b.{{0,80}}\b{_CEG_MINTA}\b",
                            re.IGNORECASE)
HIR_MAX_ORANKENT = 4
HIR_MAX_KOR_PERC = 30            # ennél régebbi hírről már nem szól

# Irány-szabályok a hírekhez: (csoport, minta, hatás, magyarázat).
# Hatás: +1 felfelé, -1 lefelé a USA Tech 100-ra (nq), az aranyra és a Bitcoinra.
# Egy csoportból csak az első illeszkedő szabály számít (ezért van elöl a pontosabb minta).
_INFL = r"(inflation|cpi|ppi|price pressures?)"
_FEL = r"(surge\w*|soar\w*|jump\w*|rall(y|ies|ied)|record|climb\w*|ris(e|es|ing)|gain\w*|advanc\w*|highest)"
_LE = r"(plung\w*|tumbl\w*|slump\w*|sell-?off|crash\w*|sink\w*|sank|fall\w*|fell|drop\w*|slid\w*|lowest)"
IRANY_SZABALYOK = [
    ("kamat", r"(temper\w*|cool\w*|eas\w*|fad\w*|dim\w*|pare\w*|trim\w*).{0,25}(rate[- ]?hike|hike)", {"nq": 1, "arany": 1, "btc": 1},
     "Csökken a kamatemelés esélye: ez általában felfelé viszi a részvényeket, az aranyat és a Bitcoint."),
    ("kamat", r"(rate[- ]?hikes?|hike bets|rais\w* rates|hawkish|tightening)", {"nq": -1, "arany": -1, "btc": -1},
     "Magasabb kamat jöhet: drágul a hitel, erősödik a dollár, ez a részvényeknek, az aranynak és a Bitcoinnak is rossz."),
    ("kamat", r"(rate[- ]?cuts?|cuts? rates|cutting rates|dovish|easing)", {"nq": 1, "arany": 1, "btc": 1},
     "Kamatcsökkentés jöhet: olcsóbb pénz, gyengébb dollár, ez általában felfelé viszi őket."),
    ("kamat", r"(holds? rates|keeps? rates|rates? (unchanged|on hold)|leaves? rates)", {},
     "A kamat nem változik: a hatás azon múlik, mit mond a Fed a jövőről."),
    ("inflacio", rf"(hot\w*|higher|hotter|jump\w*|ris(e|es|ing)|surg\w*|accelerat\w*|hits?|tops?).{{0,30}}{_INFL}|{_INFL}.{{0,30}}(hot\w*|jump\w*|ris(e|es|ing)|surg\w*|accelerat\w*|hits?|higher|tops?|beats?)",
     {"nq": -1, "arany": -1, "btc": -1}, "Magasabb infláció: a Fed nehezebben csökkent kamatot, ez lefelé húzza az árakat."),
    ("inflacio", rf"(cool\w*|eas\w*|slow\w*|lower|soft\w*|fall\w*).{{0,30}}{_INFL}|{_INFL}.{{0,30}}(cool\w*|eas\w*|slow\w*|softer|fall\w*|miss\w*)",
     {"nq": 1, "arany": 1, "btc": 1}, "Alacsonyabb infláció: közelebb a kamatcsökkentés, ez felfelé viheti az árakat."),
    ("beke", r"\b(ceasefire|truce|peace (deal|talks|agreement|plan))\b", {"nq": 1, "arany": -1, "btc": 1},
     "Enyhülő feszültség: a kockázatos eszközök emelkedhetnek, az arany mint menedék veszíthet."),
    ("haboru", r"\b(war|missiles?|invasion|invades?|military strikes?|air ?strikes?|attacks?|escalat\w*)\b", {"nq": -1, "arany": 1, "btc": -1},
     "Háborús feszültség: a befektetők a kockázatos eszközökből a biztonságos arany felé menekülnek."),
    ("vam", r"\b(tariffs?|trade war|export (bans?|controls?))\b", {"nq": -1, "arany": 1, "btc": -1},
     "Vámok, kereskedelmi háború: csökkenhet a cégek nyeresége, az arany a menedék."),
    ("szankcio", r"\bsanctions?\b", {"nq": -1, "arany": 1},
     "Szankciók: nő a bizonytalanság, ez az aranynak kedvez."),
    ("valsag", r"\b(recession|shutdown|default|bankrupt\w*|bank (run|failures?|collapse))\b", {"nq": -1, "arany": 1, "btc": -1},
     "Gazdasági válság jele: a részvények eshetnek, az arany a menedék."),
    ("olaj", rf"\boil\b.{{0,30}}{_FEL}", {"nq": -1, "arany": 1},
     "Dráguló olaj: nő az inflációs félelem."),
    ("dollar", rf"\bdollar\b.{{0,30}}({_FEL}|strengthen\w*)", {"arany": -1, "btc": -1},
     "Erősödő dollár: dollárban drágább az arany és a Bitcoin, ez lefelé nyomja őket."),
    ("dollar", rf"\bdollar\b.{{0,30}}({_LE}|weaken\w*)", {"arany": 1, "btc": 1},
     "Gyengülő dollár: ez általában felfelé viszi az aranyat és a Bitcoint."),
    ("munka", r"(jobs?|payrolls?|hiring|employment).{0,30}(beat\w*|strong\w*|surg\w*|jump\w*|blowout)", {"arany": -1, "btc": -1},
     "Erős munkaerőpiac: kisebb az esélye a kamatcsökkentésnek."),
    ("munka", r"(jobs?|payrolls?|hiring|employment).{0,30}(miss\w*|weak\w*|slow\w*|fall\w*|drop\w*)|unemployment.{0,20}ris\w*", {"arany": 1, "btc": 1},
     "Gyengülő munkaerőpiac: nő a kamatcsökkentés esélye."),
    ("jelentes", r"(miss\w*|fall\w* short|below|disappoint\w*).{0,30}(estimates|expectations|forecasts?)"
                 r"|(cuts?|lowers?|weak\w*|disappoint\w*|soft\w*|tepid) (guidance|outlook|forecast)", {"nq": -1},
     "Gyengébb gyorsjelentés vagy kilátás a vártnál: a nagy techcégek lefelé húzhatják a USA Tech 100-at."),
    ("jelentes", r"(beat\w*|top\w*|exceed\w*|surpass\w*|crush\w*).{0,30}(estimates|expectations|forecasts?)"
                 r"|(raises?|boosts?|lifts?|strong\w*|upbeat|rosy) (guidance|outlook|forecast)", {"nq": 1},
     "Jobb gyorsjelentés vagy kilátás a vártnál: a nagy techcégek felfelé húzhatják a USA Tech 100-at."),
    ("reszveny", rf"(stocks?|nasdaq|wall street|equities|tech shares|s&p).{{0,40}}{_LE}|sell-?off.{{0,30}}(stocks?|tech|nasdaq)", {"nq": -1},
     "A részvénypiac már esik."),
    ("reszveny", rf"(stocks?|nasdaq|wall street|equities|tech shares|s&p).{{0,40}}{_FEL}", {"nq": 1},
     "A részvénypiac már emelkedik."),
    ("arany", rf"\bgold\b.{{0,30}}{_LE}", {"arany": -1}, ""),
    ("arany", rf"\bgold\b.{{0,30}}{_FEL}", {"arany": 1}, ""),
    ("kripto", rf"(bitcoin|crypto|btc).{{0,40}}({_LE}|outflows?|hack\w*|bans?|sues?|lawsuit)|\bsec\b.{{0,40}}(sues?|charges?|lawsuit)", {"btc": -1},
     "Negatív kriptohír."),
    ("kripto", rf"(bitcoin|crypto|btc).{{0,40}}({_FEL}|inflows?|approv\w*)", {"btc": 1},
     "Pozitív kriptohír."),
]
IRANY_SZABALYOK = [(cs, re.compile(m, re.IGNORECASE), h, mz) for cs, m, h, mz in IRANY_SZABALYOK]

# Magyar nevek a gyakori naptári eseményekhez (részszöveg -> magyar név)
ESEMENY_NEVEK = [
    ("Core PCE", "PCE maginfláció (a Fed kedvenc inflációs mutatója)"),
    ("Core CPI", "Maginfláció (CPI, élelmiszer és energia nélkül)"),
    ("CPI", "Infláció (fogyasztói árak, CPI)"),
    ("Core PPI", "Termelői maginfláció (PPI)"),
    ("PPI", "Termelői árak (PPI)"),
    ("Non-Farm", "Munkaerőpiaci jelentés (NFP, új munkahelyek)"),
    ("Unemployment Rate", "Munkanélküliségi ráta"),
    ("Unemployment Claims", "Heti munkanélküli-segélykérelmek"),
    ("Average Hourly Earnings", "Órabérek változása"),
    ("Federal Funds Rate", "Fed-kamatdöntés"),
    ("FOMC Statement", "Fed-kamatdöntés közleménye"),
    ("FOMC Press Conference", "Fed sajtótájékoztató"),
    ("FOMC Meeting Minutes", "Fed-ülés jegyzőkönyve"),
    ("Fed Chair", "A Fed elnökének beszéde"),
    ("FOMC Member", "Fed-tag beszéde"),
    ("Fed Vice Chair", "A Fed alelnökének beszéde"),
    ("Core Retail Sales", "Kiskereskedelmi forgalom (autók nélkül)"),
    ("Retail Sales", "Kiskereskedelmi forgalom"),
    ("GDP", "GDP (gazdasági növekedés)"),
    ("ISM Manufacturing", "ISM feldolgozóipari index"),
    ("ISM Services", "ISM szolgáltatóipari index"),
    ("JOLTS", "Betöltetlen állások (JOLTS)"),
    ("Consumer Confidence", "Fogyasztói bizalom"),
    ("Consumer Sentiment", "Fogyasztói hangulat (Michigan)"),
    ("Durable Goods", "Tartós cikkek rendelései"),
    ("ADP", "ADP magánszektori foglalkoztatás"),
]

# --- Segédfüggvények ----------------------------------------------------------


def most():
    return datetime.now(HELYI_IDO)


def naplo(szoveg):
    sor = f"{most():%Y-%m-%d %H:%M:%S}  {szoveg}\n"
    try:
        if NAPLO.exists() and NAPLO.stat().st_size > 500_000:
            NAPLO.write_text(NAPLO.read_text(encoding="utf-8")[-200_000:], encoding="utf-8")
        with NAPLO.open("a", encoding="utf-8") as f:
            f.write(sor)
    except OSError:
        pass
    if sys.stdout.isatty() or os.environ.get("GITHUB_ACTIONS"):
        print(sor, end="", flush=True)


def letolt(url, ido=20):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=ido) as r:
        return r.read()


def beallitasok_olvas():
    ertekek = {}
    if BEALLITASOK.exists():
        for sor in BEALLITASOK.read_text(encoding="utf-8").splitlines():
            sor = sor.strip()
            if sor and not sor.startswith("#") and "=" in sor:
                kulcs, ertek = sor.split("=", 1)
                ertekek[kulcs.strip()] = ertek.strip().strip('"')
    for kulcs in ("TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID"):  # a felhőben (GitHub Secrets) innen jön
        if os.environ.get(kulcs):
            ertekek[kulcs] = os.environ[kulcs]
    return ertekek


def beallitas_ment(kulcs, ertek):
    sorok = BEALLITASOK.read_text(encoding="utf-8").splitlines() if BEALLITASOK.exists() else []
    sorok = [s for s in sorok if not s.strip().startswith(kulcs + "=")]
    sorok.append(f"{kulcs}={ertek}")
    BEALLITASOK.write_text("\n".join(sorok) + "\n", encoding="utf-8")


def allapot_olvas():
    try:
        return json.loads(ALLAPOT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def allapot_ment(allapot):
    ALLAPOT.write_text(json.dumps(allapot, ensure_ascii=False, indent=1), encoding="utf-8")


def ora_perc(szoveg):
    o, p = szoveg.split(":")
    return int(o) * 60 + int(p)


def csendes_e(t):
    perc = t.hour * 60 + t.minute
    kezd, vege = ora_perc(CSENDES_IDO[0]), ora_perc(CSENDES_IDO[1])
    return perc >= kezd or perc < vege if kezd > vege else kezd <= perc < vege


def us_idopont(t, ny_ido):
    """Egy New York-i időpont (pl. a nyitás) a `t` napján, magyar időben."""
    o, p = (int(x) for x in ny_ido.split(":"))
    return datetime(t.year, t.month, t.day, o, p, tzinfo=US_IDO).astimezone(HELYI_IDO)


def html(szoveg):
    return szoveg.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# --- Telegram -----------------------------------------------------------------


def cimzettek_olvas(tema="hirek"):
    """A TELEGRAM_CHAT_ID-ból a téma címzettjei. Formátum: "<hírek>|<árak>", mindkét fele vesszővel
    elválasztott lista; egy elem "csevegés" vagy "csevegés/téma" (csoporton belüli téma azonosítója).
    Ha nincs benne "|", minden üzenet ugyanoda megy."""
    felek = beallitasok_olvas().get("TELEGRAM_CHAT_ID", "").split("|")
    resz = felek[1] if tema == "arak" and len(felek) > 1 else felek[0]
    return [c.strip() for c in resz.split(",") if c.strip()]


def telegram_kuld(szoveg, csak=None, tema="hirek"):
    """Elküldi az üzenetet a téma címzettjeinek ("hirek": hírek és naptár, "arak": árugrás,
    helyzetkép, javaslat, kiértékelés), vagy csak a `csak` listában szereplőknek.
    Igaz, ha legalább egy címzetthez megérkezett."""
    b = beallitasok_olvas()
    token = b.get("TELEGRAM_TOKEN")
    cimzettek = csak or cimzettek_olvas(tema)
    if not token or not cimzettek:
        naplo("NINCS BEÁLLÍTVA a Telegram, az üzenet nem ment el: " + szoveg[:80])
        return False
    sikeres = False
    for cimzett in cimzettek:
        chat, _, szal = cimzett.partition("/")
        mezok = {"chat_id": chat, "text": szoveg, "parse_mode": "HTML", "disable_web_page_preview": "true"}
        if szal:
            mezok["message_thread_id"] = szal
        adat = urllib.parse.urlencode(mezok).encode()
        try:
            req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=adat)
            with urllib.request.urlopen(req, timeout=20) as r:
                sikeres = json.load(r).get("ok", False) or sikeres
        except Exception as e:
            naplo(f"Telegram-hiba ({cimzett}): {e}")
    return sikeres


def telegram_temak():
    """Kiírja a csoportokat és a témákat, ahová az elmúlt 24 órában írtak (a bot adminként benne van)."""
    token = beallitasok_olvas().get("TELEGRAM_TOKEN")
    try:
        valasz = json.loads(letolt(f"https://api.telegram.org/bot{token}/getUpdates"))
    except Exception as e:
        print(f"Nem sikerült elérni a Telegramot: {e}")
        return
    latott = {}
    for u in valasz.get("result", []):
        m = u.get("message") or u.get("channel_post") or {}
        chat = m.get("chat")
        if not chat:
            continue
        tema = (m.get("forum_topic_created") or (m.get("reply_to_message") or {}).get("forum_topic_created") or {}).get("name", "")
        kulcs = f"{chat['id']}/{m['message_thread_id']}" if m.get("is_topic_message") else str(chat["id"])
        latott[kulcs] = f"{chat.get('title') or chat.get('first_name', '')} {('– ' + tema) if tema else ''}".strip()
    for kulcs, nev in latott.items():
        print(f"{kulcs:30} {nev}")
    if not latott:
        print("Nincs friss üzenet. Írj egyet-egyet mindkét témába, aztán futtasd újra.")


def telegram_beallitas():
    """Hozzáadja a címzettekhez mindenkit, aki az elmúlt 24 órában írt a botnak."""
    b = beallitasok_olvas()
    token = b.get("TELEGRAM_TOKEN")
    if not token or token.startswith("IDE_"):
        print("Előbb írd be a bot tokenjét a beallitasok.env fájlba (TELEGRAM_TOKEN=...).")
        return
    try:
        valasz = json.loads(letolt(f"https://api.telegram.org/bot{token}/getUpdates"))
    except Exception as e:
        print(f"Nem sikerült elérni a Telegramot (rossz a token?): {e}")
        return
    if "|" in b.get("TELEGRAM_CHAT_ID", ""):
        print("A címzettek témákra vannak bontva (hírek|árak); ezt kézzel kell szerkeszteni, lásd --temak.")
        return
    meglevok = [c.strip() for c in b.get("TELEGRAM_CHAT_ID", "").split(",") if c.strip()]
    ujak = {}
    for u in valasz.get("result", []):
        chat = u.get("message", {}).get("chat")
        if chat and str(chat["id"]) not in meglevok:
            ujak[str(chat["id"])] = chat.get("first_name") or chat.get("title") or ""
    if not ujak:
        print(f"Nincs új címzett (jelenleg {len(meglevok)} van). Az új ember írjon a botnak, aztán futtasd újra.")
        return
    beallitas_ment("TELEGRAM_CHAT_ID", ",".join(meglevok + list(ujak)))
    for chat, nev in ujak.items():
        print(f"Új címzett hozzáadva: {nev} ({chat})")
    telegram_kuld("✅ Feliratkoztál a hírfigyelőre, mostantól ide jönnek az értesítések.", csak=list(ujak))


# --- 1. Gazdasági naptár ------------------------------------------------------


def naptar_esemenyek():
    """A heti naptár (óránként legfeljebb egyszer töltjük le, a forrás kéri)."""
    try:
        cache = json.loads(NAPTAR_CACHE.read_text(encoding="utf-8"))
        if time.time() - cache["ido"] < 3600:
            return cache["adat"]
    except (OSError, ValueError, KeyError):
        cache = None
    try:
        adat = json.loads(letolt("https://nfs.faireconomy.media/ff_calendar_thisweek.json"))
        NAPTAR_CACHE.write_text(json.dumps({"ido": time.time(), "adat": adat}), encoding="utf-8")
        return adat
    except Exception as e:
        naplo(f"Naptár-letöltési hiba: {e}")
        return cache["adat"] if cache else []


def magyar_nev(cim):
    for minta, nev in ESEMENY_NEVEK:
        if minta.lower() in cim.lower():
            return nev
    return cim


def fontos_esemenyek():
    esemenyek = []
    for e in naptar_esemenyek():
        if e.get("country") not in NAPTAR_ORSZAGOK or e.get("impact") not in NAPTAR_FONTOSSAG:
            continue
        try:
            ido = datetime.fromisoformat(e["date"]).astimezone(HELYI_IDO)
        except (KeyError, ValueError):
            continue
        esemenyek.append({**e, "ido": ido})
    return sorted(esemenyek, key=lambda e: e["ido"])


def csoportosit(esemenyek):
    """Az egy időpontban érkező adatokat (pl. CPI havi és éves) egy sorba vonja."""
    csoportok = {}
    for e in esemenyek:
        csoportok.setdefault(e["ido"], []).append(e)
    return sorted(csoportok.items())


def esemeny_sor(e):
    sor = f"• {html(magyar_nev(e['title']))} <i>({html(e['title'])})</i>"
    if e.get("forecast") or e.get("previous"):
        sor += f"\n   várt: {html(e.get('forecast') or '–')}, előző: {html(e.get('previous') or '–')}"
    return sor


def fed_beszedek(t):
    """A mai Fed-beszédek, a kis fontosságúak is (a nagyokat a fontos események már tartalmazzák)."""
    beszedek = []
    for e in naptar_esemenyek():
        cim = e.get("title", "")
        if (e.get("country") != "USD" or e.get("impact") in NAPTAR_FONTOSSAG
                or not re.search(r"\b(FOMC Member|Fed Vice Chair|Fed Chair\w*)\b.*Speaks", cim)):
            continue
        try:
            ido = datetime.fromisoformat(e["date"]).astimezone(HELYI_IDO)
        except (KeyError, ValueError):
            continue
        if ido.date() == t.date():
            beszedek.append((ido, re.sub(r"^(FOMC Member|Fed Vice Chair\w*|Fed Chair\w*) | Speaks$", "", cim)))
    return sorted(beszedek)


def gyorsjelentesek(allapot, t):
    """A nagy techcégek mai gyorsjelentései a Nasdaq naptárából: [(időszak, név, várt EPS)].
    Naponta legfeljebb 6 óránként töltjük le, az állapotban tároljuk."""
    ma = t.date().isoformat()
    cache = allapot.setdefault("jelentes_cache", {})
    tarolt = cache.get(ma)
    if not tarolt or t.timestamp() - tarolt["ido"] > 6 * 3600:
        try:
            adat = json.loads(letolt(f"https://api.nasdaq.com/api/calendar/earnings?date={ma}"))
            sorok = (adat.get("data") or {}).get("rows") or []
            lista = [[{"time-pre-market": "elotte", "time-after-hours": "utana"}.get(r.get("time"), "?"),
                      NAGY_TECH[r["symbol"]], r.get("epsForecast") or ""]
                     for r in sorok if r.get("symbol") in NAGY_TECH]
            tarolt = cache[ma] = {"ido": t.timestamp(), "lista": lista}
            for regi in sorted(cache)[:-3]:
                del cache[regi]
        except Exception as e:
            naplo(f"Gyorsjelentés-naptár hiba: {e}")
            return tarolt["lista"] if tarolt else []
    return tarolt["lista"]


def jelentes_sorok(jelentesek, t):
    zaras = us_idopont(t, US_ZARAS)
    nevek = {"elotte": f"nyitás előtt (még {us_idopont(t, US_NYITAS):%H:%M} előtt)",
             "utana": f"zárás után (~{zaras:%H:%M}–{zaras + timedelta(minutes=10):%H:%M})",
             "?": "időpont nem ismert"}
    sorok = []
    for idoszak in ("elotte", "utana", "?"):
        cegek = [f"{html(n.rstrip('*'))}{' ⭐' if n.endswith('*') else ''}" for i, n, _ in jelentesek if i == idoszak]
        if cegek:
            sorok.append(f"• {nevek[idoszak]}: {', '.join(cegek)}")
    return sorok


def naptar_ellenorzes(allapot, t):
    esemenyek = fontos_esemenyek()
    kuldott = allapot.setdefault("naptar_kuldott", [])
    jelentesek = gyorsjelentesek(allapot, t) if t.weekday() < 5 else []

    # Reggeli összefoglaló
    ma = t.date().isoformat()
    if allapot.get("reggeli_datum") != ma and t.hour * 60 + t.minute >= ora_perc(REGGELI_OSSZEFOGLALO):
        maiak = [e for e in esemenyek if e["ido"].date() == t.date()]
        if maiak:
            szoveg = f"📅 <b>Mai nagy amerikai adatok ({t:%m.%d.})</b>\n"
            for ido, csoport in csoportosit(maiak):
                szoveg += f"\n<b>{ido:%H:%M}</b>\n" + "\n".join(esemeny_sor(e) for e in csoport) + "\n"
            szoveg += "\nEkkor nagy ugrás jöhet. Nyitott pozíciónál figyelj a stop-lossra, előtte pár perccel ne nyiss újat."
        else:
            szoveg = f"📅 Ma ({t:%m.%d.}) nincs nagy hatású amerikai adat a naptárban."
        beszedek = fed_beszedek(t)
        if beszedek:
            szoveg += ("\n\n🎙 <b>Fed-tagok beszédei:</b> " + ", ".join(f"{i:%H:%M} {html(n)}" for i, n in beszedek)
                       + "\n<i>Egyenként kisebb hatásúak, de egy kamatra utaló mondat megmozgathatja az árakat.</i>")
        if jelentesek:
            szoveg += ("\n\n💼 <b>Gyorsjelentések (USA Tech 100 nagy cégei):</b>\n" + "\n".join(jelentes_sorok(jelentesek, t))
                       + "\n<i>⭐ = nagy súly az indexben; a jelentés után az index percek alatt nagyot ugorhat.</i>")
        if telegram_kuld(szoveg):
            allapot["reggeli_datum"] = ma
            naplo("Reggeli összefoglaló elküldve.")

    # Zárás utáni gyorsjelentések előtt (az esti kereskedési sávba esik)
    utana = [n.rstrip("*") for i, n, _ in jelentesek if i == "utana"]
    zaras = us_idopont(t, US_ZARAS)
    hatra = (zaras - t).total_seconds() / 60
    kulcs = "jelentes-" + zaras.isoformat()
    if JELENTES_FIGYELMEZTETES and utana and 0 < hatra <= FIGYELMEZTETES_PERC + 10 and kulcs not in kuldott and not csendes_e(t):
        szoveg = (f"💼 <b>{round(hatra)} perc múlva ({zaras:%H:%M}) zár az amerikai tőzsde, utána gyorsjelentés jön:</b>\n"
                  f"{html(', '.join(utana))}\n\nA USA Tech 100 {zaras:%H:%M} után pár perc alatt nagyot ugorhat, "
                  "mindkét irányba. Nyitott pozíciónál figyelj a stop-lossra; a stop ilyenkor rosszabb áron is teljesülhet.")
        if telegram_kuld(szoveg):
            kuldott.append(kulcs)
            naplo(f"Gyorsjelentés-figyelmeztetés elküldve: {', '.join(utana)}")

    # Figyelmeztetés közvetlenül előtte (csendes időben nem, a reggeli lista úgyis tartalmazza)
    for ido, csoport in csoportosit(esemenyek):
        hatra = (ido - t).total_seconds() / 60
        kulcs = ido.isoformat()
        if 0 < hatra <= FIGYELMEZTETES_PERC + 10 and kulcs not in kuldott and not csendes_e(t):
            szoveg = (f"⚠️ <b>{round(hatra)} perc múlva ({ido:%H:%M}) nagy hatású adat jön:</b>\n"
                      + "\n".join(esemeny_sor(e) for e in csoport)
                      + "\n\nA USA Tech 100, az arany és a Bitcoin is nagyot mozdulhat.")
            if telegram_kuld(szoveg):
                kuldott.append(kulcs)
                naplo(f"Figyelmeztetés elküldve: {ido:%H:%M} {csoport[0]['title']}")
    allapot["naptar_kuldott"] = kuldott[-100:]


# --- 2. Friss hírek -----------------------------------------------------------

# Hírcsatornák, amelyek cikkei a program számára is olvashatók (a Reuters, a Bloomberg,
# a WSJ és a MarketWatch kitiltja a programokat, azoknál csak a cím alapján becsül).
OLVASHATO_CSATORNAK = [
    ("CNBC", "https://www.cnbc.com/id/100003114/device/rss/rss.html"),   # vezető hírek
    ("CNBC", "https://www.cnbc.com/id/20910258/device/rss/rss.html"),    # gazdaság
    ("CNBC", "https://www.cnbc.com/id/10000664/device/rss/rss.html"),    # piacok
    ("CNBC", "https://www.cnbc.com/id/100727362/device/rss/rss.html"),   # világ
    ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss"),
]
OLVASHATO_FORRASOK = ("CNBC", "CoinDesk")
CIKK_MAX_MONDAT = 40
# Ha ezek egyike a találat közelében áll, a hatás megfordul ("rate cuts less likely").
TAGADAS = re.compile(r"\b(less likely|unlikely|fewer|no|not|doubts?|dim\w*|delay\w*|push\w* back|rul\w* out"
                     r"|scal\w* back|pare\w*|trim\w*|temper\w*|fad\w*|reduc\w*)\b", re.IGNORECASE)


def rss_elemek(url, forras=None):
    gyoker = ET.fromstring(letolt(url))
    for item in gyoker.iter("item"):
        cim = htmlmod.unescape(item.findtext("title", "")).strip()
        src = forras or item.findtext("source", "")
        if src and cim.endswith(" - " + src):
            cim = cim[: -len(src) - 3]
        datum = item.findtext("pubDate")
        if not cim or not datum:
            continue
        yield {
            "cim": cim,
            "forras": src,
            "link": item.findtext("link", ""),
            "leiras": htmlmod.unescape(re.sub(r"<[^>]+>", "", item.findtext("description", "") or "")),
            "ido": parsedate_to_datetime(datum),
            "olvashato": forras in OLVASHATO_FORRASOK,
        }


def google_hirek(kereses):
    url = ("https://news.google.com/rss/search?q="
           + urllib.parse.quote(f"({kereses}) when:1h") + "&hl=en-US&gl=US&ceid=US:en")
    for h in rss_elemek(url):
        h["leiras"] = ""  # a Google-nál ez csak egy link
        yield h


def hir_kulcs(cim):
    szavak = re.findall(r"[a-z0-9]+", cim.lower())
    return " ".join(szavak[:8])


def cikk_mondatai(link):
    """A cikk szövege mondatokra bontva (menük, stíluskód, rövid sorok nélkül)."""
    oldal = letolt(link).decode("utf-8", "ignore")
    oldal = re.sub(r"<(script|style|noscript)[^>]*>.*?</\1>", "", oldal, flags=re.S | re.I)
    mondatok = []
    for p in re.findall(r"<p[^>]*>(.*?)</p>", oldal, re.S):
        p = re.sub(r"\s+", " ", htmlmod.unescape(re.sub(r"<[^>]+>", "", p))).strip()
        if len(p) < 60 or "{" in p or p.count(" ") < 8 or not re.search(r"[.!?\"”’)]$", p):
            continue
        mondatok += [m for m in re.split(r"(?<=[.!?])\s+(?=[A-Z\"“])", p) if len(m) > 25]
    return mondatok[:CIKK_MAX_MONDAT]


def forditas(szoveg):
    """Angol szöveg magyarra: kulcs nélküli Google-fordító, ha az nem megy, MyMemory;
    ha egyik sem, None."""
    q = urllib.parse.quote(szoveg[:480])
    try:
        url = f"https://translate.googleapis.com/translate_a/single?client=gtx&sl=en&tl=hu&dt=t&q={q}"
        return "".join(resz[0] for resz in json.loads(letolt(url, 10))[0] if resz[0])
    except Exception as e:
        naplo(f"Google-fordítási hiba, jön a tartalék: {e}")
    try:
        valasz = json.loads(letolt(f"https://api.mymemory.translated.net/get?langpair=en%7Chu&q={q}", 10))
        if valasz.get("responseStatus") == 200:
            return valasz["responseData"]["translatedText"]
    except Exception as e:
        naplo(f"Tartalék fordítási hiba: {e}")
    return None


def irany_becsles(szovegek):
    """(szöveg, súly) párokból eszközönkénti pontszám és a leggyakoribb okok.
    Mondatonként egy csoportból csak az első illeszkedő szabály számít; ha a találat
    közelében tagadás áll, a hatás megfordul."""
    pont = {"nq": 0, "arany": 0, "btc": 0}
    abszolut = {"nq": 0, "arany": 0, "btc": 0}
    okok = {}
    for szoveg, suly in szovegek:
        csoportok = set()
        for csoport, minta, hatas, magyarazat in IRANY_SZABALYOK:
            if csoport in csoportok:
                continue
            talalat = minta.search(szoveg)
            if not talalat:
                continue
            csoportok.add(csoport)
            kornyezet = szoveg[max(0, talalat.start() - 15): talalat.end() + 12]
            # ha a tagadó szó maga a találat része (pl. "temper rate-hike bets"), a szabály már számolt vele
            tagadott = TAGADAS.search(kornyezet) and not TAGADAS.search(talalat.group(0))
            elojel = -1 if tagadott and csoport in ("kamat", "inflacio", "beke") else 1
            for eszkoz, ertek in hatas.items():
                pont[eszkoz] += elojel * ertek * suly
                abszolut[eszkoz] += abs(ertek) * suly
            if magyarazat and elojel == 1:
                okok[magyarazat] = okok.get(magyarazat, 0) + suly
    irany = {}
    for e in pont:
        if abszolut[e] == 0:
            irany[e] = None
        elif abs(pont[e]) < 0.25 * abszolut[e]:
            irany[e] = 0  # ellentétes jelek, nincs egyértelmű irány
        else:
            irany[e] = pont[e]
    return irany, [ok for ok, _ in sorted(okok.items(), key=lambda x: -x[1])]


def irany_sor(irany):
    def jel(p):
        if p is None:
            return "–"
        if p == 0:
            return "↕"
        return ("↑↑" if p >= 6 else "↑") if p > 0 else ("↓↓" if p <= -6 else "↓")
    return f"USA Tech 100 {jel(irany['nq'])} · Arany {jel(irany['arany'])} · Bitcoin {jel(irany['btc'])}"


def hir_uzenet(h):
    mondatok = []
    if h["olvashato"]:
        try:
            mondatok = cikk_mondatai(h["link"])
        except Exception as e:
            naplo(f"Cikk-letöltési hiba ({h['link'][:60]}): {e}")
    if not mondatok and h.get("leiras"):
        mondatok = [h["leiras"]]
    szovegek = [(h["cim"], 3)] + [(m, 2 if i < 3 else 1) for i, m in enumerate(mondatok)]
    irany, okok = irany_becsles(szovegek)

    szoveg = (f"📰 <b>{html(h['cim'])}</b>\n"
              f"{html(h['forras'])}, {h['ido'].astimezone(HELYI_IDO):%H:%M}\n")
    if mondatok:
        roviden = forditas(" ".join(mondatok[:2]))
        if roviden:
            szoveg += f"\n<b>Röviden:</b> {html(roviden)}\n"
    if any(p is not None for p in irany.values()):
        szoveg += f"\n<b>Lehetséges hatás:</b> {irany_sor(irany)}\n"
        if okok:
            szoveg += "<b>Miért:</b> " + " ".join(html(o) for o in okok[:2]) + "\n"
    else:
        szoveg += "\n<b>Lehetséges hatás:</b> nem egyértelmű, nézz rá az árakra.\n"
    alap = "a teljes cikk alapján" if len(mondatok) > 1 else "csak a cím alapján (a cikk nem olvasható)"
    szoveg += (f"<a href=\"{html(h['link'])}\">Megnyitás</a>\n"
               f"<i>Szabályalapú becslés {alap}, nem kereskedési jelzés. ↕ = ellentétes jelek, ↑↑/↓↓ = erős.</i>")
    return szoveg, irany


_OSSZEG = r"\$\s?([\d.,]+)\s*(million|billion|bn|m|b)\b"
ETF_MINTAK = [  # (minta, az összeg csoportja, irány: None = a szóból)
    (re.compile(_OSSZEG + r"[^$]{0,25}?\b(inflows?|outflows?)\b", re.I), 1, None),
    (re.compile(r"\b(inflows?|outflows?)\b[^$]{0,15}" + _OSSZEG, re.I), 2, None),
    (re.compile(r"\b(shed|lost|lose|bleed|pull|withdr[ae]w)\w*\s+(about\s+)?" + _OSSZEG, re.I), 3, -1),
    (re.compile(r"\b(draw|attract|took in|add|pull in|gain)\w*\s+(about\s+)?" + _OSSZEG, re.I), 3, 1),
]


def etf_aramlas(cim):
    """Egy Bitcoin-ETF-es címből a napi pénzáramlás millió dollárban (kiáramlás negatív), vagy None."""
    if not (re.search(r"\b(bitcoin|btc)\b", cim, re.I) and re.search(r"\bETFs?\b", cim)):
        return None
    if re.search(r"\b(ether\w*|eth|xrp|solana|sol|altcoins?)\b", cim, re.I):
        return None  # vegyes (több kriptós) adat, nem tiszta Bitcoin-szám
    for minta, csop, elojel in ETF_MINTAK:
        m = minta.search(cim)
        if not m:
            continue
        g = m.groups()
        ertek = float(g[csop - 1].replace(",", ""))
        if g[csop].lower() in ("billion", "bn", "b"):
            ertek *= 1000
        if elojel is None:
            szo = g[0] if csop == 2 else g[2]
            elojel = -1 if szo.lower().startswith("out") else 1
        return elojel * ertek
    return None


def hirek_ellenorzes(allapot, t):
    latott = set(allapot.get("hirek_latott", []))
    elso_futas = not latott
    kuldesek = [x for x in allapot.get("hir_kuldesek", []) if t.timestamp() - x < 3600]
    jeloltek = []
    etf = [x for x in allapot.get("etf_hirek", []) if t.timestamp() - x[0] < 48 * 3600]

    forrasok = [(f"{nev} csatorna", lambda u=url, n=nev: rss_elemek(u, n)) for nev, url in OLVASHATO_CSATORNAK]
    forrasok += [(k[:30], lambda k=k: google_hirek(k)) for k in HIR_KERESESEK]
    for nev, lekeres in forrasok:
        try:
            for h in lekeres():
                kulcs = hir_kulcs(h["cim"])
                if kulcs in latott:
                    continue
                latott.add(kulcs)
                aramlas = etf_aramlas(h["cim"])
                if aramlas is not None:  # bármelyik forrásból, a helyzetképhez
                    etf.append([h["ido"].timestamp(), aramlas, h["forras"], h["cim"]])
                kor = (t - h["ido"]).total_seconds() / 60
                if elso_futas or kor > HIR_MAX_KOR_PERC:
                    continue
                if not any(f.lower() in h["forras"].lower() for f in MEGBIZHATO_FORRASOK):
                    continue
                if not (EROS_MINTA.search(h["cim"] + " " + h["leiras"]) or JELENTES_MINTA.search(h["cim"])):
                    continue
                if any(f.lower() in h["forras"].lower() for f in OLVASHATO_FORRASOK):
                    h["olvashato"] = h["olvashato"] or not h["link"].startswith("https://news.google.com")
                jeloltek.append(h)
        except Exception as e:
            naplo(f"Hírletöltési hiba ({nev}…): {e}")

    hangulat = [x for x in allapot.get("hir_hangulat", []) if t.timestamp() - x[0] < 48 * 3600]
    for h in sorted(jeloltek, key=lambda h: h["ido"], reverse=True):
        if not csendes_e(t) and len(kuldesek) < HIR_MAX_ORANKENT:
            szoveg, irany = hir_uzenet(h)
            if telegram_kuld(szoveg):
                kuldesek.append(t.timestamp())
                naplo(f"Hír elküldve: {h['cim']}")
        else:
            irany, _ = irany_becsles([(h["cim"], 3)])
            naplo(f"Hír nem ment ki (csendes idő vagy óránkénti korlát): {h['cim']}")
        hangulat.append([h["ido"].timestamp()] + [0 if irany[e] is None else (irany[e] > 0) - (irany[e] < 0)
                                                  for e in ("nq", "arany", "btc")])
    allapot["hir_hangulat"] = hangulat
    allapot["etf_hirek"] = sorted(etf)[-30:]

    allapot["hirek_latott"] = list(latott)[-3000:]
    allapot["hir_kuldesek"] = kuldesek


# --- 3. Nagy árugrás ----------------------------------------------------------


def arak_yahoo(jel):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(jel)}?interval=1m&range=1d"
    eredmeny = json.loads(letolt(url))["chart"]["result"][0]
    zaro = eredmeny["indicators"]["quote"][0]["close"]
    return [(ts, c) for ts, c in zip(eredmeny.get("timestamp", []), zaro) if c is not None]


def arak_binance(jel):
    url = f"https://api.binance.com/api/v3/klines?symbol={jel}&interval=1m&limit=40"
    return [(int(k[0]) // 1000, float(k[4])) for k in json.loads(letolt(url))]


def arugras_ellenorzes(allapot, t):
    utolso = allapot.setdefault("arugras_utolso", {})
    for nev, forras, jel, kuszob in ESZKOZOK:
        try:
            arak = arak_yahoo(jel) if forras == "yahoo" else arak_binance(jel)
        except Exception as e:
            naplo(f"Árletöltési hiba ({nev}): {e}")
            continue
        if len(arak) < 2 or t.timestamp() - arak[-1][0] > 25 * 60:
            continue  # zárva a piac, vagy nincs friss adat (a Yahoo kb. 10 percet késik)
        mostani_ts, mostani = arak[-1]
        korabbiak = [(ts, c) for ts, c in arak if ts <= mostani_ts - ARUGRAS_ABLAK_PERC * 60]
        if not korabbiak:
            continue
        regi = korabbiak[-1][1]
        valtozas = (mostani / regi - 1) * 100
        if abs(valtozas) < kuszob:
            continue
        if t.timestamp() - utolso.get(nev, 0) < ARUGRAS_SZUNET_PERC * 60 or csendes_e(t):
            continue
        jel_kep, irany = ("📈", "emelkedett") if valtozas > 0 else ("📉", "esett")
        regi_sz, mostani_sz = (f"{x:,.2f}".replace(",", " ") for x in (regi, mostani))
        szoveg = (f"⚡ {jel_kep} <b>{nev}: {ARUGRAS_ABLAK_PERC} perc alatt {abs(valtozas):.2f}%-ot {irany}</b>\n"
                  f"{regi_sz} → {mostani_sz}\n\nValószínűleg hír vagy adat van mögötte. Ha van nyitott pozíciód, nézz rá.")
        if telegram_kuld(szoveg, tema="arak"):
            utolso[nev] = t.timestamp()
            naplo(f"Árugrás elküldve: {nev} {valtozas:+.2f}%")


# --- 4. Napi helyzetkép --------------------------------------------------------


def yahoo_gyertyak(jel, intervallum, tav):
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(jel)}"
           f"?interval={intervallum}&range={tav}")
    r = json.loads(letolt(url))["chart"]["result"][0]
    q = r["indicators"]["quote"][0]
    return [(ts, o, h, l, c) for ts, o, h, l, c in zip(r["timestamp"], q["open"], q["high"], q["low"], q["close"])
            if None not in (o, h, l, c)]


def atlag(x):
    return sum(x) / len(x)


def trend_jel(zaro, i):
    """+1 emelkedő trend (ár > 20 napos átlag > 50 napos átlag), -1 csökkenő, 0 oldalazó."""
    a20, a50 = atlag(zaro[i - 19:i + 1]), atlag(zaro[i - 49:i + 1])
    if zaro[i] > a20 > a50:
        return 1
    if zaro[i] < a20 < a50:
        return -1
    return 0


def trend_talalat(zaro):
    """Visszamérés: a trend iránya hány %-ban találta el a következő napot (10 év)."""
    jo = osszes = 0
    for i in range(50, len(zaro) - 1):
        t = trend_jel(zaro, i)
        if t:
            osszes += 1
            jo += (t > 0) == (zaro[i + 1] > zaro[i])
    return jo / osszes if osszes else None


def perc_napbol(ts):
    ido = datetime.fromtimestamp(ts, HELYI_IDO)
    return ido, ido.hour * 60 + ido.minute


def median(x):
    x = sorted(x)
    return x[len(x) // 2] if x else None


def napon_beluli(jel, t):
    """5 perces adatokból: a mai nap szintjei, a 16–18 órás sáv szokásos mozgása és a várható sáv
    21:00-ig (60 nap)."""
    gyertyak = yahoo_gyertyak(jel, "5m", "60d")
    kezd, vege = (ora_perc(x) for x in KERESKEDESI_SAV)
    ny = us_idopont(t, US_NYITAS)
    nyitas = ny.hour * 60 + ny.minute
    ma, felorak, otperc, napok = [], {}, [], {}
    alap_perc, vege_perc = (t.hour * 60 + t.minute) // 5 * 5, ora_perc(VARAKOZAS_VEGE)
    for ts, o, h, l, c in gyertyak:
        ido, perc = perc_napbol(ts)
        if ido.date() == t.date():
            ma.append((perc, h, l, c))
            utolso_ts = ts
        elif ido.weekday() < 5:  # a régebbi napok mozgása a mostani időponttól 21:00-ig
            n = napok.setdefault(ido.date(), {"fel": [], "le": []})
            if perc < alap_perc:
                n["alap"] = c
            elif perc < vege_perc and "alap" in n:
                n["fel"].append(h - n["alap"])
                n["le"].append(n["alap"] - l)
        if kezd <= perc < vege:
            otperc.append(h - l)
            kulcs = (ido.date(), perc // 30)
            fh, fl = felorak.get(kulcs, (h, l))
            felorak[kulcs] = (max(fh, h), min(fl, l))
    kileng = [(max(n["fel"]), max(n["le"])) for n in napok.values() if len(n["fel"]) >= 6]
    sav = None
    if len(kileng) >= 15 and ma:
        mf, ml = median([max(f, 0) for f, _ in kileng]), median([max(x, 0) for _, x in kileng])
        for k in [x / 20 for x in range(10, 81)]:  # a legkisebb szorzó, amellyel a napok 70%-a belefér
            bent = sum(f <= k * mf and x <= k * ml for f, x in kileng) / len(kileng)
            if bent >= VARAKOZAS_SAV_ARANY:
                break
        sav = (ma[-1][3] - k * ml, ma[-1][3] + k * mf, bent, len(kileng))
    nyito = [x for x in ma if nyitas <= x[0] < nyitas + 30]
    ejjel = [x for x in ma if x[0] < nyitas]
    return {
        "ot_perc": median(otperc),
        "fel_ora": median([h - l for h, l in felorak.values()]),
        "nyito": (max(x[1] for x in nyito), min(x[2] for x in nyito)) if len(nyito) >= 4 else None,
        "ejjel": (max(x[1] for x in ejjel), min(x[2] for x in ejjel)) if ejjel else None,
        "ma": (max(x[1] for x in ma), min(x[2] for x in ma)) if ma else None,
        "nyitas": ny,
        "sav": sav,                                  # (alsó, felső, a múltban hány %-ban fért bele, napok)
        "ar": ma[-1][3] if ma else None,             # a legutóbbi 5 perces záróár
        "ar_ts": utolso_ts if ma else None,          # és a gyertya kezdete (a Yahoo ~10 percet késik)
        "reggel": next((x[3] for x in ma if x[0] >= 9 * 60), None),  # 9:00 körüli ár
    }


def szam(x):
    return f"{x:,.0f}".replace(",", " ") if x >= 100 else f"{x:.1f}"


def eszkoz_helyzet(nev, napi_jel, perces_jel, kulcs, hangulat, t, dollar=0, stat=None):
    gyertyak = yahoo_gyertyak(napi_jel, "1d", "10y")
    # a mai, még tartó gyertyát leválasztjuk
    if t.timestamp() - gyertyak[-1][0] < 20 * 3600:
        lezart, most_ar = gyertyak[:-1], gyertyak[-1][4]
    else:
        lezart, most_ar = gyertyak, gyertyak[-1][4]
    zaro = [g[4] for g in lezart]
    tegnap = lezart[-1]
    valodi_kilenges = [max(g[2], e[4]) - min(g[3], e[4]) for e, g in zip(lezart[-16:-1], lezart[-15:])]
    atr = atlag(valodi_kilenges[:-1])
    nagy_nap = valodi_kilenges[-1] > 1.5 * atr
    szokasos = atr * (1.3 if nagy_nap else 1.0)

    trend = trend_jel(zaro, len(zaro) - 1)
    talalat = trend_talalat(zaro)
    oszlop = {"nq": 1, "arany": 2, "btc": 3}[kulcs]
    friss = [x[oszlop] for x in hangulat if t.timestamp() - x[0] < 24 * 3600]
    poz, neg = sum(1 for x in friss if x > 0), sum(1 for x in friss if x < 0)
    hir_jel = (sum(friss) >= 2) - (sum(friss) <= -2)
    trend_szo = {1: "emelkedő", -1: "csökkenő", 0: "oldalazó"}[trend]

    mai = (most_ar / tegnap[4] - 1) * 100
    sorok = [f"<b>{nev}</b>  most {szam(most_ar)} ({mai:+.2f}% a tegnapi zárához)"]
    try:
        nb = napon_beluli(perces_jel, t)
    except Exception as e:
        naplo(f"5 perces adat hiba ({nev}): {e}")
        nb = {}
    szintek = []
    if nb.get("nyito"):
        ny = nb["nyitas"]
        szintek.append(f"nyitó sáv ({ny:%H:%M}–{ny + timedelta(minutes=30):%H:%M}) {szam(nb['nyito'][1])} – {szam(nb['nyito'][0])}")
    szintek.append(f"tegnap csúcs {szam(tegnap[2])}, mélypont {szam(tegnap[3])}, záró {szam(tegnap[4])}")
    if nb.get("ejjel"):
        szintek.append(f"éjszaka/délelőtt {szam(nb['ejjel'][1])} – {szam(nb['ejjel'][0])}")
    sorok.append("<b>Szintek:</b> " + "; ".join(szintek))
    if nb.get("ma"):
        eddig = nb["ma"][0] - nb["ma"][1]
        hatra = szokasos - eddig
        sorok.append(f"<b>Mai mozgás:</b> eddig {szam(eddig)} pont, egy szokásos nap ~{szam(szokasos)}"
                     + (f" → még kb. {szam(hatra)} pont lehet benne" if hatra > 0 else " → a szokásosat már kimozogta, óvatosan a kitörésekkel")
                     + (" (tegnap nagy nap volt)" if nagy_nap else ""))
    if nb.get("ot_perc"):
        sorok.append(f"<b>Scalping:</b> 16–18 óra között egy 5 perces gyertya ~{szam(nb['ot_perc'])}, "
                     f"fél óra ~{szam(nb['fel_ora'])} pont; ennél szorosabb stopot a zaj kiüt.")
    delelott = 0
    if nb.get("reggel") and nb.get("ar"):
        valt = nb["ar"] - nb["reggel"]
        delelott = (valt > 0.1 * atr) - (valt < -0.1 * atr)
    reszek = {"trend": trend, "hir": hir_jel, "delelott": delelott, "dollar": -dollar}
    osszeg = sum(reszek.values())
    irany = {2: "📈 emelkedő", 1: "↗️ enyhén emelkedő", 0: "➡️ semleges", -1: "↘️ enyhén csökkenő",
             -2: "📉 csökkenő"}[max(-2, min(2, osszeg))]
    nyil = {1: "↑", 0: "→", -1: "↓"}
    varakozas = None
    if nb.get("sav"):
        also, felso, bent, napok = nb["sav"]
        varakozas = {"nev": nev, "ar": nb["ar"], "also": also, "felso": felso, "irany": (osszeg > 0) - (osszeg < 0),
                     "reszek": reszek, "ts": nb["ar_ts"] + 300}
        sorok.append(f"<b>{VARAKOZAS_VEGE}-ig várható sáv:</b> {szam(also)} – {szam(felso)} "
                     f"(az elmúlt {napok} napon {bent:.0%}-ban maradt benne)")
    jo, db = (stat or {}).get(kulcs, [0, 0, 0, 0])[:2]
    meres = f"a bot saját mérése eddig {jo}/{db} ({jo / db:.0%})" if db else "a bot most kezdi mérni a találatot"
    sorok.append(f"<b>Irány {VARAKOZAS_VEGE}-ig:</b> {irany} (napi trend {nyil[trend]}, hírek {poz}+/{neg}− {nyil[hir_jel]}, "
                 f"délelőtt {nyil[delelott]}, dollár {nyil[dollar]}) – {meres}")
    return "\n".join(sorok), talalat, varakozas


def hatter_sor():
    """A dollárindex és a 10 éves hozam változása a tegnapi zárához képest, rövid értelmezéssel."""
    reszek, jelek = [], []
    for nev, jel, egyseg in HATTER_JELEK:
        try:
            gy = yahoo_gyertyak(jel, "1d", "5d")
        except Exception as e:
            naplo(f"Háttéradat hiba ({nev}): {e}")
            continue
        if len(gy) < 2:
            continue
        elozo, most_ = gy[-2][4], gy[-1][4]
        if egyseg == "%":  # hozam: bázispontban (0,01 százalékpont)
            bp = (most_ - elozo) * 100
            reszek.append(f"{nev} {most_:.2f}% ({bp:+.0f} bp)")
            jelek.append((bp >= 3) - (bp <= -3))
        else:
            v = (most_ / elozo - 1) * 100
            reszek.append(f"{nev} {most_:.2f} ({v:+.2f}%)")
            jelek.append((v >= 0.2) - (v <= -0.2))
    if not reszek:
        return None
    osszeg = sum(jelek)
    if osszeg > 0:
        ertelmezes = "erősödő dollár / emelkedő hozam: ez nyomja az aranyat és a USA Tech 100-at (a Bitcoint is)."
    elif osszeg < 0:
        ertelmezes = "gyengülő dollár / csökkenő hozam: ez általában segíti az aranyat és a USA Tech 100-at."
    else:
        ertelmezes = "nincs nagy elmozdulás, ma nem ez mozgatja a piacot."
    return f"🌐 <b>Háttér:</b> {' · '.join(reszek)}\n→ {ertelmezes}"


def etf_sor(allapot, t):
    """A legfrissebb Bitcoin-ETF pénzáramlás a hírekből (az elmúlt 36 órából)."""
    friss = [x for x in allapot.get("etf_hirek", []) if t.timestamp() - x[0] < 36 * 3600]
    if not friss:
        return None
    ido, ertek, forras, _ = max(friss)
    osszeg = f"{abs(ertek) / 1000:.2f} milliárd $" if abs(ertek) >= 1000 else f"{abs(ertek):.0f} millió $"
    szo, hatas = (("beáramlás", "a vásárlás felfelé támogatja") if ertek > 0
                  else ("kiáramlás", "az eladás lefelé nyomja"))
    return (f"<b>ETF-ek:</b> nettó {szo} {osszeg} ({html(forras)}, {datetime.fromtimestamp(ido, HELYI_IDO):%m.%d. %H:%M}) "
            f"– {hatas} a Bitcoint. <i>Hírcímből, az előző kereskedési napra.</i>")


def dollar_delelott(t):
    """A dollárindex iránya 9:00 óta: +1 erősödik, -1 gyengül, 0 alig mozdult (±0,1%)."""
    try:
        gy = [g for g in yahoo_gyertyak("DX-Y.NYB", "5m", "1d") if perc_napbol(g[0])[1] >= 9 * 60]
    except Exception as e:
        naplo(f"Dollárindex 5 perces hiba: {e}")
        return 0
    if len(gy) < 2:
        return 0
    v = (gy[-1][4] / gy[0][1] - 1) * 100
    return (v >= 0.1) - (v <= -0.1)


def helyzetkep_szoveg(allapot, t, varakozasok=None):
    hangulat = allapot.get("hir_hangulat", [])
    reszek, talalatok = [], []
    dollar = dollar_delelott(t)
    for nev, napi_jel, perces_jel, kulcs in HELYZETKEP_ESZKOZOK:
        try:
            szoveg, talalat, varakozas = eszkoz_helyzet(nev, napi_jel, perces_jel, kulcs, hangulat, t,
                                                        dollar, allapot.get("varakozas_stat"))
            if varakozas and varakozasok is not None:
                varakozasok[kulcs] = varakozas
            if kulcs == "btc" and etf_sor(allapot, t):
                szoveg += "\n" + etf_sor(allapot, t)
            reszek.append(szoveg)
            if talalat:
                talalatok.append(f"{nev} {talalat:.0%}")
        except Exception as e:
            naplo(f"Helyzetkép hiba ({nev}): {e}")
    if not reszek:
        return None
    maiak = [e for e in fontos_esemenyek() if e["ido"].date() == t.date()]
    esemeny = ("Mai nagy adatok: " + ", ".join(f"{ido:%H:%M} {magyar_nev(cs[0]['title'])}" for ido, cs in csoportosit(maiak)) + "."
               if maiak else "Ma nincs nagy amerikai adat a naptárban.")
    beszedek = [(i, n) for i, n in fed_beszedek(t) if i >= t]
    if beszedek:
        esemeny += " Fed-beszédek még ma: " + ", ".join(f"{i:%H:%M} {n}" for i, n in beszedek) + "."
    hatter = hatter_sor()
    jelentesek = gyorsjelentesek(allapot, t)
    jelentes = ("\n\n💼 <b>Mai gyorsjelentések:</b>\n" + "\n".join(jelentes_sorok(jelentesek, t))) if jelentesek else ""
    return (f"🧭 <b>Napi helyzetkép ({t:%m.%d. %H:%M})</b>\n\n" + "\n\n".join(reszek)
            + (f"\n\n{hatter}" if hatter else "")
            + f"\n\n📅 {html(esemeny)}" + jelentes + "\n\n"
            "<i>Az árak a határidős piacról jönnek (a Bitcoin kivételével); a Trading 212 ára ettől pár tíz ponttal eltérhet, "
            "ezért a távolságokat (pontokat) figyeld, ne a pontos számokat. "
            "A nyitó sáv szélét 16–18 óra között szinte minden nap áttöri az ár, gyakran mindkét irányba (hamis kitörés). "
            f"Az irányt a napi trend 10 év alatt csak így találta el: {', '.join(talalatok)}; a délelőtti mozgás és a dollár "
            f"16–21 óra között 52–54%-ban: ez alig jobb a pénzfeldobásnál. {VARAKOZAS_VEGE} után a bot megírja, bejött-e. "
            "Nem kereskedési jelzés.</i>")


def helyzetkep_ellenorzes(allapot, t):
    ma = t.date().isoformat()
    perc = t.hour * 60 + t.minute
    if (t.weekday() >= 5 or allapot.get("helyzetkep_datum") == ma
            or not ora_perc(HELYZETKEP_IDO) <= perc < ora_perc(HELYZETKEP_VEGE)):
        return
    varakozasok = {}
    szoveg = helyzetkep_szoveg(allapot, t, varakozasok)
    if szoveg and telegram_kuld(szoveg, tema="arak"):
        allapot["helyzetkep_datum"] = ma
        allapot["varakozas"] = {"datum": ma, "eszkozok": varakozasok, "kiertekelve": False}
        naplo("Napi helyzetkép elküldve.")
        if varakozasok and telegram_kuld(javaslat_szoveg(varakozasok, allapot.get("varakozas_stat") or {}, t), tema="arak"):
            naplo("Napi javaslat elküldve.")


def javaslat_szoveg(varakozasok, stat, t):
    """Rövid, külön üzenet: eszközönként irány, beszállás, stop és cél 21:00-ig (a várható sáv széleivel)."""
    sorok = []
    for nev, _, _, kulcs in HELYZETKEP_ESZKOZOK:
        e = varakozasok.get(kulcs)
        if not e:
            continue
        osszeg = sum(e["reszek"].values())
        jo, db = stat.get(kulcs, [0, 0])[:2]
        meres = f"eddig {jo}/{db} ({jo / db:.0%})" if db else "még nincs mért találat"
        if not e["irany"]:
            sorok.append(f"➡️ <b>{nev}: ma nincs javaslat</b> – a jelek ellentmondanak egymásnak, kimaradni is döntés.\n"
                         f"   Várható sáv {VARAKOZAS_VEGE}-ig: {szam(e['also'])} – {szam(e['felso'])}")
            continue
        vetel = e["irany"] > 0
        stop, cel = (e["also"], e["felso"]) if vetel else (e["felso"], e["also"])
        kockazat, nyereseg = abs(e["ar"] - stop), abs(cel - e["ar"])
        erosseg = "erősebb jel" if abs(osszeg) >= 2 else "gyenge jel"
        sorok.append(f"{'🟢' if vetel else '🔴'} <b>{nev}: {'VÉTEL (long)' if vetel else 'ELADÁS (short)'}</b> – {erosseg}, {meres}\n"
                     f"   Beszállás ~{szam(e['ar'])} · Stop {szam(stop)} ({szam(kockazat)} pont) · "
                     f"Cél {szam(cel)} ({szam(nyereseg)} pont) · Kiszállás legkésőbb {VARAKOZAS_VEGE}\n"
                     f"   Kockázat:nyereség = 1:{nyereseg / kockazat:.1f}; pozícióméret = a kockázatod (pl. a számla 1%-a) ÷ {szam(kockazat)} pont")
    return (f"🎯 <b>Mai javaslat ({t:%m.%d.} {t:%H:%M} – {VARAKOZAS_VEGE})</b>\n\n" + "\n\n".join(sorok)
            + f"\n\n<i>A stop és a cél a várható sáv széle (a múltban kb. 70%-ban nem lépett ki belőle). Az ár ~10 percet késik és "
              f"a határidős piacról jön, a Trading 212-ben a pontos számok eltérhetnek: a pont-távolságokat használd. "
              f"Szabályalapú gyakorló javaslat, nem befektetési tanács; amíg az „eddig” arány hetek alatt sem megy "
              f"55–60% fölé, csak gyakorló (demó) számlán kövesd. {VARAKOZAS_VEGE} után jön az eredmény.</i>")


def varakozas_ellenorzes(allapot, t):
    """21:00 után megnézi, bejött-e a 16:00-s várakozás (irány és sáv), és elküldi az eredményt."""
    v = allapot.get("varakozas") or {}
    vege = datetime.combine(t.date(), datetime.strptime(VARAKOZAS_VEGE, "%H:%M").time(), HELYI_IDO)
    if (v.get("datum") != t.date().isoformat() or v.get("kiertekelve")
            or t < vege + timedelta(minutes=KIERTEKELES_KESES_PERC)):
        return
    stat = allapot.setdefault("varakozas_stat", {})
    naplo_lista = allapot.setdefault("varakozas_naplo", [])
    sorok = []
    for nev, napi_jel, perces_jel, kulcs in HELYZETKEP_ESZKOZOK:
        e = v["eszkozok"].get(kulcs)
        if not e:
            continue
        try:
            gy = [g for g in yahoo_gyertyak(perces_jel, "5m", "1d") if e["ts"] <= g[0] < vege.timestamp()]
        except Exception as hiba:
            naplo(f"Kiértékelési hiba ({nev}): {hiba}")
            return  # a következő futás újrapróbálja
        if not gy or (gy[-1][0] < vege.timestamp() - 600 and t < vege + timedelta(minutes=90)):
            return  # még nincs meg a 21:00 előtti utolsó gyertya
        zaro, csucs, mely = gy[-1][4], max(g[2] for g in gy), min(g[3] for g in gy)
        valt = zaro - e["ar"]
        s = stat.setdefault(kulcs, [0, 0, 0, 0])
        sav_jo = e["also"] <= mely and csucs <= e["felso"]
        s[2] += sav_jo
        s[3] += 1
        kotes = ""
        if e["irany"]:
            irany_jo = valt * e["irany"] > 0
            s[0] += irany_jo
            s[1] += 1
            irany_szoveg = f"irány {'↑' if e['irany'] > 0 else '↓'} {'✅ bejött' if irany_jo else '❌ nem jött be'}"
            # a javasolt kötés: melyiket érte el előbb, a stopot vagy a célt (egy gyertyán belül mindkettő: stop)
            vetel = e["irany"] > 0
            stop, cel = (e["also"], e["felso"]) if vetel else (e["felso"], e["also"])
            kimenet, eredmeny = f"{VARAKOZAS_VEGE}-kor zárva", valt * e["irany"]
            for g in gy:
                if (g[3] <= stop) if vetel else (g[2] >= stop):
                    kimenet, eredmeny = f"stop {datetime.fromtimestamp(g[0], HELYI_IDO):%H:%M}-kor", -abs(e["ar"] - stop)
                    break
                if (g[2] >= cel) if vetel else (g[3] <= cel):
                    kimenet, eredmeny = f"cél {datetime.fromtimestamp(g[0], HELYI_IDO):%H:%M}-kor", abs(cel - e["ar"])
                    break
            r = eredmeny / abs(e["ar"] - stop)
            while len(s) < 6:
                s.append(0)
            s[4] += r
            s[5] += 1
            kotes = (f"\n   kötés: {kimenet}, {'+' if eredmeny >= 0 else '−'}{szam(abs(eredmeny))} pont ({r:+.1f} R); "
                     f"eddig összesen {s[4]:+.1f} R {s[5]} kötésből")
        else:
            irany_jo, irany_szoveg = None, "irány → (semleges, nem számít)"
        naplo_lista.append([v["datum"], kulcs, e["irany"], round(valt / e["ar"] * 100, 3), sav_jo, e["reszek"]])
        sorok.append(f"<b>{nev}</b>: {szam(e['ar'])} → {szam(zaro)} ({valt / e['ar'] * 100:+.2f}%); {irany_szoveg}; "
                     f"sáv {szam(e['also'])}–{szam(e['felso'])} {'✅ benne maradt' if sav_jo else '❌ kilépett'} "
                     f"(csúcs {szam(csucs)}, mélypont {szam(mely)}){kotes}\n"
                     f"   eddig: irány {s[0]}/{s[1]}{f' ({s[0] / s[1]:.0%})' if s[1] else ''}, sáv {s[2]}/{s[3]} ({s[2] / s[3]:.0%})")
    allapot["varakozas_naplo"] = naplo_lista[-300:]
    v["kiertekelve"] = True
    if sorok and telegram_kuld(f"🧾 <b>Bejött-e a mai javaslat? ({t:%m.%d.}, {VARAKOZAS_VEGE})</b>\n\n" + "\n\n".join(sorok)
                               + "\n\n<i>R = a kockázat egysége: +1 R = annyit nyertél, amennyit kockáztattál (1%-os kockázatnál a számla 1%-a). "
                                 "Ha az összes R hetek alatt sem megy tartósan pluszba, nem érdemes rá kereskedni.</i>", tema="arak"):
        naplo("Várakozás kiértékelve és elküldve.")


# --- Indítás ------------------------------------------------------------------


def cimzett_ellenorzes(allapot, t):
    """Ha megváltoztak a címzettek, egyszer üzen mindkét témába, hogy látszódjon: ide küld a bot."""
    ujlenyomat = hashlib.sha256(beallitasok_olvas().get("TELEGRAM_CHAT_ID", "").encode()).hexdigest()[:12]
    if allapot.get("cimzett_lenyomat") == ujlenyomat:
        return
    ok = [telegram_kuld(f"✅ Beállítás rendben: a hírfigyelő ide küldi a(z) {nev} üzeneteket.", tema=tema)
          for tema, nev in (("hirek", "híreket és a naptárat"), ("arak", "helyzetképet, javaslatot, kiértékelést és árugrást"))]
    if all(ok):
        allapot["cimzett_lenyomat"] = ujlenyomat
        naplo("Új címzettek visszaigazolva.")


def egy_ellenorzes():
    t = most()
    allapot = allapot_olvas()
    for lepes in (cimzett_ellenorzes, naptar_ellenorzes, hirek_ellenorzes, arugras_ellenorzes, helyzetkep_ellenorzes, varakozas_ellenorzes):
        try:
            lepes(allapot, t)
        except Exception as e:
            naplo(f"Hiba ({lepes.__name__}): {e}")
    allapot["utolso_futas"] = t.isoformat()
    allapot_ment(allapot)


def naptar_kiir():
    t = most()
    esemenyek = [e for e in fontos_esemenyek() if e["ido"].date() >= t.date()]
    if not esemenyek:
        print("A héten már nincs nagy hatású amerikai adat.")
    for ido, csoport in csoportosit(esemenyek):
        print(f"\n{ido:%m.%d. %H:%M}")
        for e in csoport:
            print(f"  {magyar_nev(e['title'])}  ({e['title']}; várt: {e.get('forecast') or '–'}, előző: {e.get('previous') or '–'})")


if __name__ == "__main__":
    parancs = sys.argv[1] if len(sys.argv) > 1 else ""
    if parancs == "--beallitas":
        telegram_beallitas()
    elif parancs == "--temak":
        telegram_temak()
    elif parancs == "--teszt":
        for tema, nev in (("hirek", "hírek"), ("arak", "árak")):
            ok = telegram_kuld(f"🔔 Próbaüzenet a hírfigyelőtől ({nev}).", tema=tema)
            print(f"{nev}: " + ("elküldve." if ok else "nem sikerült, nézd meg a hirfigyelo.log-ot."))
    elif parancs == "--ciklus":
        # pl. --ciklus 3 5: háromszor ellenőriz, 5 percenként (a GitHub Actions így hívja)
        db, perc = int(sys.argv[2]), float(sys.argv[3])
        for i in range(db):
            egy_ellenorzes()
            if i < db - 1:
                time.sleep(perc * 60)
    elif parancs == "--naptar":
        naptar_kiir()
    elif parancs == "--helyzetkep":
        szoveg = helyzetkep_szoveg(allapot_olvas(), most())
        print(szoveg)
        if "--kuld" in sys.argv:
            print("Elküldve." if telegram_kuld(szoveg, tema="arak") else "Nem sikerült.")
    else:
        egy_ellenorzes()
