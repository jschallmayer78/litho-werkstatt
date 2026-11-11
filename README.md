# Lithophane-Werkstatt

Webservice, der aus Fotos druckfertige Lithophanes erzeugt: als Lampenschirm
(Zylinder, optional mit Boden und Deckel) oder als flache Platte, einfarbig
oder mehrfarbig. Das Ergebnis ist **eine einzige 3MF-Datei**, in der jedes
Teil schon seinem Filament-Platz zugeordnet ist.

## Starten

Mit Docker (empfohlen):

    docker compose up -d --build

Danach im Browser `http://<host>:8080` öffnen.

Ohne Docker (Python 3.10 oder neuer):

    python3 -m venv .venv
    source .venv/bin/activate
    pip install -r requirements.txt
    uvicorn app.main:app --host 0.0.0.0 --port 8080

Hinter einem Reverse Proxy funktioniert die Oberfläche auch unter einem
Unterpfad (z. B. `/lithophane/`), weil sie nur relative Pfade nutzt.
Ein Upload-Limit von mindestens 150 MB im Proxy einplanen.

## Was erzeugt wird

| Form | Farbe | Inhalt der 3MF |
|---|---|---|
| Lampenschirm | einfarbig | Relief, optional Boden + Deckel |
| Lampenschirm | mehrfarbig | weißes Relief + Farbhaut-Mosaik aus 2–8 Filamenten |
| Platte | einfarbig | ein Relief je Bild |
| Platte | Farbmischung | Weiß + Cyan/Magenta/Gelb-Schichten (4 Filamente) |
| Platte | eigene Farben | weißes Relief + Mosaik aus 2–8 Filamenten |

Filament 1 ist immer Weiß. Boden und Deckel werden ebenfalls mit Filament 1
gedruckt. Beim Lampenschirm werden sie aufgeschraubt: Der Schirm bekommt oben
und unten ein Außengewinde (3 mm Steigung, 2,5 Gänge, 45°-Flanken, stützfrei
druckbar), Boden und Deckel das passende Innengewinde. Bodenstärke, Tiefe der
Vertiefung für die Lichtquelle und das Gewindespiel sind einstellbar.

Die Größe des Lampenschirms ergibt sich standardmäßig aus der Lichtquelle
(+1 mm). Alternativ lässt sich der Außen-Ø der ganzen Lampe vorgeben (breiteste
Stelle, also Boden/Deckel bzw. ohne sie der Schirm) oder unter den
Feineinstellungen der Innen-Ø des Schirms.

## Hinweise zur 3MF-Datei

Die Datei enthält Standard-3MF mit Farben je Teil sowie die Teile- und
Filament-Zuordnung im PrusaSlicer-Projektformat (zusätzlich als
`model_settings.config` für Orca Slicer und Bambu Studio). PrusaSlicer liest
diese Zuordnung direkt. Falls ein Slicer die Filamente nicht automatisch
übernimmt, sind die Teile trotzdem getrennt und benannt („Relief weiß“,
„Farbe Rot“ …) und lassen sich in der Objektliste zuweisen.

## Einstellungen per Umgebungsvariable

| Variable | Standard | Bedeutung |
|---|---|---|
| `MAX_FILES` | 6 | Bilder pro Auftrag |
| `MAX_FILE_MB` | 25 | Größe je Bild |
| `MAX_PARALLEL` | 2 | gleichzeitige Berechnungen |
| `KEEP_MINUTES` | 60 | Aufbewahrung der fertigen Dateien |
| `JOB_DIR` | Temp-Verzeichnis | Ablage der fertigen Dateien |

## API

`POST /api/generate` (multipart: `files` = Bilder, `options` = JSON) liefert
Vorschau, Filament-Legende und eine Job-ID; `GET /api/download/{id}` liefert
die 3MF. Die Optionen sind in `app/main.py` (Klasse `Options`) beschrieben.
