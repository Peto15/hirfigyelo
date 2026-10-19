"""A hírirány-szabályok visszamérése valódi adatokon.

Az elmúlt napok hírcímeire lefuttatja az IRANY_SZABALYOK-at, és megnézi, hogy a hír után 30 perccel az ár
tényleg a jelzett irányba ment-e (Yahoo 1 perces adat, ~7 nap érhető el). Szabálycsoportonként is számol, és
kilistázza azokat a címeket, amelyek után nagyot mozdult az ár, de egyik szabály sem illett rájuk.
Telegram-üzenetet nem küld, az állapotot nem módosítja.

Futtatás a bot mappájából:  python3 eszkozok/szabaly_meres.py [napok=7] [perc=30] [--mentes fájl.json]
A --mentes fájlba elmenti a letöltött címeket és árakat; ha a fájl már létezik, abból dolgozik, így egy
szabálymódosítás előtt és után ugyanazon az adaton lehet mérni.
"""
import json
from datetime import datetime
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import hirfigyelo as hf  # noqa: E402

MENTES = sys.argv[sys.argv.index("--mentes") + 1] if "--mentes" in sys.argv else None
_ARG = [a for a in sys.argv[1:] if not a.startswith("--") and a != MENTES]
NAPOK = int(_ARG[0]) if len(_ARG) > 0 else 7
PERC = int(_ARG[1]) if len(_ARG) > 1 else 30


def hircimek():
    """Egyedi hírcímek a megbízható forrásokból, a bot saját kereséseivel."""
    cimek = {}
    forrasok = [(nev, lambda u=url, n=nev: hf.rss_elemek(u, n)) for nev, url in hf.OLVASHATO_CSATORNAK]
    forrasok += [(k[:30], lambda k=k: hf.google_hirek(k, f"{NAPOK}d")) for k in hf.HIR_KERESESEK]
    for nev, lekeres in forrasok:
        try:
            for h in lekeres():
                if not any(f.lower() in h["forras"].lower() for f in hf.MEGBIZHATO_FORRASOK):
                    continue
                cimek.setdefault(hf.hir_kulcs(h["cim"]), h)
        except Exception as e:
            print(f"  (letöltési hiba: {nev}: {e})", file=sys.stderr)
    return list(cimek.values())


def arak():
    """Eszközönként a 1 perces záróárak (időbélyeg, ár) listája."""
    ki = {}
    for e, (_, jel) in hf.HIR_JEL_ESZKOZOK.items():
        ki[e] = [(g[0], g[4]) for g in hf.yahoo_gyertyak(jel, "1m", "7d")]
    return ki


def mozgas(sor, ts):
    """Az ár változása (%) ts és ts+PERC perc között, vagy None, ha zárva volt a piac."""
    elotte = [a for a in sor if ts - 600 <= a[0] <= ts]
    utana = [a for a in sor if ts + PERC * 60 - 600 <= a[0] <= ts + PERC * 60]
    if not elotte or not utana:
        return None
    return (utana[-1][1] / elotte[-1][1] - 1) * 100


def illeszkedo_szabalyok(cim):
    """A címre illeszkedő (csoport, hatás) párok, ugyanazzal a logikával, mint az irany_becsles."""
    talalt, csoportok = [], set()
    for csoport, minta, hatas, _ in hf.IRANY_SZABALYOK:
        if csoport in csoportok:
            continue
        if csoport == "haboru" and "beke" in csoportok:
            continue
        if csoport == "kamat" and hf.NEM_FED.search(cim) and not hf.FED.search(cim):
            continue
        if csoport == "jelentes" and "munka" in csoportok:
            continue
        if minta.search(cim):
            csoportok.add(csoport)
            talalt.append((csoport, hatas))
    return talalt


def adatok():
    if MENTES and os.path.exists(MENTES):
        d = json.load(open(MENTES))
        hirek = [{"cim": c, "forras": f, "ido": datetime.fromisoformat(i)} for c, f, i in d["hirek"]]
        return hirek, {e: [tuple(x) for x in sor] for e, sor in d["arak"].items()}
    print(f"Hírek letöltése az elmúlt {NAPOK} napból…", file=sys.stderr)
    hirek = hircimek()
    print("Árak letöltése…", file=sys.stderr)
    arsor = arak()
    if MENTES:
        json.dump({"hirek": [[h["cim"], h["forras"], h["ido"].isoformat()] for h in hirek], "arak": arsor},
                  open(MENTES, "w"))
    return hirek, arsor


def main():
    hirek, arsor = adatok()
    print(f"{len(hirek)} egyedi cím.\n")

    ossz = defaultdict(lambda: [0, 0])          # eszköz -> [jó, db] (a bot végső iránya)
    csop = defaultdict(lambda: [0, 0])          # (csoport, eszköz) -> [jó, db]
    mindig_fel = defaultdict(lambda: [0, 0])    # összehasonlításnak: „mindig emelkedik”
    tevedesek, kimaradt = [], []
    for h in hirek:
        ts = h["ido"].timestamp()
        irany, _ = hf.irany_becsles([(h["cim"], 3)])
        mozg = {e: mozgas(arsor[e], ts) for e in arsor}
        for e, m in mozg.items():
            if m is None or m == 0:
                continue
            mindig_fel[e][0] += m > 0
            mindig_fel[e][1] += 1
            p = irany.get(e)
            if p:
                jo = (p > 0) == (m > 0)
                ossz[e][0] += jo
                ossz[e][1] += 1
                if not jo:
                    tevedesek.append((abs(m), e, m, h["cim"]))
        szabalyok = illeszkedo_szabalyok(h["cim"])
        for cs, hatas in szabalyok:
            for e, ertek in hatas.items():
                m = mozg.get(e)
                if m:
                    csop[(cs, e)][0] += (ertek > 0) == (m > 0)
                    csop[(cs, e)][1] += 1
        if not szabalyok:
            nagy = max(((abs(m), e, m) for e, m in mozg.items() if m is not None), default=None)
            if nagy:
                kimaradt.append((nagy[0], nagy[1], nagy[2], h["cim"]))

    nevek = {e: n for e, (n, _) in hf.HIR_JEL_ESZKOZOK.items()}
    pct = lambda jo, db: f"{jo}/{db} ({jo / db:.0%})" if db else "–"
    print(f"=== A bot iránya {PERC} perc alatt bejött-e ===")
    for e in nevek:
        print(f"  {nevek[e]:13} {pct(*ossz[e]):14}   (összevetésül „mindig fel”: {pct(*mindig_fel[e])})")
    print("\n=== Szabálycsoportonként ===")
    for (cs, e), (jo, db) in sorted(csop.items(), key=lambda x: (x[0][0], x[0][1])):
        jel = "  ⚠️" if db >= 5 and jo / db < 0.5 else ""
        print(f"  {cs:10} {nevek[e]:13} {pct(jo, db)}{jel}")
    print("\n=== Legnagyobb tévedések ===")
    for _, e, m, cim in sorted(tevedesek, reverse=True)[:15]:
        print(f"  {nevek[e]:13} {m:+.2f}%  {cim[:110]}")
    print("\n=== Nagy mozgás, de nem illett rá szabály ===")
    for _, e, m, cim in sorted(kimaradt, reverse=True)[:20]:
        print(f"  {nevek[e]:13} {m:+.2f}%  {cim[:110]}")


if __name__ == "__main__":
    main()
