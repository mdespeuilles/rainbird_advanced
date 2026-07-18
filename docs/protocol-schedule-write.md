# Rain Bird — écriture du planning (commande SIP `0x21` / SetSchedule)

Documentation du protocole local permettant de **modifier un programme**
(jours, heures de départ, durées par zone) sur un contrôleur Rain Bird de type
ESP-ME / ESP-TM2, via l'API locale `http://<ip>/stick`.

> **Statut : documentation seulement.** Rien de ceci n'est implémenté dans
> l'intégration `rainbird_advanced`. C'est une base de rétro-ingénierie pour une
> éventuelle implémentation future. **Une écriture mal formée peut corrompre les
> programmes existants** (seul recours : reprogrammer à la façade). Toujours
> relire (`0x20`) pour vérifier après écriture.

## Source

Rétro-ingénierie de l'application Android officielle **Rain Bird v2.17.14**
(APKPure), décompilée avec `jadx 1.5.6`. Fichiers de référence :

| Rôle | Fichier |
|---|---|
| Table des commandes | `com/rainbird/rainbirdlib/sipCommands/SIPCommandKeys.java` |
| Encodage (base ESP-ME) | `com/rainbird/rainbirdlib/controllerType/ESPMESIPProcessing.java` |
| Spécifique ESP-TM2 | `com/rainbird/rainbirdlib/controllerType/ESPTM2SIPProcessing.java` |
| Masque de jours | `com/rainbird/rainbirdlib/utilities/BitParsingUtility.java` |
| Fréquence | `com/rainbird/rainbirdlib/model/RBFrequency.java` |
| Heure de départ | `com/rainbird/rainbirdlib/model/RBStartTime.java` |

`pyrainbird` implémente déjà la **lecture** de ce planning (commande `0x20` →
réponse `A0`, `decode_schedule`). L'écriture est l'inverse : mêmes pages,
réécrites.

## Transport

Chaque commande ci-dessous est une **chaîne hexadécimale** (la « data ») qui est
ensuite chiffrée (AES) et envoyée par `tunnelSip`, exactement comme les commandes
de lecture que `pyrainbird` sait déjà émettre (`_process_command` / `_tunnelSip`).
Autrement dit : construire la bonne chaîne hex, puis la passer au même transport.

## Modèle : le planning est découpé en « pages »

Le planning n'est pas écrit d'un bloc mais **page par page**. Chaque page est une
commande `SET_SCHEDULE` (`0x21`) indépendante. La lecture utilise la même
pagination via `RETRIEVE_SCHEDULE` (`0x20`).

- **Lecture d'une page** : `createGetScheduleCommand(page)` →
  `"20" + "00" + %02X(page)`
- **Écriture d'une page** : `"21" + "00" + %02X(page) + <données>`
  (la page « infos globales » est un cas particulier, voir plus bas).

### Numérotation des pages

| Page | Numéro (octet) | Contenu |
|---|---|---|
| Infos globales | `0x00` | délai inter-zones, snooze, capteur pluie |
| Infos programme `p` | `p + 15` (0x0F+p) | fréquence / jours du programme `p` |
| Heures de départ programme `p` | `p + 95` (0x5F+p) | heures de départ du programme `p` |
| Durées (paire de zones) | `floor(zone / 2) + 128` (0x80+) | durées par zone et par programme |

Pour l'ESP-TM2 : **3 programmes** (`getMaxPrograms() = 3`, donc pages programme
15/16/17 et heures 95/96/97) et **6 pages de durées** (`getRunTimePages() = 6`,
soit 12 zones regroupées 2 par 2).

---

## Page « infos globales » (page 0)

Source : `generateGlobalInfoPage()`

```
"21" + "0000"
     + %04X(interStationDelay)      // délai entre zones, 2 octets
     + %02X(snooze)                 // 1 octet
     + %02X(rainSensor ? 0 : 1)     // 0 si capteur pluie activé, 1 sinon
```

Note : ici le préfixe est `"21" + "0000"` (et non `"00" + pageByte`), ce qui
revient à la page `0x00`.

---

## Page « infos programme » (jours / fréquence) — page `p + 15`

Source : `generateProgramInfoPage(p)`

```
"21" + "00" + %02X(p + 15)
     + %02X(masque_jours)           // voir "Masque de jours"
     + %02X(cyclicDays)             // N pour "tous les N jours" (fréquence cyclique)
     + %02X(daysRemaining)          // jours restants avant le prochain arrosage
     + %02X(permanentDaysOff)       // jours OFF permanents
     + %02X(100)                    // constante 0x64 (=100)
     + %02X(type)                   // type de fréquence, voir ci-dessous
```

### Type de fréquence (dernier octet)

| Valeur | Type | Champs pertinents |
|---|---|---|
| `0` | CUSTOM (jours de semaine choisis) | masque_jours |
| `1` | CYCLIC (tous les N jours) | cyclicDays, daysRemaining |
| `2` | ODD (jours impairs du mois) | — |
| `3` | EVEN (jours pairs du mois) | — |

(D'après `RBFrequency.FrequencyType` et le mapping dans `generateProgramInfoPage`.)

### Masque de jours

Source : `BitParsingUtility.booleanArrayToHexString(boolean[])`

```java
i = 0
pour chaque jour j (index 0..6) : i |= (jour[j] ? 1 : 0) << j
retour %02X(i)          // 1 octet
```

L'ordre des jours suit `customDays[0..6]`. D'après `RBFrequency`
(`getCustomDays()[iH != 7 ? iH : 0]`, indices calendrier), **l'index 0 =
dimanche … index 6 = samedi** — identique à l'énumération `DayOfWeek` de
pyrainbird (`SUNDAY = 0`). Donc bit 0 = dimanche, bit 1 = lundi, …, bit 6 =
samedi.

Exemple : lundi + mercredi + vendredi = bits 1,3,5 = `0b0101010` = `0x2A`.

---

## Page « heures de départ » — page `p + 95`

Source : `generateProgramStartTimePage(p)`

```
"21" + "00" + %02X(p + 95)
     + pour chaque heure de départ : %04X(minutesFromMidnight)
```

- Chaque heure = **minutes depuis minuit**, sur **2 octets** (big-endian).
  Ex. 06:30 = 390 = `0x0186`. (`RBStartTime.minutesFromMidnight`, `getHour =
  /60`, `getMinute = %60`.)
- ⚠️ **À vérifier sur le matériel** : le nombre exact d'emplacements d'heures par
  programme et la valeur de bourrage des emplacements inutilisés (probablement
  `0xFFFF`). L'app n'itère que sur les heures définies ; le contrôleur attend
  vraisemblablement un nombre fixe d'emplacements. À recouper avec `decode_schedule`
  de pyrainbird (côté lecture).

---

## Page « durées par zone » — page `floor(zone / 2) + 128`

Sources : `generateProgramRunTimePage(int[])` et surtout, pour l'ESP-TM2,
`generateTM2ProgramRunTimePage(int[])` + `addToTM2CommandString(station)`.

Principe (fiable) :

```
"21" + "00" + %02X(floor(zone / 2) + 128)
     + <données zone impaire de la paire>
     + <données zone paire de la paire>
```

où, pour l'ESP-TM2, les données d'une zone = pour **chaque programme**, la durée
sur 2 octets :

```java
// addToTM2CommandString(station) :
pour chaque programme :
    si la zone a un run-time dans ce programme : %04X(runTime)
    sinon : %04X(0)
```

Donc, ESP-TM2 (3 programmes) : chaque zone = 3 × 2 = **6 octets** ; deux zones
par page ; 12 zones ⇒ 6 pages (cohérent avec `getRunTimePages() = 6`).

- La durée `runTime` est très probablement en **secondes** (à confirmer via la
  lecture pyrainbird, qui expose des `timedelta`).
- ⚠️ **Les deux méthodes `generateProgramRunTimePage` / `generateTM2ProgramRunTimePage`
  n'ont PAS été décompilées proprement** (jadx signale « Code duplicated /
  Instruction removed », et des conditions absurdes du type
  `station == null && station.isEnabled()`). **Le détail exact
  (ordre des deux zones dans la paire, gestion des zones désactivées, bourrage)
  doit être revérifié en smali** (`baksmali`) avant toute implémentation.
- `generateStationDisablePage(...)` gère l'activation/désactivation de zones sur
  la même structure de page (0x80+).

---

## Séquence d'écriture recommandée (pour une future implémentation)

1. **Lire** tout le planning (`0x20`, pages 0, 15..17, 95..97, 128.. ) et le
   décoder (réutiliser la logique `decode_schedule` de pyrainbird).
2. **Modifier** en mémoire uniquement la page concernée (ex. les jours du
   programme A, ou les durées d'une zone).
3. **Réécrire** cette page (et seulement elle) via `0x21`.
4. **Relire** la même page et vérifier qu'elle correspond à ce qui a été envoyé.
   En cas d'écart, ne pas insister.

## Points à vérifier avant d'implémenter

1. Format exact des pages de **durées** (décompilation garbled → passer en smali).
2. Nombre d'emplacements d'**heures de départ** et bourrage des inutilisés.
3. **Unité** des durées (secondes vs minutes) et des heures (confirmer minutes).
4. Réponse attendue du contrôleur à un `0x21` (ACK ? code ?) pour valider l'envoi.
5. Comportement si on n'écrit qu'une page sur plusieurs (cohérence interne du
   planning côté contrôleur).

## Table des commandes utiles (extrait de `SIPCommandKeys`)

| Nom | Code | Sens |
|---|---|---|
| RetrieveSchedule | `20` | lire une page de planning |
| RetrieveScheduleResponse | `A0` | réponse de lecture |
| **SetSchedule** | `21` | **écrire une page de planning** |
| SetWaterBudget | `31` | écrire le water budget |
| SetZonesSeasonalAdjustFactor | `33` | écrire l'ajustement saisonnier |
| SetCurrentTime / Date | `11` / `13` | régler l'heure / la date |
