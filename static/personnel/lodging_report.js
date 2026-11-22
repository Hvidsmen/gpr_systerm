(() => {
  'use strict';
  const report = document.querySelector('.lodging-report-groups');
  if (!report) return;
  report.addEventListener('click', event => {
    const button = event.target.closest('[data-lodging-report-expand]');
    if (!button) return;
    const open = button.dataset.lodgingReportExpand === '1';
    report.querySelectorAll('.lodging-report-group').forEach(group => {group.open = open;});
  });
})();
