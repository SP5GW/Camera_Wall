CamWall - teksty strony i tłumaczenia / page texts and translations
===================================================================

Każdy język to jeden folder / Each language is one folder:

  www/lang/<kod>/strings.json      krótkie teksty / short texts
  www/lang/<kod>/help.html         pomoc (przycisk ?) / help (? button)
  www/lang/<kod>/add-camera.html   pomoc "Dodaj kamerę" / "Add camera" help


ZMIANA TEKSTU
  1. Otwórz plik w Notatniku (zapisuj jako UTF-8).
  2. W strings.json zmieniaj tylko tekst w cudzysłowie PO dwukropku:
        "errUnreachable": "Kamera nieosiągalna",
     Nie zmieniaj nazw po lewej stronie. Fragmenty w nawiasach klamrowych,
     np. {reason}, {s}, {code}, to miejsca na dane - można je przestawiać
     w zdaniu, ale nie usuwać ani nie zmieniać.
     Cudzysłów wewnątrz tekstu zapisuj jako \"
  3. Odśwież stronę w przeglądarce. Restart CamWall nie jest potrzebny.

  Brakujący tekst zostanie pokazany po angielsku (z folderu en), więc
  błąd w tłumaczeniu nie zatrzyma strony. "python camwall.py --check"
  pokazuje błędy składni (z numerem linii) i brakujące teksty.


NOWY JĘZYK (np. niemiecki)
  1. Skopiuj folder "en" jako "de".
  2. W de/strings.json zmień sekcję _meta:
        "_meta": { "label": "DE", "name": "Deutsch", "locale": "de-DE", "order": 3 },
     label  - napis na przycisku
     locale - format daty w zegarze
     order  - kolejność przycisków (mniejsza liczba = bardziej z lewej)
  3. Przetłumacz teksty w trzech plikach.
  4. Uruchom CamWall ponownie - przycisk DE pojawi się sam.

  Folder "en" jest językiem zapasowym - nie usuwaj go.


---------------------------------------------------------------------------
CHANGING A TEXT
  Edit the text in quotes AFTER the colon in strings.json, or the HTML in
  the .html files, save as UTF-8, reload the page. Keep {placeholders}.

NEW LANGUAGE
  Copy folder "en" to e.g. "de", edit "_meta" in de/strings.json
  (label, name, locale, order), translate the three files, restart CamWall.
  Missing texts fall back to English. Do not delete the "en" folder.
