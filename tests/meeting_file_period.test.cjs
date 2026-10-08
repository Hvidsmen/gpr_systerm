const test = require('node:test');
const assert = require('node:assert/strict');
const {periodFromFilename, applyFilePeriod} = require('../static/planning/meeting_file_period.js');

test('Russian meeting filename fills the full month', () => {
  assert.deepEqual(periodFromFilename('Информация к совещаниям 30.06.2026 г_.xlsx'),
    {start: '2026-06-01', end: '2026-06-30'});
  assert.deepEqual(periodFromFilename('Совещание 15.01.2026.xlsx'),
    {start: '2026-01-01', end: '2026-01-31'});
});

test('February respects leap years and calendar validity', () => {
  assert.equal(periodFromFilename('28.02.2026.xlsx').end, '2026-02-28');
  assert.equal(periodFromFilename('29.02.2024.xlsx').end, '2024-02-29');
  assert.equal(periodFromFilename('29.02.2000.xlsx').end, '2000-02-29');
  for (const filename of ['29.02.2026.xlsx', '29.02.2100.xlsx', '31.04.2026.xlsx', '00.06.2026.xlsx', '30.13.2026.xlsx']) {
    assert.equal(periodFromFilename(filename), null);
  }
});

test('Missing or ambiguous dates do not silently choose a month', () => {
  for (const filename of ['Совещание.xlsx', '2026.xlsx', '30.06.2026 — 31.07.2026.xlsx', '130.06.2026.xlsx', '30.06.20260.xlsx']) {
    assert.equal(periodFromFilename(filename), null);
  }
  assert.deepEqual(periodFromFilename('01.06.2026 — 30.06.2026.xlsx'),
    {start: '2026-06-01', end: '2026-06-30'});
});

test('Each file has independent dates and manual periods survive unrecognized names', () => {
  const first = {start: {value: ''}, end: {value: ''}};
  const second = {start: {value: '2026-01-10'}, end: {value: '2026-02-15'}};
  assert.equal(applyFilePeriod('30.06.2026.xlsx', first.start, first.end), true);
  assert.equal(applyFilePeriod('unknown.xlsx', second.start, second.end), false);
  assert.equal(second.start.value, '2026-01-10');
  assert.equal(second.end.value, '2026-02-15');
  assert.equal(applyFilePeriod('31.07.2026.xlsx', second.start, second.end), true);
  assert.equal(first.start.value, '2026-06-01');
  assert.equal(second.end.value, '2026-07-31');
  assert.equal(applyFilePeriod('30.09.2026.xlsx', first.start, first.end), true);
  assert.equal(first.end.value, '2026-09-30');
});

test('delegated change handling fills newly added rows and leaves other rows alone', () => {
  let change;
  const initial = {start: {value: '2026-01-01'}, end: {value: '2026-01-31'}};
  const added = {start: {value: ''}, end: {value: ''}};
  const scopeFor = fields => ({querySelector: selector => selector.includes('"start"') ? fields.start : fields.end});
  const form = {...scopeFor(initial), addEventListener: (name, callback) => {change = callback;}};
  const parent = {querySelector: () => null, appendChild: () => {}};
  const script = require.resolve('../static/planning/meeting_file_period.js');
  global.document = {
    querySelectorAll: () => [form],
    createElement: () => ({dataset: {}, setAttribute: () => {}}),
  };
  try {
    delete require.cache[script];
    require(script);
    change({target: {type: 'file', files: [{name: '30.06.2026.xlsx'}],
                     closest: () => scopeFor(added), parentElement: parent}});
    assert.equal(added.start.value, '2026-06-01');
    assert.equal(added.end.value, '2026-06-30');
    assert.equal(initial.start.value, '2026-01-01');
    added.start.value = '2026-06-10';
    change({target: {type: 'date'}});
    assert.equal(added.start.value, '2026-06-10');
    change({target: {type: 'file', files: [{name: '28.02.2026.xlsx'}],
                     closest: () => null, parentElement: parent}});
    assert.equal(initial.end.value, '2026-02-28');
    assert.equal(added.start.value, '2026-06-10');
  } finally {
    delete global.document;
    delete require.cache[script];
  }
});
