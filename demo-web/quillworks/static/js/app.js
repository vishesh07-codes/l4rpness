// Quillworks UI helpers
document.addEventListener('keydown', function (e) {
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
    var input = document.querySelector('.quicksearch input');
    if (input) { e.preventDefault(); input.focus(); }
  }
});

// Close the workspace menu when clicking outside of it.
document.addEventListener('click', function (e) {
  document.querySelectorAll('.wsmenu').forEach(function (menu) {
    if (!menu.contains(e.target)) menu.classList.remove('open');
  });
});

// Autosize textareas with the data-grow attribute.
document.querySelectorAll('textarea[data-grow]').forEach(function (el) {
  var grow = function () { el.style.height = 'auto'; el.style.height = el.scrollHeight + 'px'; };
  el.addEventListener('input', grow); grow();
});
