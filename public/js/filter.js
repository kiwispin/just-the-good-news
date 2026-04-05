// Client-side category filtering for Just The Good News
(function () {
  'use strict';

  var grid = document.getElementById('article-grid');
  var noResults = document.getElementById('no-results');
  if (!grid) return;

  var buttons = document.querySelectorAll('.filter-btn');
  var cards = grid.querySelectorAll('.article-card');
  var activeFilter = 'all';

  function applyFilter(filter) {
    activeFilter = filter;
    var visible = 0;

    cards.forEach(function (card) {
      if (filter === 'all') {
        card.classList.remove('hidden');
        visible++;
      } else {
        var cats = (card.getAttribute('data-categories') || '').trim().split(/\s+/);
        if (cats.indexOf(filter) !== -1) {
          card.classList.remove('hidden');
          visible++;
        } else {
          card.classList.add('hidden');
        }
      }
    });

    if (noResults) {
      noResults.style.display = visible === 0 ? 'block' : 'none';
    }
  }

  buttons.forEach(function (btn) {
    btn.addEventListener('click', function () {
      var filter = btn.getAttribute('data-filter');

      // Update active state
      buttons.forEach(function (b) {
        b.classList.remove('active');
        b.setAttribute('aria-pressed', 'false');
      });
      btn.classList.add('active');
      btn.setAttribute('aria-pressed', 'true');

      applyFilter(filter);
    });
  });
})();
