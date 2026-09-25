// External script (not inline) so it works under a strict
// Content-Security-Policy that disallows inline event handlers.

document.addEventListener('DOMContentLoaded', function () {

    // Clickable table rows: <tr data-href="/email/123">
    document.querySelectorAll('tr[data-href]').forEach(function (row) {
        row.style.cursor = 'pointer';
        row.addEventListener('click', function (event) {
            // Don't hijack clicks on links/buttons/inputs inside the row.
            if (event.target.closest('a, button, input, textarea, select, label')) {
                return;
            }
            window.location.href = row.dataset.href;
        });
    });

    // Confirmation prompt before submitting a form:
    // <form data-confirm="Send reviewed email?">
    document.querySelectorAll('form[data-confirm]').forEach(function (form) {
        form.addEventListener('submit', function (event) {
            if (!window.confirm(form.dataset.confirm)) {
                event.preventDefault();
            }
        });
    });

});
