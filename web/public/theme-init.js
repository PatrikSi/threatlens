(function () {
  var stored = null;

  try {
    stored = window.localStorage.getItem('threatlens.theme');
  } catch (_error) {
    stored = null;
  }

  var mode = stored === 'dark' || (stored && (stored.indexOf('dark-') === 0 || stored.indexOf('theme-dark') === 0)) ? 'dark' : 'light';
  var root = document.documentElement;

  for (var index = root.classList.length - 1; index >= 0; index -= 1) {
    var className = root.classList.item(index);
    if (className && className.indexOf('theme-') === 0) {
      root.classList.remove(className);
    }
  }

  root.classList.toggle('dark', mode === 'dark');
  root.classList.add('theme-' + mode);
  root.dataset.colorMode = mode;
})();
