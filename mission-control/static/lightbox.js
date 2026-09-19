/**
 * lightbox.js - Universal-Lightbox (Bild-Betrachter)
 * Outer Heaven Mission Control Dashboard
 *
 * Features:
 *   - Stufenloser Zoom per Mausrad (1.0x - 5.0x) mit Cursor grab/grabbing
 *   - Drag-Pan im gezoomten Zustand mit Clamping
 *   - Rand-Pfeile (◀ / ▶) und Tastatursteuerung (Pfeiltasten / ESC)
 *   - Aktionsleiste: [ 🗑️ Löschen ] (Confirm), [ 💾 Speichern ], [ 📁 Verschieben ] (Whitelist-Projekte)
 *   - Neues Flaw- & Feedback-System: Körper (7 Flaws), Charakter (Alter, Darstellung), Freitext
 */

(() => {
  let sessionImages = [];
  let currentImageIndex = -1;
  let currentFilename = '';
  let currentUrl = '';

  // Zoom & Pan state
  let scale = 1.0;
  let translateX = 0;
  let translateY = 0;
  let isDragging = false;
  let startX = 0;
  let startY = 0;

  // Selected flaws state
  let activeFlaws = new Set();

  const FLAW_MAP = {
    // Kategorie Körper
    deformiert: 'deformed, distorted, disfigured',
    bad_anatomy: 'bad anatomy, gross proportions',
    extra_fingers: 'extra fingers, missing fingers, fused fingers, mutated hands',
    deformed_face: 'deformed face, bad eyes, poorly drawn eyes, cross-eyed',
    low_quality: 'low quality, worst quality, lowres, blurry',
    bad_limbs: 'bad limbs, mutated limbs, missing limbs, floating limbs',
    tattoos: 'tattoos, tattoo markings, unwanted tattoos',
    // Kategorie Charakter
    zu_alt: 'older, mature, elderly, wrinkles',
    zu_jung: 'child, loli, infantile, teen, underage',
    realistisch: 'cartoon, anime, 3d, render, illustration, drawing',
    cartoon: 'photorealistic, raw photo, realistic art, dslr'
  };

  function updateTransform() {
    const img = document.getElementById('lightboxImg');
    if (!img) return;
    img.style.transform = `translate(${translateX}px, ${translateY}px) scale(${scale})`;
    img.style.transition = isDragging ? 'none' : 'transform 0.12s ease-out';
    img.style.cursor = scale > 1 ? (isDragging ? 'grabbing' : 'grab') : 'default';

    const zoomBadge = document.getElementById('lightboxZoomLevel');
    if (zoomBadge) {
      zoomBadge.textContent = `${Math.round(scale * 100)}%`;
      if (scale > 1.05) {
        zoomBadge.classList.remove('hidden');
      } else {
        zoomBadge.classList.add('hidden');
      }
    }
  }

  function resetZoom() {
    scale = 1.0;
    translateX = 0;
    translateY = 0;
    isDragging = false;
    updateTransform();
  }

  function collectSessionImages() {
    const list = [];
    const galleryItems = document.querySelectorAll('#galleryGrid [id^="gallery-card-"]');
    galleryItems.forEach(card => {
      const img = card.querySelector('img');
      if (img && img.src) {
        const id = card.id.replace('gallery-card-', '');
        list.push({ url: img.src, filename: img.alt || id });
      }
    });

    if (list.length === 0 && window.sessionGalleryImages && Array.isArray(window.sessionGalleryImages)) {
      return window.sessionGalleryImages;
    }

    sessionImages = list;
    return list;
  }

  window.openLightbox = function(url, filename) {
    const lb = document.getElementById('lightbox');
    const img = document.getElementById('lightboxImg');
    const dl = document.getElementById('lightboxDownload');
    if (!lb || !img) return;

    currentUrl = url;
    currentFilename = filename || (url ? url.split('/').pop().split('?')[0] : '');

    collectSessionImages();
    currentImageIndex = sessionImages.findIndex(item => item.url === url || item.filename === currentFilename);
    if (currentImageIndex === -1 && currentUrl) {
      sessionImages.unshift({ url: currentUrl, filename: currentFilename });
      currentImageIndex = 0;
    }

    img.src = url;
    if (dl) {
      dl.href = url;
      dl.setAttribute('download', currentFilename);
    }

    resetZoom();
    activeFlaws.clear();
    renderFlawChipsUI();
    const fbInput = document.getElementById('lightboxFlawFeedback');
    if (fbInput) fbInput.value = '';
    const statusMsg = document.getElementById('lightboxFlawStatus');
    if (statusMsg) statusMsg.textContent = '';

    lb.classList.remove('hidden');

    if (typeof inspectImageMetadata === 'function') {
      inspectImageMetadata(currentFilename);
    }
  };

  window.closeLightbox = function() {
    const lb = document.getElementById('lightbox');
    if (lb) lb.classList.add('hidden');
    resetZoom();
    currentFilename = '';
    currentUrl = '';
    activeFlaws.clear();
  };

  window.navigateLightbox = function(direction) {
    if (!sessionImages || sessionImages.length <= 1) {
      collectSessionImages();
    }
    if (sessionImages.length === 0) return;

    if (currentImageIndex === -1) currentImageIndex = 0;
    currentImageIndex += direction;

    if (currentImageIndex < 0) {
      currentImageIndex = sessionImages.length - 1;
    } else if (currentImageIndex >= sessionImages.length) {
      currentImageIndex = 0;
    }

    const nextItem = sessionImages[currentImageIndex];
    if (nextItem) {
      window.openLightbox(nextItem.url, nextItem.filename);
    }
  };

  window.deleteCurrentLightboxImage = async function() {
    if (!currentFilename) return;
    if (!confirm(`[MGS-TACTICAL] Bild wirklich dauerhaft löschen?\n\nDatei: ${currentFilename}`)) {
      return;
    }

    try {
      const res = await fetch(`/api/gallery/${encodeURIComponent(currentFilename)}`, {
        method: 'DELETE'
      });
      const data = await res.json();
      if (res.ok && data.status === 'deleted') {
        const safeId = `gallery-card-${currentFilename.replace(/[^a-zA-Z0-9_-]/g, '_')}`;
        const card = document.getElementById(safeId);
        if (card) card.remove();

        const countEl = document.getElementById('galleryCount');
        const grid = document.getElementById('galleryGrid');
        if (countEl && grid) countEl.textContent = `${grid.children.length} images`;

        sessionImages = sessionImages.filter(item => item.filename !== currentFilename);
        if (sessionImages.length > 0) {
          const nextIdx = Math.min(currentImageIndex, sessionImages.length - 1);
          const nextItem = sessionImages[nextIdx];
          window.openLightbox(nextItem.url, nextItem.filename);
        } else {
          window.closeLightbox();
        }
      } else {
        alert("Fehler beim Löschen: " + (data.error || "Unbekannter Fehler"));
      }
    } catch (e) {
      alert("Netzwerkfehler: " + e.message);
    }
  };

  window.downloadCurrentLightboxImage = function() {
    if (!currentUrl) return;
    const a = document.createElement('a');
    a.href = currentUrl;
    a.download = currentFilename || 'render.png';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  };

  window.openMoveImageModal = async function() {
    if (!currentFilename) return;
    try {
      const res = await fetch('/api/gallery/projects');
      const data = await res.json();
      const projects = (data.success && data.projects) ? data.projects : [
        "projekt-sexchat", "projekt-catlord", "projekt-dr-franks-bowling", "projekt-comfy", "comfyui-evolution"
      ];

      const projList = projects.map((p, i) => `${i + 1}. ${p}`).join('\n');
      const choice = prompt(`[WHITELIST-VERSCHIEBEN]\nWohin soll '${currentFilename}' verschoben werden?\n\nVerfügbare Projekte:\n${projList}\n\nBitte Projektnamen oder Nummer eingeben:`);
      if (!choice) return;

      let targetProject = choice.trim();
      const num = parseInt(targetProject, 10);
      if (!isNaN(num) && num >= 1 && num <= projects.length) {
        targetProject = projects[num - 1];
      }

      if (!projects.includes(targetProject)) {
        alert(`Ungültiges Projekt: ${targetProject}`);
        return;
      }

      const moveRes = await fetch('/api/gallery/move', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ filename: currentFilename, target_project: targetProject })
      });
      const moveData = await moveRes.json();
      if (moveRes.ok && moveData.success) {
        alert(`✔ Bild erfolgreich nach ${targetProject} verschoben!`);
        const safeId = `gallery-card-${currentFilename.replace(/[^a-zA-Z0-9_-]/g, '_')}`;
        const card = document.getElementById(safeId);
        if (card) card.remove();
        window.navigateLightbox(1);
      } else {
        alert(`Fehler beim Verschieben: ${moveData.error || 'Serverfehler'}`);
      }
    } catch (e) {
      alert(`Netzwerkfehler: ${e.message}`);
    }
  };

  // --- Flaw & Feedback Handlers ---
  window.toggleLightboxFlaw = function(flawKey) {
    if (activeFlaws.has(flawKey)) {
      activeFlaws.delete(flawKey);
    } else {
      activeFlaws.add(flawKey);
    }
    renderFlawChipsUI();
  };

  function renderFlawChipsUI() {
    const container = document.getElementById('lightboxFlawChipsContainer');
    if (!container) return;

    container.querySelectorAll('[data-flaw]').forEach(btn => {
      const key = btn.getAttribute('data-flaw');
      if (activeFlaws.has(key)) {
        btn.classList.add('bg-rose-900', 'border-rose-500', 'text-white');
        btn.classList.remove('bg-slate-900', 'border-slate-800', 'text-slate-300');
      } else {
        btn.classList.remove('bg-rose-900', 'border-rose-500', 'text-white');
        btn.classList.add('bg-slate-900', 'border-slate-800', 'text-slate-300');
      }
    });
  }

  window.submitLightboxFlaws = async function() {
    const feedbackInput = document.getElementById('lightboxFlawFeedback');
    const feedbackText = feedbackInput ? feedbackInput.value.trim() : '';
    const statusMsg = document.getElementById('lightboxFlawStatus');

    if (activeFlaws.size === 0 && !feedbackText) {
      if (statusMsg) {
        statusMsg.textContent = 'Bitte mindestens einen Mangel auswählen oder Feedback eingeben.';
        statusMsg.className = 'text-[10px] text-amber-400 font-mono';
      }
      return;
    }

    const flawsArray = [];
    activeFlaws.forEach(key => {
      const tagStr = FLAW_MAP[key];
      if (tagStr) flawsArray.push(tagStr);
    });

    if (statusMsg) {
      statusMsg.textContent = 'Speichere Feedback...';
      statusMsg.className = 'text-[10px] text-cyan-400 font-mono animate-pulse';
    }

    try {
      const res = await fetch('/api/flaws/submit', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          flaws: flawsArray,
          feedback: feedbackText,
          sync_to_character: true
        })
      });
      if (!res.ok) {
        let errMsg = `HTTP ${res.status}`;
        try {
          const errData = await res.json();
          errMsg = errData.error || errMsg;
        } catch (_) {}
        throw new Error(errMsg);
      }
      const data = await res.json();
      if (data.success) {
        if (statusMsg) {
          statusMsg.textContent = `✔ Korrekturen vermerkt! (${data.total_pending} aktiv in Negativ-Prompt)`;
          statusMsg.className = 'text-[10px] text-emerald-400 font-mono';
        }
        activeFlaws.clear();
        renderFlawChipsUI();
        if (feedbackInput) feedbackInput.value = '';
      } else {
        if (statusMsg) {
          statusMsg.textContent = `Fehler: ${data.error || 'Konnte nicht gespeichert werden'}`;
          statusMsg.className = 'text-[10px] text-rose-400 font-mono';
        }
      }
    } catch (e) {
      if (statusMsg) {
        statusMsg.textContent = `Netzwerkfehler: ${e.message}`;
        statusMsg.className = 'text-[10px] text-rose-400 font-mono';
      }
    }
  };

  // --- Setup on Ready ---
  function setupLightboxEvents() {
    const imgContainer = document.getElementById('lightboxImgContainer');
    const img = document.getElementById('lightboxImg');

    if (imgContainer && img) {
      imgContainer.addEventListener('wheel', e => {
        e.preventDefault();
        const delta = e.deltaY < 0 ? 0.25 : -0.25;
        const prevScale = scale;
        scale = Math.min(5.0, Math.max(1.0, scale + delta));

        if (scale === 1.0) {
          translateX = 0;
          translateY = 0;
        } else if (scale !== prevScale) {
          const rect = imgContainer.getBoundingClientRect();
          const offsetX = e.clientX - rect.left - rect.width / 2;
          const offsetY = e.clientY - rect.top - rect.height / 2;
          const factor = scale / prevScale;
          translateX = translateX * factor;
          translateY = translateY * factor;
        }
        updateTransform();
      }, { passive: false });

      imgContainer.addEventListener('mousedown', e => {
        if (scale <= 1.0) return;
        if (e.button !== 0) return;
        isDragging = true;
        startX = e.clientX - translateX;
        startY = e.clientY - translateY;
        updateTransform();
      });

      window.addEventListener('mousemove', e => {
        if (!isDragging || scale <= 1.0) return;
        translateX = e.clientX - startX;
        translateY = e.clientY - startY;

        const maxBounds = 600 * (scale - 1);
        translateX = Math.max(-maxBounds, Math.min(maxBounds, translateX));
        translateY = Math.max(-maxBounds, Math.min(maxBounds, translateY));

        updateTransform();
      });

      window.addEventListener('mouseup', () => {
        if (isDragging) {
          isDragging = false;
          updateTransform();
        }
      });

      img.addEventListener('dblclick', () => {
        resetZoom();
      });
    }

    window.addEventListener('keydown', e => {
      const lb = document.getElementById('lightbox');
      if (!lb || lb.classList.contains('hidden')) return;

      if (e.key === 'Escape') {
        window.closeLightbox();
      } else if (e.key === 'ArrowLeft') {
        window.navigateLightbox(-1);
      } else if (e.key === 'ArrowRight') {
        window.navigateLightbox(1);
      }
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', setupLightboxEvents);
  } else {
    setupLightboxEvents();
  }
})();
