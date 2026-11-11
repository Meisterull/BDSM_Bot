"""
Tests Prüf-Netz für Coach-Weitergaben (09.11.2026, services/auftrag_netz + handlers/domina):

Tag-Test mit einem lokalen Modell: der Coach schmückte [SPRACHNACHRICHT:]-Inhalte aus
(Zusatzaufgabe, „morgen" → „heute", erfundene Uhrzeit). Das Netz gleicht den
Inhalt deterministisch gegen den Wortlaut der Dom-Seite ab; bei Funden geht
nichts raus, sie bekommt ✅ so senden / ✍️ nur meine Worte / ❌ nicht senden.

    python3 tests/test_auftrag_netz.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tests.test_sprachnachricht_aufgabe as basis  # noqa: E402  (Stubs, _Welt, _update …)
from bot import state  # noqa: E402
from bot.handlers import domina  # noqa: E402
from bot.services import auftrag_netz as netz  # noqa: E402
from types import SimpleNamespace  # noqa: E402

DOM = basis.DOM
_run = basis._run


# --------------------------------------------------------------------------
# pruefe() – ausgedachte Beispiele nach den Fehlermustern des Tag-Tests
# --------------------------------------------------------------------------

def test_treue_weitergaben_still():
    assert netz.pruefe("Kleine Maus, heute Abend räumst du die Spülmaschine aus und polierst die Gläser. Verstanden?",
                       "Er muss heute Abend die Spülmaschine ausräumen und die Gläser polieren.") == []
    assert netz.pruefe("Morgen ab 20 Uhr trägst du den mittleren Plug, ohne zu jammern.",
                       "Schick ihm: morgen ab 20 Uhr mittleren Plug tragen, kein Jammern") == []
    assert netz.pruefe("Kleine Maus, heute kniest du neben dem Sofa und massierst meine Füße, solange ich will.",
                       "Er muss heute neben dem Sofa knien und meine Füße massieren solange ich will") == []
    # Bezug auf Coach-Vorschlag im Verlauf
    assert netz.pruefe("Fünf Minuten knien vor dem Bett, bevor du schlafen gehst.",
                       "Schick ihm das bitte", "Wie wär's: fünf Minuten knien vor dem Bett, bevor er schlafen geht?") == []


def test_erfundene_zeit_und_zahl():
    f = netz.pruefe("Kleine Maus, heute Abend ab 20 Uhr stehst du in der Ecke, bis ich dich hole.",
                    "Er muss in der Ecke stehen, bis ich ihn hole.")
    assert any("20" in x for x in f) and any("heute" in x for x in f), f
    f = netz.pruefe("Heute Abend trägst du den Plug.", "Sag ihm, morgen Abend trägt er den Plug.")
    assert any("heute" in x for x in f), f
    f = netz.pruefe("Du bekommst 12 Hiebe.", "Sag ihm, er bekommt zehn Hiebe.")
    assert any("12" in x for x in f), f


def test_erfundene_zusatzaufgabe():
    f = netz.pruefe("Ich brauche zwei Fotos: eins, wie du 4x Kniebeugen machst, und eins, wie du barfuß "
                    "den Flur wischst. Schick sie mir, sobald du zu Hause bist.",
                    "Schreib ihm, ich erwarte zwei Fotos, wo er 4x Kniebeugen macht.")
    assert any(x.startswith("neu:") for x in f), f


def test_zahlwort_und_artikel():
    # „eine Stunde" = 1, „ein Video" ist Einheit-los → kein Zahlfund
    assert netz.pruefe("Du trägst ihn eine Stunde.", "Er soll ihn 1 Stunde tragen") == []
    assert netz.pruefe("Schick mir ein Foto davon.", "Er soll mir ein Foto davon schicken") == []


# --------------------------------------------------------------------------
# handle() + Callback
# --------------------------------------------------------------------------

def _welt(antwort):
    w = basis._Welt(antwort).install()
    os.environ["AUFTRAG_NETZ"] = "1"
    for k in domina._NETZ_KEYS:
        state.get(DOM).pop(k, None)
    state.get_history = lambda chat_id: []

    async def _sn(update, context, inhalt, msg=None):
        w.sn_inhalte.append(inhalt)
        return w.sn_gesendet
    domina._sende_sprachnachricht_an_sklaven = _sn
    return w


def _frage(update):
    for text, markup in update.message.replies:
        if text.startswith("🔎"):
            return text, markup
    return None


def _press(data):
    q = basis._Query(data)
    u = SimpleNamespace(callback_query=q, effective_chat=SimpleNamespace(id=int(DOM)))
    _run(domina.callback_sn_netz(u, basis._context()))
    return q


def test_handle_treu_geht_direkt_raus():
    w = _welt("Geht raus.\n[SPRACHNACHRICHT: Ich bin stolz auf dich]")
    u = basis._update("Sag ihm, ich bin stolz auf ihn")
    _run(domina.handle(u, basis._context()))
    assert w.sn_inhalte == ["Ich bin stolz auf dich"] and _frage(u) is None


def test_handle_ausgeschmueckt_haelt_zurueck():
    w = _welt("Schick ich ihm.\n[SPRACHNACHRICHT: Heute Abend ab 20 Uhr trägst du den Plug]\n"
              "[AUFGABE: Heute ab 20 Uhr Plug tragen]")
    u = basis._update("Schreib ihm, er muss morgen den Plug tragen")
    _run(domina.handle(u, basis._context()))
    assert w.sn_inhalte == [], "nichts darf ungefragt raus"
    assert w.bestaetigungen == [], "kein paralleler Aufgaben-Dialog"
    fr = _frage(u)
    assert fr and "20" in fr[0] and "Heute Abend ab 20 Uhr" in fr[0], u.message.replies
    s = state.get(DOM)
    assert s["netz_task"] == "Heute ab 20 Uhr Plug tragen" and s["netz_quelltext"].startswith("Schreib ihm")
    assert len(w.gespeichert) == 1


def test_callback_ok_wort_nein():
    # ✅ so senden → Inhalt raus + Aufgaben-Angebot mit Tag-Text
    w = _welt("x\n[SPRACHNACHRICHT: Heute Abend ab 20 Uhr trägst du den Plug]\n[AUFGABE: Heute ab 20 Uhr Plug tragen]")
    u = basis._update("Schreib ihm, er muss morgen den Plug tragen")
    _run(domina.handle(u, basis._context()))
    kennung = state.get(DOM)["netz_id"]
    q = _press(f"snnetz:ok:{kennung}")
    assert q.markup_entfernt and w.sn_inhalte == ["Heute Abend ab 20 Uhr trägst du den Plug"]
    assert state.get(DOM)["sn_aufgabe_text"] == "Heute ab 20 Uhr Plug tragen"
    assert "netz_id" not in state.get(DOM)
    # Doppel-Tap → veraltet
    q2 = _press(f"snnetz:ok:{kennung}")
    assert len(w.sn_inhalte) == 1 and any("nicht mehr aktuell" in r[0] for r in q2.message.replies)

    # ✍️ nur meine Worte → ihr Wortlaut geht raus, Aufgabe aus ihrem Wortlaut
    w = _welt("x\n[SPRACHNACHRICHT: Heute Abend ab 20 Uhr trägst du den Plug]")
    u = basis._update("Schreib ihm, er muss morgen den Plug tragen")
    _run(domina.handle(u, basis._context()))
    _press(f"snnetz:wort:{state.get(DOM)['netz_id']}")
    assert w.sn_inhalte == ["Schreib ihm, er muss morgen den Plug tragen"]
    assert state.get(DOM)["sn_aufgabe_text"] == "Schreib ihm, er muss morgen den Plug tragen"

    # ❌ nicht senden
    w = _welt("x\n[SPRACHNACHRICHT: Heute Abend ab 20 Uhr trägst du den Plug]")
    u = basis._update("Schreib ihm, er muss morgen den Plug tragen")
    _run(domina.handle(u, basis._context()))
    for k in domina._SN_AUFGABE_KEYS:
        state.get(DOM).pop(k, None)
    q = _press(f"snnetz:nein:{state.get(DOM)['netz_id']}")
    assert w.sn_inhalte == [] and "sn_aufgabe_id" not in state.get(DOM)
    assert any("Nicht gesendet" in r[0] for r in q.message.replies)


def test_netz_aus_per_env():
    w = _welt("x\n[SPRACHNACHRICHT: Heute Abend ab 20 Uhr trägst du den Plug]")
    os.environ["AUFTRAG_NETZ"] = "0"
    u = basis._update("Schreib ihm, er muss morgen den Plug tragen")
    _run(domina.handle(u, basis._context()))
    assert len(w.sn_inhalte) == 1 and _frage(u) is None
    os.environ["AUFTRAG_NETZ"] = "1"


def _run_alle():
    test_treue_weitergaben_still()
    test_erfundene_zeit_und_zahl()
    test_erfundene_zusatzaufgabe()
    test_zahlwort_und_artikel()
    test_handle_treu_geht_direkt_raus()
    test_handle_ausgeschmueckt_haelt_zurueck()
    test_callback_ok_wort_nein()
    test_netz_aus_per_env()
    print("✅ Alle Prüf-Netz-Tests bestanden")


if __name__ == "__main__":
    _run_alle()
