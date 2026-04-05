// Pagefind-powered search for Just The Good News
(function () {
  'use strict';

  var overlay   = document.getElementById('search-overlay');
  var openBtn   = document.getElementById('search-btn');
  var closeBtn  = document.getElementById('search-close');
  var input     = document.getElementById('search-input');
  var results   = document.getElementById('search-results');
  var hint      = document.getElementById('search-hint');

  if (!overlay || !openBtn) return;

  var pagefind      = null;
  var searchTimeout = null;
  var pagefindSrc   = overlay.dataset.pagefind;

  // ── Load Pagefind lazily on first open ───────────────────────────────────
  function loadPagefind() {
    if (pagefind !== null) return;
    import(pagefindSrc)
      .then(function (pf) {
        pagefind = pf;
        // If user already typed while loading, run search now
        if (input.value.trim()) runSearch(input.value);
      })
      .catch(function () {
        // Pagefind index not present (local dev without a prior build)
        pagefind = false;
      });
  }

  // ── Open / close ─────────────────────────────────────────────────────────
  function openSearch() {
    overlay.removeAttribute('hidden');
    document.body.style.overflow = 'hidden';
    input.focus();
    input.select();
    loadPagefind();
  }

  function closeSearch() {
    overlay.setAttribute('hidden', '');
    document.body.style.overflow = '';
    input.value = '';
    results.innerHTML = '';
    if (hint) hint.style.display = '';
  }

  // ── Search ────────────────────────────────────────────────────────────────
  function runSearch(query) {
    query = query.trim();
    if (!query) {
      results.innerHTML = '';
      if (hint) hint.style.display = '';
      return;
    }
    if (hint) hint.style.display = 'none';

    if (pagefind === false) {
      results.innerHTML = '<p class="search-status">Search index not available in local preview.<br>It will work on the live site after deployment.</p>';
      return;
    }
    if (!pagefind) {
      results.innerHTML = '<p class="search-status">Loading search…</p>';
      return;
    }

    pagefind.search(query).then(function (search) {
      if (!search.results.length) {
        results.innerHTML = '<p class="search-status">No results for &ldquo;' + escapeHtml(query) + '&rdquo;</p>';
        return;
      }
      // Fetch up to 8 result data objects in parallel
      Promise.all(search.results.slice(0, 8).map(function (r) { return r.data(); }))
        .then(function (items) {
          results.innerHTML = items.map(function (item) {
            return (
              '<a href="' + item.url + '" class="search-result" role="listitem">' +
                '<div class="search-result-title">' + escapeHtml(item.meta.title || 'Untitled') + '</div>' +
                '<div class="search-result-excerpt">' + item.excerpt + '</div>' +
              '</a>'
            );
          }).join('');
        });
    });
  }

  function escapeHtml(str) {
    return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  // ── Events ────────────────────────────────────────────────────────────────
  openBtn.addEventListener('click', openSearch);
  closeBtn.addEventListener('click', closeSearch);

  // Backdrop click closes
  overlay.addEventListener('click', function (e) {
    if (e.target === overlay) closeSearch();
  });

  // Debounced input
  input.addEventListener('input', function () {
    clearTimeout(searchTimeout);
    searchTimeout = setTimeout(function () { runSearch(input.value); }, 220);
  });

  // Keyboard shortcuts
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') { closeSearch(); return; }
    if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
      e.preventDefault();
      overlay.hasAttribute('hidden') ? openSearch() : closeSearch();
    }
  });

  // Close search results when navigating to an article
  results.addEventListener('click', function () { closeSearch(); });
})();
