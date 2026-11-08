# **Projekt-Analyse: Catlord / Hellcat Project**

Die bisherigen Ausarbeitungen zeigen ein tiefgründiges, vielschichtiges Projekt, das humorvolle Katzen-Taktik mit einer düsteren, psychologischen Erzählung verbindet. Die Dokumente spiegeln zwei miteinander verflochtene Stränge wider: Die narrative Struktur um "Attic Guy" und den psychotischen Gert Baert sowie die spielmechanische Ebene des "Hellcat Projects", bei dem echte Katzen zu Assassinen ausgebildet werden, um ein Einfamilienhaus gegen die Mafia zu verteidigen.

---

### **1\. Geschichte & Lore**

> Die Erzählung basiert auf einem vertikalen Spannungsfeld innerhalb und außerhalb eines Einfamilienhauses. Es zeichnet sich durch eine düstere, humorvolle und detailreiche Atmosphäre aus.

> * **Die Kernkonstellation (Das Spannungsdreieck):**  
  * **Oben (Dachboden):** Ort von Sebastian ("Attic Guy"). Ein Raum der Analyse, des Fachwissens und der erzwungenen Ruhe.  
  * **Unten (Hof/Erdgeschoss):** Ort des Chaos, des Drogenwahns (Kokain-Psychose) und der physischen Zerstörung durch Gert Baert.  
  * **Dazwischen (Das Waschbecken):** Das Territorium von Kater Elliot, der als seismographischer Beobachter Erschütterungen von unten registriert.  
> * **Hintergrund & Der "Kiwi-Schnitt":**  
  * Sebastian arbeitet seit acht Jahren mit Gert im Gartenbau zusammen (Schwarzarbeit) und kennt ihn seit 30 Jahren. Seit mehreren Jahren versucht er in einer aktiven, emotional zehrenden Rettungsphase, Gerts Business und Psyche zu stabilisieren.  
  * Das zentrale Trauma und das Mahnmal für Gerts Realitätsverlust ist der sogenannte "Kiwi-Schnitt". Gert behauptet, er sei beim Schneiden einer Kiwi-Pflanze unglücklich aus dem ersten Stock gefallen. Die bittere Wahrheit ist jedoch, dass Gert im Wahn gesprungen ist. Jedes Mal, wenn Gert diese Universalausrede nutzt, wird Sebastian an dessen totalen Realitätsverlust erinnert.  
> * **Narrative Ebenen:**  
  * *Fachliche Ebene:* Der Gartenbau dient als Metapher für den Zerfall. Gert plant Unmögliches, während Sebastian das Scheitern voraussieht.  
  * *Kriminelle Ebene:* Durch erhebliche Schulden bei Gangstern zieht Gert eine ständige Bedrohung in Sebastians Lebensraum.  
  * *Psychologische Ebene:* Ein innerer Konflikt zwischen Sebastians Loyalität aufgrund der gemeinsamen Vergangenheit und dem Drang zur Selbsterhaltung.  
> * **Szenen-Ideen & Dialoge:**  
  * *Mülltonnen-Crash:* Gert kracht im Drogenwahn viel zu schnell und tollpatschig mit seinem Auto in die betonierte Mülltonnen-Nische. Er flucht und versucht aus dem Wagen zu klettern, während Sebastian ihn vom Dachfenster aus beobachtet und Elliot unten im Waschbecken lauert.  
  * *Die Drohung:* Bei einer verbalen Auseinandersetzung, bei der Gert versucht, Geld für Kaffee zu leihen, ruft Sebastian vom Dachfenster: „Laber nicht oder ich schmeiß dir gleich die Ninja Karter runter\!\!“ Gert lacht und erwidert: „Ninja Katzen? Die zwei blöden Viecher? Mach halt dann streichel ich sie zu Tode\!“  
  * *Verwechslung:* Der pubertierende Sohn der Hausbesitzerin verschafft sich heimlich Zutritt zum Haus, um mit Freunden sturmfrei zu nutzen und Netflix zu schauen. Sebastian erschreckt sich und verwechselt die Jugendlichen im Dunkeln mit den Gangstern der Mafia.  
> * **Ren'Py-Skriptfragmente (Code-Bausteine aus pie):**

>   Die Skripte definieren die Charaktere farblich und tracken Variablen wie Gerts Zustand, das Korruptionslevel von Sebastian oder die Jahre des Rettungsversuchs. Ein funktionales Kernfragment lautet:

>   `define el = Character("Elliot", color="#ff4500")`  
>   `define lo = Character("Loco", color="#00ced1")`  
>   `define g = Character("Gert", color="#27ae60")`  
>   `define ag = Character("Attic Guy", color="#2c3e50")`  
>   `define eslow = Character(kind=el, what_slow_cps=20) # Fix für Textgeschwindigkeit`

>   `label start:`  
>       `$ jahre_rettung = "mehrere Jahre"`  
>       `$ gert_status = "Zentrum der Macht"`  
>       `$ kiwi_wahrheit = "Sprung statt Fall"`  
>       `$ korruption_level = 75`  
>         
>       `play music "audio/main_theme.ogg" fadein 2.0`  
>       `scene bg badezimmer_anime with fade`  
>         
>       `ag "Seit [jahre_rettung] versuche ich Gert zu retten. Er faselt vom Kiwi-Sturz, aber ich kenne die Wahrheit."`

### ---

**2\. Charaktere**

Die Charaktere sind scharf gezeichnet und teilen sich in menschliche Akteure und die zu Assassinen trainierten Maine-Coon-Kater auf.

#### **Menschliche Charaktere**

> * **Sebastian / Attic Guy (Protagonist):** Wohnt unterm Dach, ist ein entspannter Typ (raucht gerne mal einen). Er verfügt über das echte handwerkliche Fachwissen, bügelt Gerts Fehler aus und fungiert als Schutzschild gegen Gläubiger. Er ist durch die Lügen und den Pfusch, den er für Gert decken muss, moralisch korrumpiert.  
> * **Gert Baert ("Zentrum der Macht"):** Ein verpeilter, extrem tollpatschiger Ex-Junkie, der in einer tiefen Kokain-Psychose lebt und sich am Telefon standardmäßig mit „Zentrum der Macht?“ meldet. Er leugnet seine massiven Schulden komplett ab und sieht sich fälschlicherweise als genialer Chef.  
> * **Robert:** Ein Kollege und Mitwisser der Baustellen-Katastrophen, der für spätere Kapitel als Charakter vorgemerkt ist.  
> * **Hausbesitzerin & Sohn:** Die Vermieterin des Hauses. Ihr in der Pubertät befindlicher Sohn hilft Sebastian heimlich oder unbewusst beim Training der Katzen.  
> * **Die Mafia / Gangster:** Bedrohliche "Kunden" und Gläubiger im Hintergrund, die versuchen, in das Haus einzudringen.

#### **Die Katzen-Assassinen**

> * **Elliot (Der Tank):**  
  * *Physis:* Ein massiver, kräftiger, orange getigerter Maine-Coon-Kater mit einem Gewicht von 8 kg. Er besitzt "Krallen wie Wolverine".  
  * *Rolle:* Defensiver Ninja-Tank mit massiver Präsenz und Standhaftigkeit. Er agiert als taktischer Stratege, der Schwachstellen analysiert.  
  * *Fähigkeiten:* "Arsch-Anschlag" (blockiert Türen physisch mit seinem Körper) und "Waschbecken-Slam" (eine offensive Attacke aus der Deckung des Waschbeckens heraus).  
  * *Macken:* Trotz seiner Kraft ist er extrem lärmempfindlich („Schisser“) und reagiert auf lauten Krach mit Desorientierung. Er besitzt eine absolute Obsession für Waschbecken.  
> * **Loco / Loki (Der Assassin):**  
  * *Physis:* Ein 9 Monate alter, großer, langhaariger, grau-weißer Maine Coon.  
  * *Rolle:* Agiler, extrem schneller Ninja-Jäger für die lautlose Hausverteidigung. Er ist maximal wurftauglich.  
  * *Fähigkeiten:* "Tail-Shade & Toss" – nutzt seinen buschigen Schwanz zur optischen Tarnung/Unsichtbarkeit und lässt sich dann präzise von Attic Guy auf Gegner werfen.  
> * **Cash:** Ein weiterer Kater aus der Rasselbande, dessen wichtigste Fähigkeit der "Kulleraugen-Boost" ist, um Gegner abzulenken oder zu manipulieren.  
> * **Holly:** Eine erfahrene Ninja-Veteranin, die im Hintergrund die Truppe diszipliniert und strukturiert.

### ---

**3\. Design & Visueller Stil**

> Das Spiel nutzt ein markantes visuelles Konzept, das als **"Hardstyle"** definiert ist. Es bricht mit typischen Soft-Anime-Visuals und setzt auf einen urbanen, rauen Look.

> * **Ästhetik:** Ein Mix aus Comic und Anime, stark inspiriert vom urbanen Illustrationsstil der **GTA-Loading-Screens** sowie dem Stil von Patrick Brown und Anthony Macbain.  
> * **Kern-Elemente:**  
  * Dicke, markante, schwarze Außenlinien (*Heavy bold black outlines*).  
  * Harter Schattenwurf ohne weiche Verläufe (*Cel-Shading*).  
  * Hoher Kontrast und flächige, kontrastreiche Farben.  
> * **Bildgenerierungs-Strategie für ComfyUI & Krita:**  
  * Das Ziel ist das Training dedizierter LoRAs mit ComfyUI, um das exakte Aussehen der echten Kater (Elliot und Loco) im gewünschten Grafikstil konsistent festzuhalten.  
  * *Workflow-Parameter:* Verwendung des Img2Img-Modus (Image-to-Image) mit einer *Denoising Strength* von 0.65 bis 0.80. Zur Beibehaltung der Posen und Konturen wird *ControlNet* (entweder Canny oder Lineart) eingesetzt.  
> * **Exemplarische KI-Prompts:**  
  * *Für Loco:* „A high-stakes tactical illustration, hardstyle GTA art style. A majestic, large, long-haired grey and white Maine Coon cat (Loco) in a low, predatory ninja stance on a dark wooden staircase. He wears dark, matte-black shinobi gear and a tactical harness. Alert eyes, focused on defense. High-contrast, bold black outlines, sharp cel-shaded shadows, no gradients. Art by Anthony Macbain.“  
  * *Für Elliot:* „A powerful character illustration, hardstyle GTA art style. A massive, muscular 8kg orange tabby Maine Coon cat (Elliot) as a Ninja Tank. Standing in a strong, defensive guarding pose on a wooden floor. He wears traditional dark Japanese shinobi armor with a thick protective chest plate. High-contrast, thick bold black outlines, sharp cel-shaded shadows, no gradients. Professional Ninja Business look. Art by Anthony Macbain.“

### ---

**4\. Levelaufbau, Schauplätze & Gameplay**

> Das Gameplay kombiniert klassische Visual-Roman-Elemente mit strategischen Ressourcen-Management- und Verteidigungs-Mechaniken.

> * **Engine-Wahl:** Das Projekt nutzt primär **Ren'Py** für die Dialogstrukturen, Story-Verzweigungen und die Menüführung. **Godot** ist für die aktiveren Gameplay-Elemente oder alternative Prototypen (wie das Bowling-Projekt) parallel im Einsatz, während Skripte in **Python** geschrieben werden.  
> * **Zentrale Schauplätze:**  
  * *Das Einfamilienhaus:* Das primäre Level, das gegen die Mafia verteidigt werden muss.  
  * *Der Dachboden:* Ruheraum und Einsatzzentrale von Sebastian.  
  * *Die Mülltonnen-Nische / Der Hof:* Der Außenbereich, an dem das mechanische und fahrerische Versagen von Gert stattfindet.  
  * *Das Badezimmer / Die Waschbecken:* Strategische Kampfzonen.  
> * **Gameplay-Mechaniken:**  
  * *Waschbecken-Taktik (Porzellan-Festung):* Waschbecken dienen als *Ambush-Spots* (Hinterhalt-Zonen). Da Mafiosi eitel sind, versuchen sie oft, sich die Hände zu waschen. Elliot tarnt sich im weißen Becken (*Camouflage*). Dreht ein Gegner den Hahn auf, nutzt Elliot den Wasserschwall als Startrampe, springt dem Gegner ins Gesicht und verpasst ihm den Statuseffekt „Eingeseift“ (kurzzeitig blind).  
  * *Co-Op-Regeneration (Flausch-Stacking / Kuschel-Mechanik):* Außerhalb von Kämpfen kann in *Safe-Zones* (z. B. auf dem Bett) Energie regeneriert werden. Liegen zwei Katzen zusammen auf einem Möbelstück, lädt sich die Energie doppelt so schnell auf (essentiell nach einem "Haarball-Stun"). *Risiko:* Katzen hören Autos bereits auf dem Grundstück und wechseln in eine Vorbereitungshaltung. Da Elliot lärmempfindlich ist, kann lauter Krach den Schlafmodus unterbrechen und beide Katzen aufschrecken.  
  * *Das Konter-System der Gangster:* Gegner können angreifende Katzen am Bürzel (Schwanz) abfangen, wie „Obelix die Römer“ im Kreis schleudern und zurück zu Sebastian werfen.  
  * *Globale Handicaps:* Das „Langhaar-Handicap“ kann bei den Maine Coons zu einem unvorhersehbaren *Haarball-Stun* führen, wodurch der betroffene Charakter eine Runde aussetzen muss.

### ---

**5\. Technische Umsetzung & Offene To-Dos**

> Das technische Fundament steht, verlangt jedoch in der Integration der verschiedenen Tools noch nach Feinschliff.

> * **Verwendeter Tool-Stack:**  
  * *Programmierung:* Python & Ren'Py (inklusive des pie-Frameworks) für die Logik, Godot und VSCode Studio als IDE.  
  * *Grafik & Design:* ComfyUI (LoRA-Training, automatisierte Bilderstellung, Prompts) und Krita (Img2Img-Ausarbeitung) sowie Blender für 3D-Modelle.  
> * **Bestehende Code-Logik (Python in Ren'Py):**

>   Es existieren bereits funktionierende Funktionen für das Ressourcenmanagement im Spielverlauf:  
  * check\_health(): Überprüft die HP des Spielers. Sinkt dieser Wert auf oder unter 20 %, erfolgt eine automatische Heilung auf 100 % via Leck-Trigger inklusive einer Bildschirm-Benachrichtigung.  
  * use\_potion(): Regelt die Einnahme von Heiltränken. Ist die HP über 50 %, heilt der Trank vollständig. Bei kritisch niedrigen HP (unter 50 %) ist die Einnahme riskant: Die Heilung schlägt fehl und triggert den hairball\_malus, der den Spieler eine Runde blockiert.  
  * *Hauptschleife (hauptschleife):* Regelt das Aktionsmenü (Katzen trainieren zur Steigerung von el\_power oder lo\_watch, Pause machen für schrittweise HP-Regeneration, Trank nutzen oder Story-Events mit Gert starten).  
> * **Offene Baustellen & Nächste Schritte:**  
  * **Layla-App / Inference-Setup:** Das Sprachmodell für den "Catlord Autor" muss exakt nach dem Konfigurations-Leitfaden eingestellt werden. Der *System Prompt* (Prosa-Zwang, Gert als Vollidiot, Elliot als einzige Katze im Waschbecken) sowie die Regler (Temperature: 0.75, Top-P: 0.9, Frequency Penalty: 1.1, Context Length: 4096\) müssen final in die App übertragen werden.  
  * **GUI für Bildbewertung in ComfyUI:** Die automatisierte Bilderstellung läuft, aber es fehlt eine komfortable grafische Benutzeroberfläche (GUI), um erstellte Bilder aus /mnt/storage-beta/... schnell zu bewerten, gute Bilder in den Projektordner zu verschieben und schlechte direkt zu löschen.  
  * **Asset-Integration im Skript:** Im Ren'Py-Code sind die visuellen Darstellungen der Katzen derzeit noch als reine Platzhalter hinterlegt (\# show elliot\_jojo at center, \# show loco\_vordergrund at left). Sobald die LoRAs in ComfyUI trainiert und die finalen Charakter-Bilder generiert wurden, müssen diese Platzhalter durch die echten Bild-Assets ersetzt werden.  
  * **Audio-Dateien:** Es muss sichergestellt werden, dass die im Skript aufgerufene Audiodatei audio/main\_theme.ogg im korrekten Verzeichnis existiert, damit das Audio-Setup fehlerfrei lädt.

Möchtest du, dass ich für dich das bestehende Ren'Py-Skript um die neuen Variablen wie threat\_level oder die Zustände für die Waschbecken-Imagebuttons (hidden, ambush, cuddle) erweitere?

Quellen:

> * [Catlord .pdf](https://drive.google.com/file/d/18Wx5OmEvB9t4-A4jjoF6t7LIMkr_3sKm/view?usp=drive_web)  
> * [Hellcat Project \- Game Design Dokument V1.pdf](https://drive.google.com/file/d/1lRhn7PHTkRgyytXyalo0H0Hn71CwOdKs/view?usp=drive_web)  
> * [Projekt\_Attic\_Guy\_Charaktere\_V1.pdf](https://drive.google.com/file/d/1BqYdvxKoHFrj_oAOeot5jHFQcTIGdDpo/view?usp=drive_web)  
> * [Projekt\_Attic\_Guy\_Konstruktur\_V2.pdf](https://drive.google.com/file/d/1IAEBW7J9gRqozFdguDO0sjHBmRc9wWyf/view?usp=drive_web)  
> * [Catlord Ideen für Konter.pdf](https://drive.google.com/file/d/1KbhlYCMJ2N6leKlPLVgv-6XrDzAwJXmN/view?usp=drive_web)  
> * [Anleitung\_Catlord\_Setup\_Sebastian.pdf](https://drive.google.com/file/d/1gBQr2IB9Bczhw2UajE236uwkWCMhD-Dr/view?usp=drive_web)  
> * [Projekt\_Attic\_Guy\_Konstruktur\_V5\_Ewige\_Rettung.pdf](https://drive.google.com/file/d/1lPJZdvDpvTogSC06a7G1WmD3btuYOCVp/view?usp=drive_web)  
> * [Projekt\_Attic\_Guy\_Konstruktur\_V4\_Kiwi\_Wahrheit.pdf](https://drive.google.com/file/d/1HWNNzFvOHk9Tx26EtTOx-KzEWXHrJ0T5/view?usp=drive_web)  
> * [Catlord KI Futter .pdf](https://drive.google.com/file/d/1lhl6DodqXUF0iY061TcC6DXLpSIp1RCM/view?usp=drive_web)  
> * [Script Idee .pdf](https://drive.google.com/file/d/1k1kmNsB67geFbCwnebCNND1BlaenC9jr/view?usp=drive_web)  
> * [Catlord\_v1.1111.pdf](https://drive.google.com/file/d/1AfgwIXriE2mWu5u9pdDy6l9ff0Lt8i8G/view?usp=drive_web)  
> * [Projekt\_Attic\_Guy\_Konstruktur\_V3\_Gesamt.pdf](https://drive.google.com/file/d/12z_uS5wu1GQQT6HPYUCL-PUrY3te7mdA/view?usp=drive_web)  
> * [Projekt übersicht Basti](https://docs.google.com/document/d/14P78DSaeXI9-JgfIrrcQZCJAPZIxntIAnyYilEPLEWQ/edit?usp=drive_web)  
> * [Ninja-Katzen Projekt: Prompts und Stilvorgaben.pdf](https://drive.google.com/file/d/1weBvrmPvWKJKANptFe8UlVsD3HLYIHCF/view?usp=drive_web)