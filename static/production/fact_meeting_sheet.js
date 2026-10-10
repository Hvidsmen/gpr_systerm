(() => {
  const object = document.getElementById('id_construction_object');
  const sheet = document.getElementById('id_sheet_name');
  if (!object || !sheet) return;
  const name = () => object.value ? object.selectedOptions[0].textContent.trim() : '';
  let previous = name();
  if (!sheet.value) sheet.value = previous;
  object.addEventListener('change', () => {
    if (!sheet.value || sheet.value === previous) sheet.value = name();
    previous = name();
  });
})();
