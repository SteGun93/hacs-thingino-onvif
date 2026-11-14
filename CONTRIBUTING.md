# An Thingino ONVIF mitarbeiten

## Auftrag und Umfang

Aufgabe, Nutzen und erkennbare Akzeptanzkriterien vor der Umsetzung festhalten. Ein klarer Nutzerauftrag reicht; ein Issue ist hilfreich, aber keine zusätzliche Pflicht. Offene Produktentscheidungen klären und bereits erlaubte unabhängige Arbeit fortsetzen.

Ein Thema pro Änderung. Vorhandene Lösungen und Aufrufer zuerst lesen; zusätzliche Einstellungen, Abhängigkeiten und Abstraktionen brauchen einen konkreten Nutzen. Fremde Änderungen erhalten, umfangreiches Aufräumen separat beauftragen. Ein Bugfix beginnt möglichst mit einem reproduzierenden Regressionstest. Bei Doku oder Konfiguration genügt eine passende überprüfbare Prüfung.

README und Code können bei Dienstnamen oder Einrichtung auseinanderliegen; `services.yaml`, Registrierungscode und Manifest gemeinsam lesen. Vorhandene Bytecode-Dateien sind keine zu pflegenden Quellen und werden nicht neu erzeugt oder committed.

## Verbindliche Quellen

- [README.md](README.md)
- [thingino_onvif/manifest.json](thingino_onvif/manifest.json)
- [thingino_onvif/services.yaml](thingino_onvif/services.yaml)

Aktuellen Codezustand und geplante Funktionen unterscheiden. Produkt-/Architekturänderungen werden im selben PR in der zuständigen Quelle nachgeführt. Fachliche oder API-Details an aktuellen Primärquellen prüfen, wenn sie das Verhalten entscheiden. Unsicherheiten kennzeichnen; Code und Test können dieselbe falsche Annahme enthalten. Agentenschritte stehen in Skills, Regeln und ihre Begründung in der Doku.

## Branch und Pull Request

Von aktuellem `origin/main` einen eigenen Themenbranch anlegen. Ein Branch und ein PR behandeln ein Thema. Vor jedem Push prüfen, ob ein vorhandener PR noch offen ist; nach einem Merge neu von der Hauptlinie beginnen. Fremde Commits nicht umschreiben; nötige Force-Pushes nur auf dem eigenen Branch mit `--force-with-lease`.

Titel und Beschreibung auf Deutsch, technische Bezeichner in der vorhandenen Schreibweise. Titel nach `Bereich: Ergebnis`; die Beschreibung nennt Änderung, Nutzen, Prüfergebnisse, Übersprungenes und offene Entscheidungen. Sie bleibt bei weiteren Pushes aktuell. Sichtbare Änderungen vergleichbar vorher/nachher mit Testdaten zeigen; Mobil-/Tastaturbedienung prüfen, soweit betroffen.

KI-Unterstützung im PR kenntlich machen, etwa `Mit Codex erstellt`. Keine erfundenen Co-Autorenadressen oder privaten Chatverläufe veröffentlichen.

## Prüfungen

Lokal gezielt prüfen, was sich geändert hat; bei breiten Änderungen die betroffenen Aufrufer einbeziehen. Vorhandene CI prüft ihren vollständigen Umfang auf dem aktuellen PR-Kopf. Eine reine Textänderung erfordert keine vollständige Laufzeitsuite. Werden Paket-/Buildausschlüsse geändert, den betroffenen Paketinhalt kontrollieren.

| Bereich | Prüfung und Voraussetzung |
| --- | --- |
| Python | Syntax der geänderten Dateien mit einer zur Ziel-Home-Assistant-Version passenden Python-Version prüfen, ohne Import der Integration oder Verbindung zu Geräten |
| Metadaten | `manifest.json`, `strings.json` und `icons.json` als JSON prüfen; Services, Übersetzungen, Domain und Entity-Referenzen abgleichen |
| Logik/CI | `python -m pip install -r requirements-ci.txt`, `python tools/check_source.py`, `python -m unittest discover -s tests -v`; CI auf Python 3.14 prüft Syntax, Metadaten und isolierte Helpertests mit HTTP-Mocks |
| Integration | In einer eigenen Home-Assistant-Testinstanz Setup, Reload und Cleanup prüfen; Hardwaretests und nötige Freigaben separat festhalten |
| Doku/Skills | README-Aussagen gegen Code prüfen, insbesondere tatsächliche Servicenamen und Installationslayout; Verweise, Skill-Metadaten und `git diff --check` |

Ein Bericht nennt Stand/Commit, tatsächlich ausgeführte Befehle, Ergebnis und ausgelassene Prüfungen mit Grund. Fehlende Datenbank, Hardware, Browser oder Werkzeuge sind kein bestandener Test. Eigene Testinstanzen verwenden; keine laufenden Dienste der Person beenden und keine produktiven Datenbankmigrationen für einen Test ausführen.

## Unabhängiges Review

Vor Freigabe prüft eine andere Person oder ein frischer Agent, der nicht mitgeschrieben hat. Wenn Subagenten verfügbar sind, einen frischen Subagenten ohne Entstehungsverlauf, Autorenbewertung oder gewünschtes Ergebnis in einem eigenen Checkout/Worktree einsetzen. Auftrag, Repository, Basis- und Kopfstand reichen als Start. Ohne diese Möglichkeit die Selbstprüfung als solche ausweisen und unabhängiges Review offenlassen.

Vier Fragen beantworten: gewollt und vollständig, fachlich durch Code/Quellen begründet, möglichst einfach, mit den Projektregeln vereinbar? Priorität im Review: Async-Lifecycle, fehlende Capabilities, Authentifizierungsfehler, PTZ-Stop, Presets und sensible Diagnosedaten mit Mocks.

Befunde brauchen Datei/Stelle, konkrete Auswirkung und einen überprüfbaren Beleg. Bei Logik möglichst ein auf dem PR-Stand fehlgeschlagener Test; bei Sicherheit, Doku und Konfiguration auch reproduzierbare Prüfung oder belegter Widerspruch. Unbelegte Vermutungen und Geschmacksfragen getrennt führen. Sicherheitsdetails nicht öffentlich ausbreiten.

Fixes nur im beauftragten Umfang; danach den betroffenen Diff prüfen. Weitere Runden nur bei neuen Änderungen, Befunden oder ungeklärten Risiken. Bei offenen Sicherheits-/Datenverlustproblemen oder Produktentscheidungen nicht mergen, sondern mit konkretem Entscheidungsbedarf zurückmelden. Ein frischer Agent ist kein zweiter GitHub-Account und ersetzt kein erforderliches Approval.

## Befugnisse und Release

Lokale Umsetzung und Prüfungen gehören zum Änderungsauftrag. Commit, Push, PR-Kommentare und Draft-PR im Umfang des Nutzerauftrags erledigen; vorhandene konkrete Freigaben nicht erneut abfragen. Ohne weitergehenden Auftrag bleibt der PR ein Entwurf. „Ready for review“, Merge, Tags, Deployment und externe Veröffentlichung werden nur erledigt, wenn dafür ein Auftrag vorliegt und die beschriebenen Voraussetzungen erfüllt sind. Repository-Rechte und Zugangsdaten nicht beiläufig ändern.

Ein automatisierter Release-/HACS-Publishing-Workflow ist derzeit nicht eingerichtet. Vor einer freigegebenen Version den Stand von `thingino_onvif/manifest.json`, Installationslayout und HACS-Anforderungen gegen die aktuelle offizielle Dokumentation prüfen. Repository-Layout oder Domain nicht beiläufig ändern. Ohne konkreten Auftrag nur Releasevorschlag und Testnachweise vorbereiten, keine Version oder Tags erzeugen.

Grüne CI und ein dokumentiertes Review sind keine technisch erzwungene Merge-Sperre. Bestehende Repository-Schutzregeln beachten; ihre Einrichtung ist ein gesonderter Auftrag.
