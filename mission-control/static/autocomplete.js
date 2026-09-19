/**
 * autocomplete.js - Danbooru Tag Autocomplete Engine
 * Outer Heaven Mission Control Dashboard
 *
 * Requirements:
 *   - Systemweites Autocomplete für Prompts
 *   - Absolut KEINE Veränderungen am Layout oder Styling der Textareas
 *   - Floating Dropdown an body angehängt, exakt am Eingabefokus orientiert
 *   - Tastatur-Navigation: Pfeiltasten (Hoch/Runter), Enter/Tab zum Einfügen, Escape zum Schließen
 */

(() => {
  let dropdownEl = null;
  let activeTextarea = null;
  let currentSuggestions = [];
  let selectedIndex = 0;
  let debounceTimer = null;
  let currentQuery = '';
  let tokenStart = 0;
  let tokenEnd = 0;

  function createDropdown() {
    if (document.getElementById('danbooruAutocompleteDropdown')) {
      dropdownEl = document.getElementById('danbooruAutocompleteDropdown');
      return;
    }
    dropdownEl = document.createElement('div');
    dropdownEl.id = 'danbooruAutocompleteDropdown';
    dropdownEl.className = 'fixed z-[9999] hidden bg-slate-950/95 border border-cyan-500/60 rounded-lg shadow-2xl overflow-y-auto max-h-64 w-80 text-xs font-mono backdrop-blur-md divide-y divide-slate-800/80';
    document.body.appendChild(dropdownEl);

    // Prevent losing focus on mousedown
    dropdownEl.addEventListener('mousedown', e => {
      e.preventDefault();
    });
  }

  function formatCount(count) {
    if (!count) return '';
    if (count >= 1000000) return (count / 1000000).toFixed(1) + 'M';
    if (count >= 1000) return (count / 1000).toFixed(0) + 'k';
    return count.toString();
  }

  function getCategoryColor(cat) {
    // 0 = general, 1 = artist, 3 = copyright, 4 = character, 5 = meta
    switch (cat) {
      case 1: return 'text-rose-400';
      case 3: return 'text-purple-400';
      case 4: return 'text-emerald-400';
      case 5: return 'text-amber-400';
      default: return 'text-cyan-300';
    }
  }

  function renderDropdown() {
    if (!dropdownEl || currentSuggestions.length === 0) {
      hideDropdown();
      return;
    }

    dropdownEl.innerHTML = '';
    currentSuggestions.forEach((item, idx) => {
      const row = document.createElement('div');
      row.className = `px-3 py-1.5 flex items-center justify-between cursor-pointer transition ${
        idx === selectedIndex ? 'bg-cyan-950/90 text-white font-bold border-l-2 border-cyan-400' : 'hover:bg-slate-900 text-slate-300'
      }`;
      const catColor = getCategoryColor(item.category);
      row.innerHTML = `
        <span class="truncate ${catColor}">${item.tag.replace(/_/g, ' ')}</span>
        <span class="text-[10px] text-slate-500 font-mono ml-2 shrink-0">${formatCount(item.count)}</span>
      `;
      row.addEventListener('click', () => {
        applySuggestion(item.tag);
      });
      dropdownEl.appendChild(row);
    });

    positionDropdown();
    dropdownEl.classList.remove('hidden');
  }

  function positionDropdown() {
    if (!dropdownEl || !activeTextarea) return;
    const rect = activeTextarea.getBoundingClientRect();

    // Position floating dropdown directly below active textarea, clamped to window
    const top = rect.bottom + window.scrollY + 4;
    const left = Math.max(10, Math.min(rect.left + window.scrollX, window.innerWidth - 340));

    dropdownEl.style.top = `${top}px`;
    dropdownEl.style.left = `${left}px`;
  }

  function hideDropdown() {
    if (dropdownEl) {
      dropdownEl.classList.add('hidden');
      dropdownEl.innerHTML = '';
    }
    currentSuggestions = [];
    selectedIndex = 0;
  }

  function extractCurrentToken(ta) {
    const val = ta.value;
    const caret = ta.selectionStart;
    if (caret === undefined) return null;

    // Find boundaries of the current token (separated by comma or newline)
    let start = caret;
    while (start > 0 && val[start - 1] !== ',' && val[start - 1] !== '\n') {
      start--;
    }

    let end = caret;
    while (end < val.length && val[end] !== ',' && val[end] !== '\n') {
      end++;
    }

    tokenStart = start;
    tokenEnd = end;

    const rawToken = val.substring(start, caret);
    // Strip leading weight brackets or whitespace
    const cleanToken = rawToken.replace(/^[\s\(\[\{]+/, '').trim();
    return cleanToken;
  }

  function applySuggestion(tag) {
    if (!activeTextarea) return;
    const val = activeTextarea.value;
    const cleanTag = tag.replace(/_/g, ' ');

    // Replace from tokenStart to tokenEnd
    const before = val.substring(0, tokenStart);
    const after = val.substring(tokenEnd);

    // Keep leading comma/space spacing clean
    const prefix = (before.length > 0 && !before.endsWith(' ') && !before.endsWith('\n')) ? ' ' : '';
    const newText = before + prefix + cleanTag + ', ' + after.replace(/^[\s,]+/, '');

    activeTextarea.value = newText;
    const newCaret = (before + prefix + cleanTag + ', ').length;
    activeTextarea.setSelectionRange(newCaret, newCaret);
    activeTextarea.focus();

    // Trigger input event so any character counters update
    activeTextarea.dispatchEvent(new Event('input', { bubbles: true }));

    hideDropdown();
  }

  async function fetchSuggestions(query) {
    if (!query || query.length < 2) {
      hideDropdown();
      return;
    }
    currentQuery = query;

    try {
      const res = await fetch(`/api/danbooru/tags?q=${encodeURIComponent(query)}&limit=12`);
      if (!res.ok) return;
      const data = await res.json();
      if (data.success && Array.isArray(data.tags)) {
        currentSuggestions = data.tags;
        selectedIndex = 0;
        if (currentSuggestions.length > 0) {
          renderDropdown();
        } else {
          hideDropdown();
        }
      }
    } catch (e) {
      console.warn('Autocomplete fetch error:', e);
    }
  }

  function handleInput(e) {
    activeTextarea = e.target;
    const token = extractCurrentToken(activeTextarea);

    clearTimeout(debounceTimer);
    if (!token || token.length < 2) {
      hideDropdown();
      return;
    }

    debounceTimer = setTimeout(() => {
      fetchSuggestions(token);
    }, 140);
  }

  function handleKeydown(e) {
    if (!dropdownEl || dropdownEl.classList.contains('hidden') || currentSuggestions.length === 0) {
      return;
    }

    if (e.key === 'ArrowDown') {
      e.preventDefault();
      selectedIndex = (selectedIndex + 1) % currentSuggestions.length;
      renderDropdown();
      ensureItemVisible();
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      selectedIndex = (selectedIndex - 1 + currentSuggestions.length) % currentSuggestions.length;
      renderDropdown();
      ensureItemVisible();
    } else if (e.key === 'Enter' || e.key === 'Tab') {
      e.preventDefault();
      if (currentSuggestions[selectedIndex]) {
        applySuggestion(currentSuggestions[selectedIndex].tag);
      }
    } else if (e.key === 'Escape') {
      e.preventDefault();
      hideDropdown();
    }
  }

  function ensureItemVisible() {
    if (!dropdownEl) return;
    const activeItem = dropdownEl.children[selectedIndex];
    if (activeItem) {
      activeItem.scrollIntoView({ block: 'nearest' });
    }
  }

  function attachToInputs() {
    createDropdown();

    // Select all prompt related text inputs and textareas
    const targetSelectors = [
      '#renderPrompt',
      '#renderNegativePrompt',
      '#meganPositivePrompt',
      '#meganNegativePrompt',
      '#newPresetText',
      '#appPresetBody',
      '#appPresetFace',
      '#appPresetFeatures',
      '#botPositivePrompt',
      '#botNegativePrompt',
      'textarea[id*="prompt" i]',
      'textarea[id*="Prompt" i]',
      'input[id*="prompt" i]',
      '.danbooru-autocomplete'
    ];

    document.querySelectorAll(targetSelectors.join(', ')).forEach(el => {
      if (el.dataset.danbooruBound) return;
      el.dataset.danbooruBound = 'true';

      el.addEventListener('input', handleInput);
      el.addEventListener('keydown', handleKeydown);
      el.addEventListener('blur', () => {
        setTimeout(() => {
          hideDropdown();
        }, 220);
      });
    });
  }

  window.addEventListener('resize', positionDropdown);
  window.addEventListener('scroll', positionDropdown, true);

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', attachToInputs);
  } else {
    attachToInputs();
  }

  // Re-attach periodically for dynamically created modals
  setInterval(attachToInputs, 2000);
})();
