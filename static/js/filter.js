// Client-side category + region + kids filtering for Just The Good News
(function () {
  'use strict';

  var grid      = document.getElementById('article-grid');
  var noResults = document.getElementById('no-results');
  if (!grid) return;

  var cards           = Array.from(grid.querySelectorAll('.article-card'));
  var activeCategory  = 'all';
  var activeRegion    = 'all';
  var kidsMode        = false;

  // ── Element refs ─────────────────────────────────────────────────────────
  var regionToggle   = document.getElementById('region-toggle');
  var regionDropdown = document.getElementById('region-dropdown');
  var regionLabelEl  = document.getElementById('region-label-text');
  var kidsToggle     = document.getElementById('kids-toggle');
  var kidsBanner     = document.getElementById('kids-mode-banner');
  var kidsExit       = document.getElementById('kids-mode-exit');
  var navKids        = document.getElementById('nav-kids');

  // ── Filtering logic ──────────────────────────────────────────────────────
  function applyFilters() {
    var visible = 0;

    cards.forEach(function (card) {
      // Kids mode: only show articles tagged kids: true
      if (kidsMode && card.getAttribute('data-kids') !== 'true') {
        card.classList.add('hidden');
        return;
      }

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

  // ── Summary swap ─────────────────────────────────────────────────────────
  function swapSummaries(showKids) {
    cards.forEach(function (card) {
      var regular = card.querySelector('.js-summary');
      var kids    = card.querySelector('.js-kids-summary');
      if (!regular) return;
      if (showKids && kids) {
        regular.setAttribute('hidden', '');
        kids.removeAttribute('hidden');
      } else {
        regular.removeAttribute('hidden');
        if (kids) kids.setAttribute('hidden', '');
      }
    });
  }

  // ── Kids mode on/off ─────────────────────────────────────────────────────
  function activateKids() {
    kidsMode = true;
    if (kidsToggle) {
      kidsToggle.classList.add('active');
      kidsToggle.setAttribute('aria-pressed', 'true');
    }
    document.body.classList.add('kids-mode');
    if (kidsBanner) kidsBanner.removeAttribute('hidden');
    swapSummaries(true);
    applyFilters();
  }

  function deactivateKids() {
    kidsMode = false;
    if (kidsToggle) {
      kidsToggle.classList.remove('active');
      kidsToggle.setAttribute('aria-pressed', 'false');
    }
    document.body.classList.remove('kids-mode');
    if (kidsBanner) kidsBanner.setAttribute('hidden', '');
    swapSummaries(false);
    applyFilters();
    if (window.location.hash === '#kids') {
      history.replaceState(null, '', window.location.pathname + window.location.search);
    }
  }

  // ── Kids toggle button ───────────────────────────────────────────────────
  if (kidsToggle) {
    kidsToggle.addEventListener('click', function () {
      if (kidsMode) {
        deactivateKids();
      } else {
        activateKids();
        history.replaceState(null, '', '#kids');
      }
    });
  }

  // Kids mode exit banner button
  if (kidsExit) {
    kidsExit.addEventListener('click', deactivateKids);
  }

  // Kids nav link — if we're on the homepage intercept and toggle;
  // otherwise let the href="#kids" navigate normally.
  if (navKids) {
    navKids.addEventListener('click', function (e) {
      if (grid) { // grid present = homepage
        e.preventDefault();
        if (kidsMode) {
          deactivateKids();
        } else {
          activateKids();
          history.replaceState(null, '', '#kids');
          // Scroll filter bar into view
          var filterBar = document.querySelector('.filter-bar');
          if (filterBar) filterBar.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        }
      }
    });
  }

  // Activate kids mode on page load if hash is present
  if (window.location.hash === '#kids') {
    activateKids();
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

  if (regionToggle && regionDropdown) {
    regionToggle.addEventListener('click', function (e) {
      e.stopPropagation();
      regionDropdown.hasAttribute('hidden') ? openDropdown() : closeDropdown();
    });

    document.addEventListener('click', function (e) {
      if (!regionDropdown.hasAttribute('hidden') &&
          !regionDropdown.contains(e.target) &&
          e.target !== regionToggle) {
        closeDropdown();
      }
    });

    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && !regionDropdown.hasAttribute('hidden')) {
        closeDropdown();
        regionToggle.focus();
      }
    });

    document.querySelectorAll('.region-btn').forEach(function (btn) {
      btn.addEventListener('click', function () {
        activeRegion = btn.getAttribute('data-region');
        document.querySelectorAll('.region-btn').forEach(function (b) {
          b.classList.remove('active');
          b.setAttribute('aria-selected', 'false');
        });
        btn.classList.add('active');
        btn.setAttribute('aria-selected', 'true');
        if (regionLabelEl) regionLabelEl.textContent = btn.textContent.trim();
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
