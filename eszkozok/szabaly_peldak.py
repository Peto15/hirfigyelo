"""Regressziós példák a hírirány-szabályokhoz (IRANY_SZABALYOK).

Minden sor: hírcím, és az elvárt irány eszközönként (+ emelkedés, - esés, 0 bizonytalan/ellentétes,
. nincs jelzés), sorrendben: USA Tech 100, arany, Bitcoin. Szabálymódosítás után futtasd le:
    python3 eszkozok/szabaly_peldak.py
Hibakóddal lép ki, ha valamelyik példa eltér.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import hirfigyelo as hf  # noqa: E402

PELDAK = [
    # béke / háború
    ("Putin reportedly proposes three-way summit with Trump and Xi on Ukraine war — without Zelenskyy", "+-+"),
    ("Russia and Ukraine agree to ceasefire talks in Istanbul", "+-+"),
    ("Trump says he wants to end the war in Ukraine quickly", "+-+"),
    ("Russia launches massive missile attack on Kyiv", "-+-"),
    ("Israel strikes Iran as conflict escalates", "-+-"),
    ("Talks collapse as fighting intensifies; Russia launches new missile attack", "-+-"),
    ("Hackers claim cyberattack on major U.S. bank", "..."),
    # kamat (Fed)
    ("Fed signals rate hikes ahead as inflation persists", "-.-"),
    ("Gold Prices Rise Above $4,200 in Futures Trading as Fed Rate Hike Expectations Ease", "+.+"),
    ("Traders pare bets on Fed rate hike after soft data", "+.+"),
    ("Fed may now have to hike twice or increase by a half point in December, traders bet", "-.-"),
    ("Fed's Warsh opens door to rate cuts next year", "+.+"),
    ("BoE's Pill says a rate hike need not be start of long series", "..."),
    ("ECB raises rates again to fight inflation", "..."),
    ("Easing tensions lift European shares", "..."),
    # infláció
    ("Consumer prices rose 3.7% annually in September as gas prices skyrocket", "--."),
    ("US inflation cools more than expected in September", "++."),
    ("Fed's Warsh to Get Final Word in Bangkok as Inflation Roars Back", "--."),
    # vám, szankció, olaj, hozam
    ("Trump announces new tariffs on Chinese chips", "-+."),
    ("US and China agree to pause tariffs for 90 days", "+-."),
    ("Washington suspends some sanctions on Russian oil", "+.."),
    ("EU imposes new sanctions on Russia", ".+."),
    ("Oil falls after Trump waives some Russia sanctions", "+.."),
    ("Oil jumps as Middle East tensions flare", "-+."),
    ("Treasury yields rise to highest since 2007", "-.."),
    ("TSX futures rise amid easing bond yields, higher gold prices", "+.."),
    # gyorsjelentés, részvénypiac
    ("Nvidia beats estimates and raises guidance", "+.."),
    ("Apple misses revenue expectations, shares slide", "-.."),
    ("Nasdaq jumps as tech shares rally", "+.."),
    # kripto
    ("Bitcoin ETFs see $500 million in inflows", "..+"),
    ("Crypto exchange hacked, $200 million stolen", "..-"),
    ("Bitcoin slides below $80,000", "..."),
    ("Gold hits record high", "..."),
    # válság, munkaerőpiac
    ("US government shutdown begins as talks fail", "-+-"),
    ("Payrolls beat expectations with strong hiring", ".--"),
    ("By default, the app shares your location", "..."),
]


def jel(p):
    return "." if p is None else "0" if p == 0 else "+" if p > 0 else "-"


def main():
    hibak = 0
    for cim, vart in PELDAK:
        irany, _ = hf.irany_becsles([(cim, 3)])
        kapott = "".join(jel(irany[e]) for e in ("nq", "arany", "btc"))
        if kapott != vart:
            hibak += 1
            print(f"✗ várt {vart}, kapott {kapott}:  {cim}")
    print(f"\n{len(PELDAK) - hibak}/{len(PELDAK)} példa rendben.")
    sys.exit(1 if hibak else 0)


if __name__ == "__main__":
    main()
