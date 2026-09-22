/**
 * appearance_presets.js - Appearance Presets & RPG Tag Generator Controller
 * Outer Heaven Mission Control Dashboard
 *
 * Requirements:
 *   - Saubere 2-Spalten-Trennung im Modal:
 *       * Links: Visuelle ComfyUI-Tags (Körperbau, isolierte Augen, Haare nach Farbe & Schnitt getrennt, Tattoos/Piercings)
 *       * Rechts: Narrative Lore / System-Prompt (wie der Bot über sich spricht)
 *   - RPG-Dropdowns (Geschlecht, Alter, Größe, Haarfarbe, Frisur, Augenfarbe, Statur, Vibe) als solide Auswahllisten
 *   - Live-Kopplung der Auswahl-Events mit dem linken Generator (sofortige Generierung sauberer ComfyUI/Danbooru-Tags)
 *   - Button [ ✨ Aus Merkmalen generieren ] & Auto-Sync für deutsche Chat-Lore
 *   - Synchronisation mit character.json, bot_config.json und Service-Reload
 */

(() => {
  let loadedPresets = [];
  let currentPresetId = '';

  const RPG_MAP = {
    gender: {
      'Weiblich': '1girl, solo',
      'Männlich': '1boy, solo',
      'Shemale': '1shemale, futanari, solo',
      'Transgender': 'transgender, solo'
    },
    age: {
      '18': '18yo',
      '20': '20yo',
      '22': '22yo',
      '24': '24yo',
      '26': '26yo',
      '28': '28yo',
      '30+': 'mature female'
    }
  };

  function getAppearanceModal() {
    return document.getElementById('appearanceModal') || document.getElementById('appearancePresetModal');
  }

  window.openAppearanceModal = function() {
    const modal = getAppearanceModal();
    if (modal) {
      modal.classList.remove('hidden');
      modal.style.display = 'flex';
      loadAppearancePresets();
    }
  };

  window.closeAppearanceModal = function() {
    const modal = getAppearanceModal();
    if (modal) {
      modal.classList.add('hidden');
      modal.style.display = 'none';
    }
  };

  window.loadAppearancePresets = async function() {
    const sel = document.getElementById('appearancePresetSelect');
    const badge = document.getElementById('appearanceActiveBadge');
    if (sel) sel.innerHTML = '<option value="">Lade Profile...</option>';

    try {
      const res = await fetch('/api/bot/appearance/presets');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      if (!data.success) return;

      loadedPresets = data.presets || [];
      const activeId = data.active_id || '';
      currentPresetId = activeId;

      if (typeof window.syncDashboardAppearanceOptions === 'function') {
        window.syncDashboardAppearanceOptions(loadedPresets);
      }

      if (sel) {
        sel.innerHTML = '';
        loadedPresets.forEach(p => {
          const opt = document.createElement('option');
          opt.value = p.id;
          opt.textContent = `${p.name || p.id}${p.is_active ? ' (Aktiv)' : ''}`;
          if (p.id === activeId || p.is_active) opt.selected = true;
          sel.appendChild(opt);
        });
      }

      const activePreset = loadedPresets.find(p => p.id === activeId) || loadedPresets[0];
      if (activePreset) {
        populatePresetForm(activePreset);
      }
    } catch (e) {
      console.warn('Fehler beim Laden der Aussehen-Presets:', e);
      if (sel) sel.innerHTML = '<option value="">Fehler beim Laden</option>';
    }
  };

  window.onAppearancePresetSelectChange = function(presetId) {
    const p = loadedPresets.find(item => item.id === presetId);
    if (p) {
      currentPresetId = p.id;
      populatePresetForm(p);
    }
  };

  function selectOptionByFuzzy(selectId, textOrVal) {
    const sel = document.getElementById(selectId);
    if (!sel || !textOrVal) return;
    const target = String(textOrVal).toLowerCase().trim();
    for (let i = 0; i < sel.options.length; i++) {
      const optVal = sel.options[i].value.toLowerCase();
      const optText = sel.options[i].textContent.toLowerCase();
      if (optVal === target || optText === target || optVal.includes(target) || target.includes(optVal)) {
        sel.selectedIndex = i;
        return;
      }
    }
  }

  function populatePresetForm(p) {
    currentPresetId = p.id;

    // Header metadata
    setVal('appPresetId', p.id || '');
    setVal('appPresetName', p.name || '');
    setVal('appPresetDesc', p.description || '');

    // 1. RPG Dropdowns
    const gender = p.gender || (p.rpg && p.rpg.gender) || 'Weiblich';
    selectOptionByFuzzy('appRpgGender', gender);

    const age = p.age || (p.rpg && p.rpg.age) || '24';
    selectOptionByFuzzy('appRpgAge', age);

    const height = p.prompt_height || (p.rpg && p.rpg.height) || '178cm tall';
    selectOptionByFuzzy('appPresetHeight', height);

    const vis = p.visual || {};
    const hairCol = vis.hair_color || 'dark brown hair';
    selectOptionByFuzzy('appPresetHairColor', hairCol);

    const hairCut = vis.hair_style || vis.hair || 'long layered hair, soft natural waves';
    selectOptionByFuzzy('appRpgHairStyle', hairCut);
    setVal('appPresetHairCut', hairCut);

    const eyeCol = vis.eye_color || 'green-blue eyes';
    selectOptionByFuzzy('appPresetEyeColor', eyeCol);

    const eyeShape = vis.eye_shape || vis.eyes || 'seductive playful smile, alluring captivating gaze';
    selectOptionByFuzzy('appRpgVibe', eyeShape);
    setVal('appPresetEyeShape', eyeShape);

    const body = vis.body || '1girl, solo, 178cm tall, slender athletic build, voluptuous hourglass figure, very large full natural breasts, flat toned stomach';
    selectOptionByFuzzy('appRpgBodyType', body);
    setVal('appPresetBody', body);

    setVal('appPresetFeatures', vis.features || 'massive intricate sleeve tattoo left arm, silver navel piercing');

    const badge = document.getElementById('appearanceActiveBadge');
    if (badge) {
      if (p.is_active) {
        badge.classList.remove('hidden');
      } else {
        badge.classList.add('hidden');
      }
    }

    // Right Column: Narrative Lore
    const lore = p.physical_chat_lore || {};
    setVal('appLoreSelbstbild', lore.selbstbild || lore.statur || '');
    setVal('appLoreIntim', lore.intim || lore.oberweite || '');
    setVal('appLoreGestik', lore.gestik || lore.gesicht || '');
    setVal('appLoreMarkant', lore.markantes_merkmal || lore.markant || '');

    // Fallbacks for legacy field IDs
    setVal('appLoreStatur', lore.statur || lore.selbstbild || '');
    setVal('appLoreOberweite', lore.oberweite || lore.intim || '');
    setVal('appLoreGesicht', lore.gesicht || lore.gestik || '');
    setVal('appLoreHaare', lore.haare || '');
    setVal('appLoreKoerper', lore.koerperliche_merkmale || '');

    updateCombinedVisualPreview();
    updateLorePreview();
  }

  function setVal(id, val) {
    const el = document.getElementById(id);
    if (el) el.value = val;
  }

  function getVal(id) {
    const el = document.getElementById(id);
    return el ? el.value.trim() : '';
  }

  /**
   * Live RPG Tag Generator:
   * Called when any RPG dropdown changes.
   * Generates clean Danbooru/ComfyUI tags for the left column.
   */
  window.onRpgDropdownChange = function() {
    const genderSel = document.getElementById('appRpgGender');
    const ageSel = document.getElementById('appRpgAge');
    const heightSel = document.getElementById('appPresetHeight');
    const hairColSel = document.getElementById('appPresetHairColor');
    const hairCutSel = document.getElementById('appRpgHairStyle');
    const eyeColSel = document.getElementById('appPresetEyeColor');
    const bodySel = document.getElementById('appRpgBodyType');
    const vibeSel = document.getElementById('appRpgVibe');

    const genderVal = genderSel ? genderSel.value : 'Weiblich';
    const ageVal = ageSel ? ageSel.value : '24';
    const heightVal = heightSel ? heightSel.value : '178cm tall';
    const hairColVal = hairColSel ? hairColSel.value : 'dark brown hair';
    const hairCutVal = hairCutSel ? hairCutSel.value : 'long layered hair, soft natural waves';
    const eyeColVal = eyeColSel ? eyeColSel.value : 'green-blue eyes';
    const bodyVal = bodySel ? bodySel.value : 'slender athletic build, flat toned stomach';
    const vibeVal = vibeSel ? vibeSel.value : 'seductive playful smile, alluring captivating gaze';

    // 1. Synthesize Body Tags
    const genderTag = RPG_MAP.gender[genderVal] || '1girl, solo';
    const ageTag = RPG_MAP.age[ageVal] || `${ageVal}yo`;

    const bodyParts = [genderTag, ageTag, heightVal, bodyVal].filter(Boolean);
    const fullBodyTag = bodyParts.join(', ');
    setVal('appPresetBody', fullBodyTag);

    // 2. Set individual inputs
    setVal('appPresetHairCut', hairCutVal);
    setVal('appPresetEyeShape', vibeVal);

    // 3. Update preview
    updateCombinedVisualPreview();

    // 4. Update narrative lore
    window.generateLoreFromVisualTags();
  };

  window.updateCombinedVisualPreview = function() {
    const body = getVal('appPresetBody');
    const eyeCol = getVal('appPresetEyeColor');
    const eyeShape = getVal('appPresetEyeShape');
    const hairCol = getVal('appPresetHairColor');
    const hairCut = getVal('appPresetHairCut');
    const feat = getVal('appPresetFeatures');

    const parts = [];
    if (body) parts.push(body);
    if (eyeCol || eyeShape) parts.push([eyeCol, eyeShape].filter(Boolean).join(', '));
    if (hairCol || hairCut) parts.push([hairCol, hairCut].filter(Boolean).join(', '));
    if (feat) parts.push(feat);

    const comb = parts.filter(Boolean).join(', ');
    const previewEl = document.getElementById('appPresetCombinedPreview');
    if (previewEl) {
      previewEl.textContent = comb || '1girl, solo, ... (Wird automatisch aus den Feldern zusammengestellt)';
    }
    return comb;
  };

  window.updateLorePreview = function() {
    const selbstbild = getVal('appLoreSelbstbild') || getVal('appLoreStatur');
    const intim = getVal('appLoreIntim') || getVal('appLoreOberweite');
    const gestik = getVal('appLoreGestik') || getVal('appLoreGesicht');
    const markant = getVal('appLoreMarkant');

    const previewEl = document.getElementById('appLoreSystemPromptPreview');
    if (previewEl) {
      previewEl.textContent =
`### PHYSISCHE PRÄSENZ & EROTIK (AUS DEM PROFIL)
* **Selbstbild & Physis:** ${selbstbild || '--'}
* **Intime Details & Dekolleté:** ${intim || '--'}
* **Gestik & Blick:** ${gestik || '--'}
* **Signatur-Merkmale:** ${markant || '--'}`;
    }
  };

  /**
   * [ ✨ Aus Merkmalen generieren ]
   * Translates visual ComfyUI tags into natural German narrative lore.
   */
  window.generateLoreFromVisualTags = function() {
    const genderSel = document.getElementById('appRpgGender');
    const ageSel = document.getElementById('appRpgAge');
    const heightSel = document.getElementById('appPresetHeight');
    const hairColSel = document.getElementById('appPresetHairColor');
    const hairCutSel = document.getElementById('appRpgHairStyle');
    const eyeColSel = document.getElementById('appPresetEyeColor');
    const bodySel = document.getElementById('appRpgBodyType');
    const vibeSel = document.getElementById('appRpgVibe');

    const gender = genderSel ? genderSel.value : 'Weiblich';
    const age = ageSel ? ageSel.value : '24';
    const heightText = heightSel ? heightSel.options[heightSel.selectedIndex]?.text || heightSel.value : '178 cm';
    const hairColText = hairColSel ? hairColSel.options[hairColSel.selectedIndex]?.text || hairColSel.value : 'Dunkelbraun';
    const hairCutText = hairCutSel ? hairCutSel.options[hairCutSel.selectedIndex]?.text || hairCutSel.value : 'Lange Stufen';
    const eyeColText = eyeColSel ? eyeColSel.options[eyeColSel.selectedIndex]?.text || eyeColSel.value : 'Grün-Blau';
    const bodyText = bodySel ? bodySel.options[bodySel.selectedIndex]?.text || bodySel.value : 'Schlank & Athletisch';
    const vibeText = vibeSel ? vibeSel.options[vibeSel.selectedIndex]?.text || vibeSel.value : 'Verspielt & Verführerisch';
    const features = getVal('appPresetFeatures');

    // 1. Selbstbild & Physis
    const cleanHeight = heightText.split('(')[0].trim();
    const selbstbild = `${cleanHeight} groß, ${age} Jahre alt, ${gender}. ${bodyText} mit femininer, anziehender Ausstrahlung und samtig weicher Haut.`;
    setVal('appLoreSelbstbild', selbstbild);
    setVal('appLoreStatur', selbstbild);

    // 2. Intime Details & Dekolleté
    let intimText = 'Wohlgeformte, straffe Brüste mit aufregendem Dekolleté, schmale Taille und knackig definierter Hintern.';
    if (bodyText.includes('Vollbusig') || bodyText.includes('Sanduhr') || bodyText.includes('Kurvig')) {
      intimText = 'Sehr große, pralle natürliche Brüste mit tiefem, atemberaubendem Dekolleté, schmale Wespentaille und sinnlich geschwungene Hüften.';
    } else if (bodyText.includes('Zierlich')) {
      intimText = 'Zarte, feste kleine Brüste mit empfindlichen Knospen, super flacher Bauch und straffer, kompakter Körper.';
    } else if (bodyText.includes('Muskulös')) {
      intimText = 'Deutlich sichtbare, straffe Bauchmuskeln, feste runde Brüste, athletischer Po und hohe Muskelspannung.';
    }
    setVal('appLoreIntim', intimText);
    setVal('appLoreOberweite', intimText);

    // 3. Gestik, Ausdruck & Haare
    const gestikText = `Faszinierende ${eyeColText}e Augen mit einem Ausdruck von: ${vibeText}. Glänzende ${hairColText}e Haare (${hairCutText}) und verführerisch geschwungene Lippen.`;
    setVal('appLoreGestik', gestikText);
    setVal('appLoreGesicht', gestikText);
    setVal('appLoreHaare', `${hairColText} (${hairCutText})`);

    // 4. Signatur-Merkmale
    let markantText = features;
    if (!markantText || markantText.includes('sleeve')) {
      markantText = 'Massives, kunstvolles Sleeve-Tattoo auf dem linken Arm sowie ein dezentes silbernes Bauchnabel-Piercing.';
    }
    setVal('appLoreMarkant', markantText);

    updateLorePreview();

    // Visual feedback animation
    const rightCol = document.getElementById('appearanceModalRightCol');
    if (rightCol) {
      rightCol.classList.add('ring-2', 'ring-pink-500');
      setTimeout(() => rightCol.classList.remove('ring-2', 'ring-pink-500'), 1000);
    }
  };

  window.initNewAppearancePreset = function() {
    currentPresetId = '';
    setVal('appPresetId', 'megan_custom_' + Math.floor(Math.random() * 1000));
    setVal('appPresetName', 'Megan (Neues Profil)');
    setVal('appPresetDesc', 'Individuelles Aussehen');

    selectOptionByFuzzy('appRpgGender', 'Weiblich');
    selectOptionByFuzzy('appRpgAge', '24');
    selectOptionByFuzzy('appPresetHeight', '178cm tall');
    selectOptionByFuzzy('appPresetHairColor', 'dark brown hair');
    selectOptionByFuzzy('appRpgHairStyle', 'long layered hair, soft natural waves');
    selectOptionByFuzzy('appPresetEyeColor', 'green-blue eyes');
    selectOptionByFuzzy('appRpgBodyType', 'slender athletic build, flat toned stomach');
    selectOptionByFuzzy('appRpgVibe', 'seductive playful smile, alluring captivating gaze');
    setVal('appPresetFeatures', 'massive intricate sleeve tattoo left arm, silver navel piercing');

    window.onRpgDropdownChange();

    const badge = document.getElementById('appearanceActiveBadge');
    if (badge) badge.classList.add('hidden');
  };

  window.saveAppearancePresetForm = async function() {
    const id = getVal('appPresetId') || 'megan_custom';
    const name = getVal('appPresetName') || id;
    const desc = getVal('appPresetDesc');

    const genderSel = document.getElementById('appRpgGender');
    const ageSel = document.getElementById('appRpgAge');
    const heightSel = document.getElementById('appPresetHeight');
    const hairColSel = document.getElementById('appPresetHairColor');
    const hairCutSel = document.getElementById('appRpgHairStyle');
    const eyeColSel = document.getElementById('appPresetEyeColor');
    const bodySel = document.getElementById('appRpgBodyType');
    const vibeSel = document.getElementById('appRpgVibe');

    const heightVal = heightSel ? heightSel.value : getVal('appPresetHeight');

    const statusEl = document.getElementById('appearanceModalStatus');
    if (statusEl) {
      statusEl.textContent = 'Speichere Profil...';
      statusEl.className = 'text-xs font-mono text-cyan-400 animate-pulse';
    }

    const payload = {
      id: id,
      name: name,
      description: desc,
      prompt_height: heightVal,
      gender: genderSel ? genderSel.value : 'Weiblich',
      age: ageSel ? ageSel.value : '24',
      visual: {
        body: getVal('appPresetBody'),
        eyes: [getVal('appPresetEyeColor'), getVal('appPresetEyeShape')].filter(Boolean).join(', '),
        eye_color: getVal('appPresetEyeColor'),
        eye_shape: getVal('appPresetEyeShape'),
        hair: [getVal('appPresetHairColor'), getVal('appPresetHairCut')].filter(Boolean).join(', '),
        hair_color: getVal('appPresetHairColor'),
        hair_style: getVal('appPresetHairCut'),
        face: [getVal('appPresetEyeColor'), getVal('appPresetEyeShape')].filter(Boolean).join(', '),
        features: getVal('appPresetFeatures'),
        style_aesthetic: 'photorealistic, raw photo, 8k uhd, dslr, high quality, soft studio lighting, rim lighting, vibrant, well-lit, high key, detailed skin texture, natural skin pores'
      },
      physical_chat_lore: {
        selbstbild: getVal('appLoreSelbstbild') || getVal('appLoreStatur'),
        intim: getVal('appLoreIntim') || getVal('appLoreOberweite'),
        gestik: getVal('appLoreGestik') || getVal('appLoreGesicht'),
        markantes_merkmal: getVal('appLoreMarkant'),
        statur: getVal('appLoreSelbstbild') || getVal('appLoreStatur'),
        oberweite: getVal('appLoreIntim') || getVal('appLoreOberweite'),
        gesicht: getVal('appLoreGestik') || getVal('appLoreGesicht'),
        haare: getVal('appPresetHairColor') + ' ' + getVal('appPresetHairCut')
      }
    };

    try {
      const res = await fetch('/api/bot/appearance/presets', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      const data = await res.json();
      if (res.ok && data.success) {
        if (statusEl) {
          statusEl.textContent = `✔ Profil '${name}' gespeichert!`;
          statusEl.className = 'text-xs font-mono text-emerald-400';
        }
        await loadAppearancePresets();
      } else {
        if (statusEl) {
          statusEl.textContent = `Fehler: ${data.error || 'Speichern fehlgeschlagen'}`;
          statusEl.className = 'text-xs font-mono text-rose-400';
        }
      }
    } catch (e) {
      if (statusEl) {
        statusEl.textContent = `Netzwerkfehler: ${e.message}`;
        statusEl.className = 'text-xs font-mono text-rose-400';
      }
    }
  };

  window.activateAppearancePresetForm = async function() {
    const id = getVal('appPresetId');
    if (!id) {
      alert('Bitte vorher ein Profil auswählen oder speichern.');
      return;
    }

    await window.saveAppearancePresetForm();

    const statusEl = document.getElementById('appearanceModalStatus');
    if (statusEl) {
      statusEl.textContent = 'Aktiviere Profil & synchronisiere character.json...';
      statusEl.className = 'text-xs font-mono text-pink-400 animate-pulse';
    }

    try {
      const res = await fetch(`/api/bot/appearance/presets/${encodeURIComponent(id)}/activate`, {
        method: 'POST'
      });
      const data = await res.json();
      if (res.ok && data.success) {
        if (statusEl) {
          statusEl.textContent = `✔ Aussehen '${id}' ist jetzt aktiv! (Cluster synchronisiert)`;
          statusEl.className = 'text-xs font-mono text-emerald-400';
        }
        await loadAppearancePresets();
      } else {
        if (statusEl) {
          statusEl.textContent = `Fehler beim Aktivieren: ${data.error || 'Serverfehler'}`;
          statusEl.className = 'text-xs font-mono text-rose-400';
        }
      }
    } catch (e) {
      if (statusEl) {
        statusEl.textContent = `Netzwerkfehler: ${e.message}`;
        statusEl.className = 'text-xs font-mono text-rose-400';
      }
    }
  };

  window.deleteAppearancePresetForm = async function() {
    const id = getVal('appPresetId');
    if (!id) return;
    if (id === 'megan_athletic_sleeve') {
      alert('Das Basis-Standardprofil kann nicht gelöscht werden.');
      return;
    }
    if (!confirm(`Profil '${id}' wirklich unwiderruflich löschen?`)) {
      return;
    }

    try {
      const res = await fetch(`/api/bot/appearance/presets/${encodeURIComponent(id)}`, {
        method: 'DELETE'
      });
      const data = await res.json();
      if (res.ok && data.success) {
        alert(`Profil '${id}' gelöscht.`);
        await loadAppearancePresets();
      } else {
        alert(`Fehler: ${data.error || 'Löschen fehlgeschlagen'}`);
      }
    } catch (e) {
      alert(`Netzwerkfehler: ${e.message}`);
    }
  };

  // Bind live listeners
  function initListeners() {
    // 1. RPG Dropdowns
    const rpgDropdowns = [
      'appRpgGender', 'appRpgAge', 'appPresetHeight',
      'appPresetHairColor', 'appRpgHairStyle', 'appPresetEyeColor',
      'appRpgBodyType', 'appRpgVibe'
    ];
    rpgDropdowns.forEach(id => {
      const el = document.getElementById(id);
      if (el) {
        el.addEventListener('change', window.onRpgDropdownChange);
        el.addEventListener('input', window.onRpgDropdownChange);
      }
    });

    // 2. Left visual text inputs
    ['appPresetBody', 'appPresetEyeColor', 'appPresetEyeShape', 'appPresetHairColor', 'appPresetHairCut', 'appPresetFeatures'].forEach(id => {
      const el = document.getElementById(id);
      if (el) {
        el.addEventListener('input', updateCombinedVisualPreview);
        el.addEventListener('change', updateCombinedVisualPreview);
      }
    });

    // 3. Right lore inputs
    ['appLoreSelbstbild', 'appLoreIntim', 'appLoreGestik', 'appLoreMarkant', 'appLoreStatur', 'appLoreOberweite', 'appLoreGesicht', 'appLoreHaare', 'appLoreKoerper'].forEach(id => {
      const el = document.getElementById(id);
      if (el) {
        el.addEventListener('input', updateLorePreview);
        el.addEventListener('change', updateLorePreview);
      }
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initListeners);
  } else {
    initListeners();
  }
})();
