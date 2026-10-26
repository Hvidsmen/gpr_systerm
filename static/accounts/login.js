(() => {
  const button = document.querySelector('[data-password-toggle]');
  if (!button) return;
  const password = document.getElementById(button.getAttribute('aria-controls'));
  if (!password) return;
  button.hidden = false;
  button.addEventListener('click', () => {
    const show = password.type === 'password';
    password.type = show ? 'text' : 'password';
    button.textContent = show ? 'Скрыть' : 'Показать';
    button.setAttribute('aria-label', show ? 'Скрыть пароль' : 'Показать пароль');
    button.setAttribute('aria-pressed', String(show));
  });
})();
