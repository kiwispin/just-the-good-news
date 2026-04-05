// Client-side category + region filtering for Just The Good News
(function () {
  'use strict';

  var grid      = document.getElementById('article-grid');
  var noResults = document.getElementById('no-results');
  if (!grid) return;

  var cards           = grid.querySelectorAll('.article-card');
  var activeCategory  = 'all';
  var activeRegion    = 'all';

  // ── Region dropdown elements ─────────────────────────────────────────────
  var regionToggle   = document.getElementById('region-toggle');
  var regionDropdown = document.getElementById('region-dropdown');
  var regionLabelEl  = document.getElementById('region-label-text');

  // ── Filtering logic ──────────────────────────────────────────────────────
  function applyFilters() {
    var visible = 0;
    cards.forEach(function (card) {
      var catMatch = activeCategory === 'all' ||
        (card.getAttribute('data-categories') || '').trim().split(/\s+/).indexOf(activeCategory) !== -1;
      var regionMatch = activeRegion === 'all' ||
        (card.getAttribute('data-region') || '').trim() === activeRegion;

      if (catMatch && regionMatch) {
        card.classList.remove('hidden');
        visible++;
      } else {
        card.classList.add('hidden');
      }
    });

    if (noResults) {
      noResults.style.display = visible === 0 ? 'block' : 'none';
    }
  }

  // ── Category pills ───────────────────────────────────────────────────────
  document.querySelectorAll('.cat-btn').forEach(function (btn) {
    btn.addEventListener('click', function () {
      activeCategory = btn.getAttribute('data-category');
      document.querySelectorAll('.cat-btn').forEach(function (b) {
        b.classList.remove('active');
        b.setAttribute('aria-pressed', 'false');
      });
      btn.classList.add('active');
      btn.setAttribute('aria-pressed', 'true');
      applyFilters();
    });
  });

  // ── Region dropdown toggle ───────────────────────────────────────────────
  function openDropdown() {
    regionDropdown.removeAttribute('hidden');
    regionToggle.setAttribute('aria-expanded', 'true');
  }

  function closeDropdown() {
    regionDropdown.setAttribute('hidden', '');
    regionToggle.setAttribute('aria-expanded', 'false');
  }

  function toggleDropdown() {
    if (regionDropdown.hasAttribute('hidden')) {
      openDropdown();
    } else {
      closeDropdown();
    }
  }

  if (regionToggle && regionDropdown) {
    regionToggle.addEventListener('click', function (e) {
      e.stopPropagation();
      toggleDropdown();
    });

    // Close on outside click
    document.addEventListener('click', function (e) {
      if (!regionDropdown.hasAttribute('hidden') &&
          !regionDropdown.contains(e.target) &&
          e.target !== regionToggle) {
        closeDropdown();
      }
    });

    // Close on Escape
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && !regionDropdown.hasAttribute('hidden')) {
        closeDropdown();
        regionToggle.focus();
      }
    });

    // ── Region option buttons ──────────────────────────────────────────────
    document.querySelectorAll('.region-btn').forEach(function (btn) {
      btn.addEventListener('click', function () {
        activeRegion = btn.getAttribute('data-region');

        // Update button states
        document.querySelectorAll('.region-btn').forEach(function (b) {
          b.classList.remove('active');
          b.setAttribute('aria-selected', 'false');
        });
        btn.classList.add('active');
        btn.setAttribute('aria-selected', 'true');

        // Update toggle label & visual state
        if (regionLabelEl) {
          regionLabelEl.textContent = btn.textContent.trim();
        }
        if (activeRegion === 'all') {
          regionToggle.classList.remove('has-region');
        } else {
          regionToggle.classList.add('has-region');
        }

        closeDropdown();
        applyFilters();
      });
    });
  }
})();
