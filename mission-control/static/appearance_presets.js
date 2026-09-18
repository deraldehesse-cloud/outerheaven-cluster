/**
 * appearance_presets.js - Appearance Presets & Lore Generator Controller
 * Outer Heaven Mission Control Dashboard
 *
 * Requirements:
 *   - Saubere 2-Spalten-Trennung im Modal:
 *       * Links: Visuelle ComfyUI-Tags (Körperbau, isolierte Augen, Haare nach Farbe & Schnitt getrennt, Tattoos/Piercings)
 *       * Rechts: Narrative Lore / System-Prompt (wie der Bot über sich spricht)
 *   - Button [ ✨ Aus Merkmalen generieren ] baut aus den visuellen Tags eine Lore-Kurzbeschreibung
 *   - Synchronisation mit character.json, bot_config.json und Service-Reload
 */

(() => {
  let loadedPresets = [];
  let currentPresetId = '';

  window.openAppearanceModal = function() {
    const modal = document.getElementById('appearancePresetModal');
    if (modal) {
      modal.classList.remove('hidden');
      modal.style.display = 'flex';
    }
    // If presets are already in memory, immediately populate form
    if (loadedPresets && loadedPresets.length > 0) {
      const p = (currentPresetId && loadedPresets.find(item => item.id === currentPresetId)) ||
                loadedPresets.find(item => item.is_active) ||
                loadedPresets[0];
      if (p) populatePresetForm(p);
    }
    window.loadActiveAppearanceProfile();
  };

  window.closeAppearanceModal = function() {
    const modal = document.getElementById('appearancePresetModal');
    if (modal) {
      modal.classList.add('hidden');
      modal.style.display = 'none';
    }
  };

  window.loadActiveAppearanceProfile = async function() {
    console.log('[*] loadActiveAppearanceProfile(): Lade Profil-Daten...');
    const sel = document.getElementById('appearancePresetSelect');
    if (sel && sel.options.length <= 1) sel.innerHTML = '<option value="">Lade Profile...</option>';

    try {
      const res = await fetch('/api/bot/appearance/presets');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      if (!data.success) {
        console.warn('loadActiveAppearanceProfile: API success=false');
        return;
      }

      loadedPresets = data.presets || [];
      const activeId = data.active_id || '';
      currentPresetId = currentPresetId || activeId;

      if (sel) {
        sel.innerHTML = '';
        loadedPresets.forEach(p => {
          const opt = document.createElement('option');
          opt.value = p.id;
          opt.textContent = `${p.name || p.id}${p.is_active ? ' (Aktiv)' : ''}`;
          if (p.id === currentPresetId || (!currentPresetId && (p.id === activeId || p.is_active))) opt.selected = true;
          sel.appendChild(opt);
        });
      }

      const targetPreset = (currentPresetId && loadedPresets.find(p => p.id === currentPresetId)) ||
                           loadedPresets.find(p => p.id === activeId) ||
                           loadedPresets.find(p => p.is_active) ||
                           loadedPresets[0];
      if (targetPreset) {
        currentPresetId = targetPreset.id;
        populatePresetForm(targetPreset);
        console.log(`[✔] Profil '${targetPreset.name || targetPreset.id}' erfolgreich geladen und in DOM gemappt.`);
      }
    } catch (e) {
      console.warn('Fehler beim Laden des aktiven Aussehen-Profils:', e);
      if (sel) sel.innerHTML = '<option value="">Fehler beim Laden</option>';
    }
  };

  window.loadAppearancePresets = window.loadActiveAppearanceProfile;

  window.onAppearancePresetSelectChange = function(presetId) {
    const p = loadedPresets.find(item => item.id === presetId);
    if (p) {
      currentPresetId = p.id;
      populatePresetForm(p);
    }
  };

  function populatePresetForm(p) {
    if (!p) return;
    currentPresetId = p.id;

    // 1. Profile Basics
    setVal('appPresetId', p.id || '');
    setVal('appPresetName', p.name || '');
    setVal('appPresetDesc', p.description || '');

    const badge = document.getElementById('appearanceActiveBadge');
    if (badge) {
      if (p.is_active) {
        badge.classList.remove('hidden');
      } else {
        badge.classList.add('hidden');
      }
    }

    // 2. SCHRITT 2: RPG-Kopfleiste (Strukturierte Attribute)
    const rpg = p.rpg || {};
    const vis = p.visual || {};
    const bodyStr = (vis.body || '').toLowerCase();

    // Gender fallback & normalization
    let genderVal = rpg.gender || '';
    if (!genderVal) {
      if (bodyStr.includes('1boy') || bodyStr.includes('male') || bodyStr.includes('man')) {
        genderVal = 'Männlich';
      } else if (bodyStr.includes('androgynous') || bodyStr.includes('1person')) {
        genderVal = 'Androgyn';
      } else {
        genderVal = 'Weiblich';
      }
    }
    const gLower = genderVal.toLowerCase();
    if (gLower.includes('männ') || gLower.includes('male') || gLower.includes('boy')) {
      genderVal = 'Männlich';
    } else if (gLower.includes('andro')) {
      genderVal = 'Androgyn';
    } else {
      genderVal = 'Weiblich';
    }
    setVal('appRpgGender', genderVal);

    // Age
    setVal('appRpgAge', rpg.age || (bodyStr.includes('teen') ? '20' : '24'));

    // Height
    setVal('appPresetHeight', rpg.height || p.prompt_height || extractHeightFromText(bodyStr) || '168cm tall');

    // Hair Color
    setVal('appPresetHairColor', rpg.hair_color || vis.hair_color || extractColorFromText(vis.hair || '', 'hair') || 'Dark brown');

    // Eye Color
    setVal('appPresetEyeColor', rpg.eye_color || vis.eye_color || extractColorFromText(vis.eyes || vis.face || '', 'eyes') || 'Green-blue');

    // 3. SCHRITT 3: Linke Spalte (Visuelle ComfyUI-Tags)
    setVal('appPresetBody', vis.body || '');
    setVal('appPresetHairCut', vis.hair_style || vis.hair || '');
    setVal('appPresetEyeShape', vis.eye_shape || vis.eyes || vis.face || '');
    setVal('appPresetFeatures', vis.features || '');

    // 4. SCHRITT 3: Rechte Spalte (Narrative Chat-Lore)
    const lore = p.physical_chat_lore || {};
    setVal('appLoreSelbstbild', lore.selbstbild || lore.statur || '');
    setVal('appLoreIntim', lore.intime_details || lore.oberweite || '');
    setVal('appLoreGestik', lore.gestik_haltung || lore.gesicht || lore.koerperliche_merkmale || '');
    setVal('appLoreMarkant', lore.signatur_merkmale || lore.markantes_merkmal || '');

    updateCombinedVisualPreview();
    updateLorePreview();
  }
  window.populatePresetForm = populatePresetForm;

  function setVal(id, val) {
    const el = document.getElementById(id);
    if (el) el.value = val;
  }

  function getVal(id) {
    const el = document.getElementById(id);
    return el ? el.value.trim() : '';
  }

  function extractHeightFromText(txt) {
    const m = txt.match(/\b(\d{3}\s*cm\s*tall|\d[.,]\d{2}\s*m)\b/i);
    return m ? m[0] : '';
  }

  function extractColorFromText(txt, type) {
    if (!txt) return '';
    const clean = txt.replace(new RegExp(type, 'gi'), '').replace(/,/g, '').trim();
    return clean || txt;
  }

  window.updateCombinedVisualPreview = function() {
    const gender = getVal('appRpgGender');
    const age = getVal('appRpgAge');
    const height = getVal('appPresetHeight');
    const hairCol = getVal('appPresetHairColor');
    const eyeCol = getVal('appPresetEyeColor');

    const body = getVal('appPresetBody');
    const hairCut = getVal('appPresetHairCut');
    const eyeShape = getVal('appPresetEyeShape');
    const features = getVal('appPresetFeatures');

    const tags = [];

    // Base subject
    if (gender === 'Männlich') tags.push('1boy, solo');
    else if (gender === 'Androgyn') tags.push('1person, solo, androgynous');
    else tags.push('1girl, solo');

    if (height && !body.toLowerCase().includes(height.toLowerCase())) tags.push(height);
    if (age && !body.toLowerCase().includes(`${age}yo`)) {
      const numAge = parseInt(age);
      if (!isNaN(numAge)) tags.push(`${numAge}yo`);
    }

    if (body) {
      // Avoid duplicating 1girl/solo
      const cleanBody = body.replace(/^(1girl|1boy|1person|solo|\s*,\s*)+/gi, '').trim();
      if (cleanBody) tags.push(cleanBody);
    }

    // Hair
    const hairParts = [hairCol, hairCut].filter(Boolean);
    if (hairParts.length) tags.push(hairParts.join(', '));

    // Eyes
    const eyeParts = [eyeCol, eyeShape].filter(Boolean);
    if (eyeParts.length) tags.push(eyeParts.join(', '));

    // Features
    if (features) tags.push(features);

    const comb = tags.filter(Boolean).join(', ');
    const previewEl = document.getElementById('appPresetCombinedPreview');
    if (previewEl) {
      previewEl.textContent = comb || '1girl, solo, ... (Wird automatisch zusammengestellt)';
    }
    return comb;
  };

  window.updateLorePreview = function() {
    const selbstbild = getVal('appLoreSelbstbild');
    const intim = getVal('appLoreIntim');
    const gestik = getVal('appLoreGestik');
    const markant = getVal('appLoreMarkant');

    const previewEl = document.getElementById('appLoreSystemPromptPreview');
    if (previewEl) {
      previewEl.textContent =
`### PHYSISCHE PRÄSENZ & EROTIK (AUS DEM PROFIL)
* **Selbstbild & Physis:** ${selbstbild || '--'}
* **Intime Details & Dekolleté:** ${intim || '--'}
* **Gestik & Haltung:** ${gestik || '--'}
* **Signatur-Merkmale:** ${markant || '--'}`;
    }
  };

  /**
   * [ ✨ Aus Merkmalen generieren ]
   * Liest RPG-Kopfdaten + visuelle Tags aus und baut automatisch zusammenhängende deutsche Lore-Sätze.
   */
  window.generateLoreFromVisualTags = function() {
    const gender = getVal('appRpgGender') || 'Weiblich';
    const age = getVal('appRpgAge') || '24';
    const height = getVal('appPresetHeight');
    const hairCol = getVal('appPresetHairColor');
    const eyeCol = getVal('appPresetEyeColor');

    const body = getVal('appPresetBody').toLowerCase();
    const hairCut = getVal('appPresetHairCut');
    const eyeShape = getVal('appPresetEyeShape');
    const features = getVal('appPresetFeatures');

    // --- 1. Selbstbild & Physis ---
    let heightFormatted = '';
    if (height) {
      const cmMatch = height.match(/(\d{3})/);
      if (cmMatch) {
        const meters = (parseInt(cmMatch[1]) / 100).toFixed(2).replace('.', ',');
        heightFormatted = `${meters} m groß`;
      } else {
        heightFormatted = height.replace(/tall/i, '').trim();
      }
    }

    let personDesc = 'junge Frau';
    if (gender === 'Männlich') personDesc = 'junger Mann';
    else if (gender === 'Androgyn') personDesc = 'androgyne Person';

    let agePart = age ? `${age} Jahre alt` : 'Anfang zwanzig';
    let baseIntro = [heightFormatted, agePart].filter(Boolean).join(', ');
    if (baseIntro) baseIntro += `. Eine attraktive ${personDesc} `;
    else baseIntro = `Eine attraktive ${personDesc} `;

    let buildDesc = '';
    if (body.includes('slender') && body.includes('athletic')) {
      buildDesc = 'mit schlankem, athletisch durchtrainiertem Körperbau und sehr femininer Sanduhr-Figur.';
    } else if (body.includes('curvy') || body.includes('voluptuous')) {
      buildDesc = 'mit sinnlich üppigen Rundungen, schmaler Taille und weichen, femininen Kurven.';
    } else if (body.includes('petite')) {
      buildDesc = 'mit zierlicher, kompakter und sportlich straffer Statur.';
    } else if (body.includes('toned') || body.includes('fit')) {
      buildDesc = 'mit sportlich straffem Körper, klar definierten Linien und natürlicher Eleganz.';
    } else {
      buildDesc = 'mit harmonischer, wohlproportionierter Silhouette und eleganter Ausstrahlung.';
    }
    setVal('appLoreSelbstbild', (baseIntro + buildDesc).trim());

    // --- 2. Intime/Physische Details (Dekolleté/Vorlieben) ---
    let intimText = '';
    if (body.includes('very large') || body.includes('heavy breasts') || body.includes('huge breasts')) {
      intimText = 'Sehr große, pralle und wunderschön geformte natürliche Brüste mit tiefem, atemberaubendem Dekolleté, flachem Bauch und samtweicher, makelloser Haut.';
    } else if (body.includes('large breasts') || body.includes('big breasts')) {
      intimText = 'Große, feste und wohlgeformte Brüste, die jedes Top verführerisch ausfüllen, sowie sinnlich geschwungene Hüften.';
    } else if (body.includes('medium breasts')) {
      intimText = 'Wohlproportionierte, straffe und natürlich geformte Oberweite mit sanften Rundungen und zarter Haut.';
    } else if (body.includes('small breasts') || body.includes('petite breasts')) {
      intimText = 'Zarte, straffe und feste kleine Brüste mit empfindlichen Knospen und flachem trainiertem Bauch.';
    } else {
      intimText = 'Perfekt geformte, feste Oberweite mit sinnlichem Dekolleté, knackigem Hintern und seidig glatter Haut.';
    }
    setVal('appLoreIntim', intimText);

    // --- 3. Gestik / Haltung ---
    const augenFarbeDe = translateColor(eyeCol) || 'ausdrucksstarken';
    const augenBlick = eyeShape || 'betörender, direkter Blick';
    let gestikText = `Selbstbewusste und offene Körpersprache; faszinierende ${augenFarbeDe}e Augen mit unverstelltem Ausdruck (${augenBlick}) und einem verspielten, leicht fordernden Lächeln.`;
    setVal('appLoreGestik', gestikText);

    // --- 4. Signatur-Merkmale (Haare, Tattoos, Piercings) ---
    const haarFarbeDe = translateColor(hairCol) || 'gepflegte';
    const schnitt = hairCut || 'natürliche Wellen';
    let markantParts = [];
    markantParts.push(`Seidig glänzende, ${haarFarbeDe}e Haare im Stil: ${schnitt}`);

    if (features) {
      if (features.toLowerCase().includes('sleeve')) {
        markantParts.push('ein massives, kunstvoll detailliertes Sleeve-Tattoo auf dem linken Arm sowie feiner Körperschmuck');
      } else if (features.toLowerCase().includes('tattoo')) {
        markantParts.push(`charakteristische Verzierungen (${features})`);
      } else {
        markantParts.push(`besondere Akzente: ${features}`);
      }
    } else {
      markantParts.push('ein dezentes silbernes Bauchnabel-Piercing und makellose, reine Haut');
    }

    setVal('appLoreMarkant', markantParts.join(' sowie ') + '.');

    updateLorePreview();

    // Visual feedback animation
    const rightCol = document.getElementById('appearanceModalRightCol');
    if (rightCol) {
      rightCol.classList.add('ring-2', 'ring-pink-500');
      setTimeout(() => rightCol.classList.remove('ring-2', 'ring-pink-500'), 800);
    }
  };

  function translateColor(col) {
    if (!col) return '';
    const c = col.toLowerCase();
    if (c.includes('orange') || c.includes('ginger')) return 'leuchtend orange/kupferrot';
    if (c.includes('dark brown')) return 'dunkelbraun';
    if (c.includes('brown')) return 'braun';
    if (c.includes('auburn')) return 'kastanienbraun';
    if (c.includes('blonde') || c.includes('blond')) return 'blond';
    if (c.includes('black')) return 'tiefschwarz';
    if (c.includes('green-blue') || c.includes('blue-green')) return 'grün-blau';
    if (c.includes('hazel')) return 'haselnussbraun';
    if (c.includes('amber')) return 'bernsteinfarben';
    if (c.includes('green') || c.includes('emerald')) return 'smaragdgrün';
    if (c.includes('blue')) return 'strahlend blau';
    if (c.includes('grey') || c.includes('gray')) return 'silbergrau';
    return col;
  }

  window.initNewAppearancePreset = function() {
    currentPresetId = '';
    const randomId = 'megan_rpg_' + Math.floor(Math.random() * 1000);
    setVal('appPresetId', randomId);
    setVal('appPresetName', 'Megan (RPG Custom)');
    setVal('appPresetDesc', 'Individuell erstelltes RPG-Aussehen');

    // RPG attributes
    setVal('appRpgGender', 'Weiblich');
    setVal('appRpgAge', '24');
    setVal('appPresetHeight', '168cm tall');
    setVal('appPresetHairColor', 'Orange/Ginger');
    setVal('appPresetEyeColor', 'Grün-Blau');

    // Visual tags
    setVal('appPresetBody', '1girl, solo, 168cm tall, slender athletic build, voluptuous hourglass figure, very large full natural breasts, flat toned stomach');
    setVal('appPresetHairCut', 'long layered hair, soft natural waves');
    setVal('appPresetEyeShape', 'alluring captivating gaze, playful seductive smile');
    setVal('appPresetFeatures', 'massive intricate sleeve tattoo left arm, subtle navel piercing');

    window.generateLoreFromVisualTags();

    const badge = document.getElementById('appearanceActiveBadge');
    if (badge) badge.classList.add('hidden');
  };

  window.saveAppearancePresetForm = async function() {
    const id = getVal('appPresetId') || 'megan_custom';
    const name = getVal('appPresetName') || id;
    const desc = getVal('appPresetDesc');

    // RPG data
    const gender = getVal('appRpgGender') || 'Weiblich';
    const age = getVal('appRpgAge') || '24';
    const height = getVal('appPresetHeight');
    const hairColor = getVal('appPresetHairColor');
    const eyeColor = getVal('appPresetEyeColor');

    // Visual tags
    const body = getVal('appPresetBody');
    const hairCut = getVal('appPresetHairCut');
    const eyeShape = getVal('appPresetEyeShape');
    const features = getVal('appPresetFeatures');

    // Lore fields
    const selbstbild = getVal('appLoreSelbstbild');
    const intim = getVal('appLoreIntim');
    const gestik = getVal('appLoreGestik');
    const markant = getVal('appLoreMarkant');

    const statusEl = document.getElementById('appearanceModalStatus');
    if (statusEl) {
      statusEl.textContent = 'Speichere Profil...';
      statusEl.className = 'text-xs font-mono text-cyan-400 animate-pulse';
    }

    const payload = {
      id: id,
      name: name,
      description: desc,
      prompt_height: height,
      rpg: {
        gender: gender,
        age: age,
        height: height,
        hair_color: hairColor,
        eye_color: eyeColor
      },
      visual: {
        body: body,
        eyes: [eyeColor, eyeShape].filter(Boolean).join(', '),
        eye_color: eyeColor,
        eye_shape: eyeShape,
        hair: [hairColor, hairCut].filter(Boolean).join(', '),
        hair_color: hairColor,
        hair_style: hairCut,
        face: [eyeColor, eyeShape].filter(Boolean).join(', '),
        features: features,
        style_aesthetic: 'photorealistic, raw photo, 8k uhd, dslr, high quality, soft studio lighting, rim lighting, vibrant, well-lit, high key, detailed skin texture, natural skin pores'
      },
      physical_chat_lore: {
        selbstbild: selbstbild,
        intime_details: intim,
        gestik_haltung: gestik,
        signatur_merkmale: markant,
        // Standard mappings for character.json system_prompt synchronization:
        statur: selbstbild,
        oberweite: intim,
        gesicht: gestik,
        haare: `Glänzende ${hairColor ? translateColor(hairColor) + 'e' : 'schöne'} Haare (${hairCut || 'weiche Wellen'}).`,
        markantes_merkmal: markant,
        koerperliche_merkmale: gestik
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

    // Save first to capture any unsaved edits
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
          statusEl.textContent = `✔ Profil '${id}' aktiv! (Cluster-Sync & Bot-Reload)`;
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

  // Bind live preview listeners & load active profile
  function initAppearanceEvents() {
    // Header RPG fields
    ['appRpgGender', 'appRpgAge', 'appPresetHeight', 'appPresetHairColor', 'appPresetEyeColor'].forEach(id => {
      const el = document.getElementById(id);
      if (el) {
        el.addEventListener('input', updateCombinedVisualPreview);
        el.addEventListener('change', updateCombinedVisualPreview);
      }
    });

    // Left column tags
    ['appPresetBody', 'appPresetHairCut', 'appPresetEyeShape', 'appPresetFeatures'].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.addEventListener('input', updateCombinedVisualPreview);
    });

    // Right column lore
    ['appLoreSelbstbild', 'appLoreIntim', 'appLoreGestik', 'appLoreMarkant'].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.addEventListener('input', updateLorePreview);
    });

    // Appearance button click handlers
    ['btnHeaderAppearance', 'btnAppearancePresets', 'btnMeganHeadAppearance', 'btnOpenAppearanceModal', 'btnMeganStudioAppearance'].forEach(btnId => {
      const btn = document.getElementById(btnId);
      if (btn) {
        btn.addEventListener('click', (e) => {
          e.preventDefault();
          window.openAppearanceModal();
        });
      }
    });

    // Pre-cache active appearance profile
    window.loadActiveAppearanceProfile();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initAppearanceEvents);
  } else {
    initAppearanceEvents();
  }
})();
