# Einstieg für Coding-Agenten

Home-Assistant-Integration im Verzeichnis thingino_onvif; auf der offiziellen ONVIF-Integration aufbauend.

Zuerst [CONTRIBUTING.md](CONTRIBUTING.md) und die zur Aufgabe passenden Quellen daraus lesen. Den betroffenen Code und seine Aufrufer prüfen; Widersprüche zur Dokumentation vor der Umsetzung benennen.

## Projektgrenzen

- Domain `thingino_onvif`, ConfigEntry-/Entity-Identitäten und bestehende Installationen kompatibel halten.
- Async-Home-Assistant-Lifecycle und Cleanup respektieren; blockierende Geräteaufrufe nicht in den Event-Loop legen.
- Kamera-Zugangsdaten, RTSP-URLs mit Credentials und private Bildinhalte aus Logs, Diagnosen und PRs fernhalten.
- PTZ-Bewegungen, Presets und Thingino-HTTP-/Exec-Funktionen nur auf ausdrücklich freigegebener Testhardware prüfen; keine privaten Kameras oder ihre Positionen ungefragt verändern.
- Keine Geheimnisse oder produktiven Personen-/Gerätedaten in Tests, Logs, Screenshots oder PRs verwenden.
- Tests nicht abschwächen oder als bestanden melden, wenn sie nicht ausgeführt wurden.
- Nicht direkt auf `main` pushen. Ein Draft-PR-Auftrag gibt weder Merge noch Tags oder Veröffentlichung frei. Bereits konkret erteilte Freigaben gelten weiter.

## Skills

Die Skills liegen im Codex-Suchpfad `.agents/skills/`; andere Agenten lesen dieselben Dateien. Projektregeln stehen einmal in der Beitragsdokumentation und den dort genannten Fachquellen.

| Aufgabe | Skill |
| --- | --- |
| Änderung und Draft-PR | [thingino-aenderung](.agents/skills/thingino-aenderung/SKILL.md) |
| Prüfungen auswählen | [thingino-checks](.agents/skills/thingino-checks/SKILL.md) |
| Unabhängiges Review | [thingino-review](.agents/skills/thingino-review/SKILL.md) |
| Release vorbereiten | [thingino-release](.agents/skills/thingino-release/SKILL.md) |
