# HochCiv – fest programmierte Bots

Bots für HochCiv mit fester, in Python geschriebener Logik. Gegner und Maßstab ist die
eingebaute **KI auf Stufe „schwer"** (`ki.js`). Ziel: sie verlässlich schlagen – im Duell auf
der Plättchenkarte, zu dritt und zu viert, auf der Originalkarte und auf eigenen Karten.
Wunder, Ereignisse und Marathon spielen keine Rolle.

## Grundsätze

- **Alle Entscheidungen fallen in Python.** Die Regelmaschine ist nur Orakel: Sie beantwortet
  Regelfragen (Kosten, Reichweiten, Angriffs- und Verteidigungswerte, Einkommen …) und führt
  Aktionen aus. Aktionen auf einer Kopie des Spiels auszuprobieren ist erlaubt (`fork`,
  `trials`).
- **Nie** `ki*`-, `bot*`- oder `rush*`-Funktionen der Regelmaschine aufrufen – das wäre die KI
  selbst. `ki.js` darf man lesen, um den Gegner zu verstehen.
- Die Regelmaschine liegt schreibgeschützt in `../multiplayer-server/HochCiv/js` (v108) und
  wird hier **nicht** verändert. Anderer Ort: Umgebungsvariable `HOCHCIV_ENGINE_DIR`.

## Aufbau

```
template/             der Rahmen, für alle Bots gleich
  engine/worker.js    lädt die Regelmaschine in einer Node-VM, spricht JSON-Zeilen über stdin/stdout
  hcbots/bridge.py    startet den Worker, schickt Anfragen
  hcbots/turn.py      Turn: Sicht und Werkzeuge für einen Zug (s. u.)
  hcbots/runner.py    spielt eine ganze Partie offline (Python-Sitze gegen KI-Sitze)
  hcbots/hexgrid.py   Hex-Abstände, Nachbarn, Distanzfelder
  example_bot.py      der kleinste Bot – Vorlage zum Kopieren
bots/<name>/bot.py    je Bot ein Ordner; die Klasse hat name = "<name>"
evaluate.py           Auswertung gegen die KI über viele Seeds und alle Sitze
```

## Ein Bot

Eine Klasse mit `name` und zwei Methoden:

- `place(seat, plan)` → `{"o", "cell"}` oder `None`: Startplättchen auf Plättchenkarten.
  `seat["choices"][o][cell]` ist das Einkommen einer Hauptstadt dort.
- `turn(t)`: einen Zug spielen. Den Zug **nicht** beenden, das macht der Runner.

Werkzeuge in `t` (`template/hcbots/turn.py`):

| | |
|---|---|
| `t.S`, `t.pi`, `t.me`, `t.round` | Spielstand, eigener Sitz, eigener Spieler, Runde |
| `t.cities(pi)`, `t.armies(pi)`, `t.capital(pi)`, `t.city_at(r,c)`, `t.army_at(r,c)`, `t.enemies()` | Abfragen |
| `t.q(fn, *args)` / `t.call([(fn, *args), …])` | Funktion der Regelmaschine aufrufen (eine / viele in einem Rundlauf). `"$S"` steht für den Spielstand, `{"$city": id}`, `{"$player": pi}` für Objekte; `("$player", pi)` liefert den Spieler selbst |
| `t.act(type, **params)` | Aktion ausführen; gibt `None` oder die Fehlermeldung zurück |
| `t.fork()` | Kopie des Spiels als eigener `Turn` (mit `close()` freigeben) |
| `t.trials([(acts, calls), …])` | viele Versuche auf je einer frischen Kopie in einem Rundlauf: je Versuch `None` (Aktion scheiterte) oder die Werte der `calls` danach |

Zufall in Kopien hat einen eigenen Seed – was dort klappt, muss im echten Spiel nicht genauso
ausgehen.

## Auswerten

```
python evaluate.py rusher                                  # alle Modi, 12 Seeds je Modus
python evaluate.py rusher --modes tile2 --seeds 64         # Duell auf der Plättchenkarte
python evaluate.py rusher --modes tile2 --seeds 64 --losses --opt soft_share=0.5 def_w=1
python evaluate.py rusher --seed0 20 --seeds 1 -v          # eine Partie, ausführlich
```

Modi: `tile2 tile3 tile4` (Plättchenkarte), `orig4` (Originalkarte zu viert),
`custom2 custom3 custom4` (erzeugte eigene Karten). Der Bot sitzt reihum auf jedem Platz;
„fair" ist 1/n. `--opt key=value` reicht Konstruktor-Argumente an den Bot durch.

Die Streuung ist groß: bei 64 Partien etwa ±6 Prozentpunkte. Vergleiche daher mit 64 Seeds
oder mehr, eine Partie dauert 20–40 s je Prozess.

## Regeln, die für Bots zählen (v108)

- **Eroberung:** zwei Treffer in Folge, je am Ende eines eigenen Zuges (Angriff > Verteidigung).
  Ein Fehlschlag setzt den Zähler zurück. Wer eine Hauptstadt erobert, gewinnt.
- **Angriff** je Armee in Reichweite: Macht (+3 mit Belagerung, ×2 mit Dynamit).
- **Verteidigung:** Bevölkerung × (1 + 1 in der Hauptstadt + MG) + 5 mit Stadtmauern + Macht mit
  Burgenbau + Macht × eigene Armeen in Reichweite. Ohne Helfer zählt Macht nicht.
- **Flankieren:** zwei eigene Stellungen in Reichweite, gegenüber (mit Taktik beliebig), und
  mehr Macht als der Gegner.
- **Macht** verfällt zu Beginn des eigenen Zuges (`powerDecay`). Übrige Ressourcen verfallen am
  Zugende.
- **Armeekosten:** Faktor × (Zahl der Armeen + 1); Faktor 5, mit Demokratie 4, mit
  Nationalismus 2.
- **Wachstum:** ein bezahlter Schritt je Stadt und Runde (+1 mit Keramik, +1 mit Dampfmaschine);
  kostet Nahrung und Münzen in Höhe der Bevölkerung.
- **Siege:** Hauptstadt erobern, Wirtschaft (Anteil an der Weltbevölkerung, `victoryOption`),
  Forschung (Singularität).

## Was die KI „schwer" tut (zum Verstehen, aus `ki.js`)

Sie bewertet Spielstände (Einkommen über einen Horizont, Bevölkerung, Techs, Siegfortschritt,
militärisches Risiko) und wählt gierig die beste Aktion je Ressource. Einen ersten Treffer
setzt sie, wenn ihre nächste Welle die beste Antwort des Verteidigers übertrifft
(`kiOffense`, `kiDefensePotential`: Münzeinkommen in Macht × eigene Armeen in Reichweite und
einen Zug entfernt, plus Wachstum). Python-Sitze laufen als `human` – gegen sie rechnet die
KI bei laufender Belagerung mit dem entschlossenen Angreifer. Sie greift früh an (Runde 3–5),
gern kleine Hauptstädte ohne Armeen daneben.

## Bots

### rusher (`bots/rusher`)

Sklavenwirtschaft und eine frühe Belagerung der nächsten feindlichen Hauptstadt.

1. Läuft eine eigene Belagerung, die diesen Zug gewinnt: zuerst zuschlagen.
2. Verteidigung von Städten mit einem Treffer (Wachstum, Helfer, Macht – Pläne auf Kopien
   durchgerechnet).
3. Angriff, wenn eine gewinnende Belagerung bezahlbar ist.
4. Wirtschaft (`econ.py`): je Schritt alle Kandidaten (Forschung, Freiwahlen, Wachstum,
   Gründung, Straßen) auf Kopien ausprobieren, den besten Gewinn je ausgegebener Ressource
   ausführen.
5. Opfern (Sklaverei), Armeen sammeln, Rest in Macht.

Optionen (`--opt`): `stage_armies`, `from_round`, `defend`, `soft_share` (vorbeugende
Verteidigung), `garrison_max`/`garrison_k`/`garrison_share` (Armeen an der Hauptstadt),
und für die Wirtschaft `threat_w`, `grow_step`, `coin_price`, `def_w`.

Stand (64 Seeds, tile2, gegen KI schwer): **etwa 42 %** – noch unter dem fairen Anteil.
Niederlagen vor allem durch frühe Eroberung der eigenen Hauptstadt (Runde 4–6), danach
Singularität der KI und Wirtschaftssieg. Vorbeugende Macht, Garnison und Gewicht auf die
Verteidigung der Hauptstadt brachten bisher nichts Messbares.

## Einrichten

Node.js (≥ 18) und Python (≥ 3.10).

```
pip install -r requirements.txt
python -m unittest discover tests
```

Zugangsdaten (`hcbot_…`-Tokens) und `*.env`-Dateien gehören nie ins Repository.
