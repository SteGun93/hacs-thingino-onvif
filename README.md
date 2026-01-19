# Thingino ONVIF PTZ (HACS)

Custom Home Assistant Integration für **Thingino-basierte ONVIF-Kameras** mit Fokus auf **zuverlässiger PTZ-Steuerung**, Presets und sauberer Home-Assistant-Integration.

Diese Integration ist ein **Fork der offiziellen Home-Assistant ONVIF Integration**, wurde jedoch gezielt angepasst, um mit günstigen PTZ-Kameras (z. B. Galayou Y4 / Ingenic T31) stabil zu funktionieren, auch wenn ONVIF-Capabilities unvollständig oder fehlerhaft gemeldet werden.

---

## ✨ Features

* 🔌 **Native Home-Assistant Integration** (keine Gateways, kein SSH, kein Frigate-Zwang)
* 📷 **Mehrere Kameras** über Config Flow verwaltbar
* 🎥 **ONVIF Media Profiles** (Streams & Snapshots)
* 🎮 **PTZ-Steuerung direkt in Home Assistant**

  * Pan / Tilt / Zoom
  * Stop
  * Presets (Set / GoTo / Remove)
  * **Goto Home Position**
  * **Set Home Position**
* 🧠 **Tolerante PTZ-Erkennung**

  * PTZ wird aktiviert, wenn Befehle funktionieren – nicht nur wenn Capabilities „schön“ sind
* 🔐 **User/Passwort-Authentifizierung**
* 🧩 **HACS-fähig**
* 🧪 **Diagnostics & Debug Logging**
* ⚙️ **Skalierbar** für viele Kameras (keine Einzel-Skripte)

---

## 🎯 Zielgruppe

Diese Integration richtet sich an Nutzer, die:

* Thingino-Firmware einsetzen
* günstige PTZ-Kameras verwenden
* PTZ **über Home Assistant steuern** möchten
* Frigate **nur für Automatisierung/Analyse**, nicht für Steuerung nutzen wollen

---

## 🧱 Unterstützte Geräte (getestet / Ziel)

* Galayou Y4 (Ingenic T31L, SC2336)
* Thingino ONVIF (Port 80)
* RTSP Streams:

  * `ch0` → 1080p
  * `ch1` → Substream

Andere ONVIF-PTZ-Kameras **können funktionieren**, sind aber nicht garantiert.

---

## 📦 Installation (HACS)

### 1️⃣ Repository zu HACS hinzufügen

HACS → **Integrationen** → **⋮** → *Benutzerdefinierte Repositories*

* Repository: `https://github.com/<DEIN_GITHUB_NAME>/hacs-thingino-onvif`
* Kategorie: **Integration**

### 2️⃣ Integration installieren

* In HACS nach **Thingino ONVIF PTZ** suchen
* Installieren
* Home Assistant neu starten

---

## ➕ Integration hinzufügen

Home Assistant → **Einstellungen** → **Geräte & Dienste** → **Integration hinzufügen**

➡️ **Thingino ONVIF PTZ**

### Benötigte Angaben:

* Host / IP (z. B. `192.168.1.111`)
* Port (Standard: `80`)
* Benutzername
* Passwort

Die Verbindung wird beim Setup geprüft.

---

## 🎮 PTZ-Steuerung in Home Assistant

Nach erfolgreichem Setup stehen folgende Möglichkeiten zur Verfügung:

### 🔘 Services

* `thingino_onvif.ptz_move`
* `thingino_onvif.ptz_stop`
* `thingino_onvif.ptz_zoom`
* `thingino_onvif.goto_home`
* `thingino_onvif.set_home`
* `thingino_onvif.goto_preset`
* `thingino_onvif.set_preset`
* `thingino_onvif.remove_preset`

➡️ Ideal für Automationen & Skripte.

### 🧭 Entities

Je nach Kamera:

* Buttons (Home, Presets)
* Selects (Preset-Auswahl)
* Kamera-Entity mit Stream & Snapshot

---

## 🏠 „Home“-Position (wichtig)

Thingino implementiert **Home nicht als Preset**, sondern als eigenen ONVIF-Befehl:

* `GotoHomePosition`
* `SetHomePosition`

Diese Integration:

* trennt **Home** bewusst von Presets
* stellt Home trotzdem in HA sauber bereit
* kann optional ein „virtuelles Home-Preset“ anbieten

---

## 🧪 Debug & Diagnose

* Debug Logging:

  ```yaml
  logger:
    default: info
    logs:
      custom_components.thingino_onvif: debug
  ```
* Diagnostics verfügbar (Credentials werden maskiert)

---

## 🔒 Sicherheit

* Keine SSH-Keys
* Keine externen Gateways
* Credentials nur über Config Flow
* Keine Klartext-Passwörter in Logs oder Diagnostics

---

## ⚠️ Hinweise

* Diese Integration **ersetzt nicht** die offizielle ONVIF-Integration
* Beide können parallel existieren
* Domain: `thingino_onvif` (keine Kollision)

---

## 🚧 Status

**Work in progress / frühe Version**

Geplant:

* bessere UI-Controls für PTZ
* Preset-Sync verbessern
* weitere Thingino-Spezifika

---

## 📜 Lizenz

Apache License 2.0
(entsprechend Home-Assistant Core ONVIF Integration)

---

## 🤝 Mitmachen

Pull Requests, Issues und Tests mit weiteren Kameras sind ausdrücklich willkommen 🚀
