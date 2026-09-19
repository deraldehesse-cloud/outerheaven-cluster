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

  function populatePresetForm(p) {
    currentPresetId = p.id;

    // Header metadata
    setVal('appPresetId', p.id || '');
    setVal('appPresetName', p.name || '');
    setVal('appPresetHeight', p.prompt_height || '');
    setVal('appPresetDesc', p.description || '');
    setVal('appRpgGender', p.gender || (p.rpg && p.rpg.gender) || 'Weiblich');
    setVal('appRpgAge', p.age || (p.rpg && p.rpg.age) || '');

    const badge = document.getElementById('appearanceActiveBadge');
    if (badge) {
      if (p.is_active) {
        badge.classList.remove('hidden');
      } else {
        badge.classList.add('hidden');
      }
    }

    // Left Column: Visual Tags
    const vis = p.visual || {};
    setVal('appPresetBody', vis.body || '');
    setVal('appPresetEyeColor', vis.eye_color || '');
    setVal('appPresetEyeShape', vis.eye_shape || vis.eyes || '');
    setVal('appPresetHairColor', vis.hair_color || '');
    setVal('appPresetHairCut', vis.hair_style || vis.hair || '');
    setVal('appPresetFeatures', vis.features || '');

    // Right Column: Narrative Lore
    const lore = p.physical_chat_lore || {};
    setVal('appLoreStatur', lore.statur || '');
    setVal('appLoreOberweite', lore.oberweite || '');
    setVal('appLoreGesicht', lore.gesicht || '');
    setVal('appLoreHaare', lore.haare || '');
    setVal('appLoreMarkant', lore.markantes_merkmal || '');
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
    const statur = getVal('appLoreStatur');
    const oberweite = getVal('appLoreOberweite');
    const gesicht = getVal('appLoreGesicht');
    const haare = getVal('appLoreHaare');
    const markant = getVal('appLoreMarkant');
    const koerper = getVal('appLoreKoerper');

    const previewEl = document.getElementById('appLoreSystemPromptPreview');
    if (previewEl) {
      previewEl.textContent =
`### PHYSISCHE PRÄSENZ & EROTIK (AUS DEM PROFIL)
* **Statur & Silhouette:** ${statur || '--'}
* **Oberweite:** ${oberweite || '--'}
* **Gesicht & Blick:** ${gesicht || '--'}
* **Haare:** ${haare || '--'}
* **Markantes Merkmal:** ${markant || '--'}
* **Körperliche Merkmale:** ${koerper || '--'}`;
    }
  };

  /**
   * [ ✨ Aus Merkmalen generieren ]
   * Translates visual ComfyUI tags into natural German narrative lore.
   */
  window.generateLoreFromVisualTags = function() {
    const body = getVal('appPresetBody').toLowerCase();
    const eyeCol = getVal('appPresetEyeColor');
    const eyeShape = getVal('appPresetEyeShape');
    const hairCol = getVal('appPresetHairColor');
    const hairCut = getVal('appPresetHairCut');
    const features = getVal('appPresetFeatures');
    const height = getVal('appPresetHeight');

    // 1. Statur & Silhouette
    let staturText = '';
    if (height) {
      staturText += `${height.replace(/tall/i, '').trim()} groß. `;
    }
    if (body.includes('slender') && body.includes('athletic')) {
      staturText += 'Schlank und athletisch trainiert mit einer femininen Sanduhr-Figur.';
    } else if (body.includes('curvy') || body.includes('voluptuous')) {
      staturText += 'Sinnlich kurvig mit üppigen, wohlgeformten weiblichen Rundungen und schmaler Taille.';
    } else if (body.includes('petite')) {
      staturText += 'Zierlich und feminin mit straffem, jugendlich anmutendem Körperbau.';
    } else {
      staturText += 'Schlanke und wohlproportionierte weibliche Silhouette mit weichen Kurven.';
    }
    setVal('appLoreStatur', staturText.trim());

    // 2. Oberweite & Dekolleté
    let oberweiteText = '';
    if (body.includes('very large') || body.includes('heavy breasts') || body.includes('huge breasts')) {
      oberweiteText = 'Sehr große, pralle und wunderschön geformte natürliche Brüste mit tiefem, atemberaubendem Dekolleté.';
    } else if (body.includes('large breasts')) {
      oberweiteText = 'Große, straffe und perfekt geformte Brüste, die jedes Top verführerisch ausfüllen.';
    } else if (body.includes('medium breasts')) {
      oberweiteText = 'Wohlgeformte, straffe und natürlich proportionierte Oberweite.';
    } else if (body.includes('small breasts') || body.includes('petite breasts')) {
      oberweiteText = 'Zarte, straffe und feste kleine Brüste mit empfindlichen Knospen.';
    } else {
      oberweiteText = 'Straffe, feste und perfekt geformte Brüste mit wohlgeformten Rundungen.';
    }
    setVal('appLoreOberweite', oberweiteText);

    // 3. Gesicht & Blick
    let gesichtText = '';
    const augenFarbeDe = translateColor(eyeCol);
    const augenBlick = eyeShape || 'sanfter, betörender Blick';
    if (augenFarbeDe) {
      gesichtText = `Wunderschönes, markantes Gesicht mit faszinierenden ${augenFarbeDe}en Augen (${augenBlick}) und vollen, leicht glänzenden Lippen.`;
    } else {
      gesichtText = `Wunderschönes, feminines Gesicht mit ausdrucksstarkem Blick (${augenBlick}) und sanft geschwungenen Lippen.`;
    }
    setVal('appLoreGesicht', gesichtText);

    // 4. Haare & Frisur
    let haareText = '';
    const haarFarbeDe = translateColor(hairCol);
    const schnitt = hairCut || 'weiche Wellen';
    if (haarFarbeDe) {
      haareText = `Glänzende, seidige ${haarFarbeDe}e Haare im Stil: ${schnitt}.`;
    } else {
      haareText = `Seidig glänzende, verführerische Haare (${schnitt}).`;
    }
    setVal('appLoreHaare', haareText);

    // 5. Markantes Merkmal (Tattoos & Piercings)
    let markantText = '';
    if (features) {
      if (features.toLowerCase().includes('sleeve')) {
        markantText = 'Ein kunstvolles, detailreiches Sleeve-Tattoo auf dem linken Arm sowie feine Piercings.';
      } else if (features.toLowerCase().includes('tattoo')) {
        markantText = `Einzigartige Tattoo-Verzierungen (${features}) und dezent blitzender Körperschmuck.`;
      } else {
        markantText = `Besondere körperliche Akzente: ${features}.`;
      }
    } else {
      markantText = 'Ein dezentes silbernes Bauchnabel-Piercing und samtweiche, makellose Haut.';
    }
    setVal('appLoreMarkant', markantText);

    // 6. Körperliche Merkmale
    let koerperText = 'Flacher, leicht definierter Bauch, straffer trainierter Hintern und samtige, sonnengeküsste Haut.';
    if (body.includes('abs') || body.includes('toned stomach')) {
      koerperText = 'Deutlich sichtbare Bauchmuskeln, flacher durchtrainierter Bauch, fester knackiger Po und samtweiche Haut.';
    }
    setVal('appLoreKoerper', koerperText);

    updateLorePreview();

    // Visual feedback animation
    const rightCol = document.getElementById('appearanceModalRightCol');
    if (rightCol) {
      rightCol.classList.add('ring-2', 'ring-pink-500');
      setTimeout(() => rightCol.classList.remove('ring-2', 'ring-pink-500'), 1000);
    }
  };

  function translateColor(col) {
    if (!col) return '';
    const c = col.toLowerCase();
    if (c.includes('dark brown')) return 'dunkelbraun';
    if (c.includes('brown')) return 'braun';
    if (c.includes('auburn')) return 'kupferbraun';
    if (c.includes('blonde')) return 'blond';
    if (c.includes('black')) return 'tiefschwarz';
    if (c.includes('green-blue') || c.includes('blue-green')) return 'grün-blau';
    if (c.includes('hazel')) return 'haselnussbraun';
    if (c.includes('green')) return 'smaragdgrün';
    if (c.includes('blue')) return 'strahlend blau';
    if (c.includes('grey') || c.includes('gray')) return 'silbergrau';
    return col;
  }

  window.initNewAppearancePreset = function() {
    currentPresetId = '';
    setVal('appPresetId', 'megan_custom_' + Math.floor(Math.random() * 1000));
    setVal('appPresetName', 'Megan (Neues Aussehen)');
    setVal('appPresetHeight', '178cm tall');
    setVal('appPresetDesc', 'Individuelles Aussehen');
    setVal('appPresetBody', '1girl, solo, 178cm tall, slender athletic build, voluptuous hourglass figure, very large full natural breasts, flat toned stomach');
    setVal('appPresetEyeColor', 'green-blue eyes');
    setVal('appPresetEyeShape', 'alluring captivating gaze, playful seductive smile');
    setVal('appPresetHairColor', 'dark brown hair');
    setVal('appPresetHairCut', 'long layered hair, soft natural waves');
    setVal('appPresetFeatures', 'massive intricate sleeve tattoo left arm, silver navel piercing');

    window.generateLoreFromVisualTags();
    const badge = document.getElementById('appearanceActiveBadge');
    if (badge) badge.classList.add('hidden');
  };

  window.saveAppearancePresetForm = async function() {
    const id = getVal('appPresetId') || 'megan_custom';
    const name = getVal('appPresetName') || id;
    const height = getVal('appPresetHeight');
    const desc = getVal('appPresetDesc');

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
      gender: getVal('appRpgGender') || 'Weiblich',
      age: getVal('appRpgAge') || '',
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
        statur: getVal('appLoreStatur'),
        oberweite: getVal('appLoreOberweite'),
        gesicht: getVal('appLoreGesicht'),
        haare: getVal('appLoreHaare'),
        markantes_merkmal: getVal('appLoreMarkant'),
        koerperliche_merkmale: getVal('appLoreKoerper')
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

    // Save first to ensure latest edits are captured
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
          statusEl.textContent = `✔ Aussehen '${id}' ist jetzt aktiv! (Bot neu gestartet)`;
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

  // Bind live preview listeners
  document.addEventListener('DOMContentLoaded', () => {
    ['appPresetBody', 'appPresetEyeColor', 'appPresetEyeShape', 'appPresetHairColor', 'appPresetHairCut', 'appPresetFeatures'].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.addEventListener('input', updateCombinedVisualPreview);
    });

    ['appLoreStatur', 'appLoreOberweite', 'appLoreGesicht', 'appLoreHaare', 'appLoreMarkant', 'appLoreKoerper'].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.addEventListener('input', updateLorePreview);
    });
  });
})();
