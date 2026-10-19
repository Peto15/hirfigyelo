# Hírfigyelő

Telegram-értesítések a USA Tech 100-at, az aranyat és a Bitcoint megmozgató eseményekről:
gazdasági naptár, friss hírek (szabályalapú irány-becsléssel), nagy árugrások és napi helyzetkép
napon belüli kereskedéshez. GitHub Actions futtatja negyedóránként.

Titkos beállítások (Settings → Secrets and variables → Actions): `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID`
(több címzett vesszővel elválasztva).

Nem kereskedési tanács.

## A hírirány-szabályok karbantartása
- `python3 eszkozok/szabaly_peldak.py` – kézzel címkézett példák; minden szabálymódosítás után futtasd le.
- `python3 eszkozok/szabaly_meres.py [napok] [perc] [--mentes adat.json]` – visszamérés: az elmúlt napok hírcímei
  után az ár tényleg a jelzett irányba ment-e, szabálycsoportonként. A `--mentes` fájllal a módosítás előtt és
  után ugyanazon az adaton lehet mérni.
