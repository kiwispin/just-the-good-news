// Client-side category + region filtering for Just The Good News
(function () {
  'use strict';

  var grid = document.getElementById('article-grid');
  var noResults = document.getElementById('no-results');
  if (!grid) return;

  var cards = grid.querySelectorAll('.article-card');
  var activeCategory = 'all';
  var activeRegion = 'all';

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

  // Category buttons
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

  // Region buttons
  document.querySelectorAll('.region-btn').forEach(function (btn) {
    btn.addEventListener('click', function () {
      activeRegion = btn.getAttribute('data-region');
      document.querySelectorAll('.region-btn').forEach(function (b) {
        b.classList.remove('active');
        b.setAttribute('aria-pressed', 'false');
      });
      btn.classList.add('active');
      btn.setAttribute('aria-pressed', 'true');
      applyFilters();
    });
  });
})();
