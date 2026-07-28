'use strict';

// filterByPeriod 단위 테스트.
// 기간 버튼(30/90/180일, 전체)이 쓰는 함수다. 기준일은 "오늘"이 아니라
// 마지막 데이터 포인트이고, 경계 계산은 전부 UTC로 해야 한다.

const test = require('node:test');
const assert = require('node:assert/strict');

const { filterByPeriod } = require('../../js/lib/shared.js');

function series(dates) {
  return dates.map((date) => ({ date, median: 100 }));
}

test('filterByPeriod: days=0은 전체 기간(원본 그대로)', () => {
  const data = series(['2026-01-01', '2026-02-01', '2026-03-01']);
  assert.equal(filterByPeriod(data, 0), data);
});

test('filterByPeriod: days가 없으면(null/undefined/NaN) 전체 기간', () => {
  const data = series(['2026-01-01', '2026-02-01']);
  assert.equal(filterByPeriod(data, null), data);
  assert.equal(filterByPeriod(data, undefined), data);
  assert.equal(filterByPeriod(data, NaN), data);
});

test('filterByPeriod: 빈 배열은 빈 배열', () => {
  const empty = [];
  assert.equal(filterByPeriod(empty, 30), empty);
  assert.equal(filterByPeriod(empty, 0), empty);
});

test('filterByPeriod: 기준일은 마지막 항목이며 경계일은 포함한다', () => {
  const data = series([
    '2026-01-07', // 경계 밖
    '2026-01-08', // 경계 (2026-01-15 - 7일) — 포함
    '2026-01-09',
    '2026-01-15',
  ]);
  const result = filterByPeriod(data, 7);
  assert.deepEqual(
    result.map((e) => e.date),
    ['2026-01-08', '2026-01-09', '2026-01-15']
  );
});

test('filterByPeriod: 기준일은 "오늘"이 아니라 데이터의 마지막 날짜다', () => {
  // 데이터가 통째로 과거여도 마지막 항목 기준으로 잘라야 한다
  // (오늘 기준이면 전부 사라져 차트가 비어버린다).
  const data = series(['2020-01-01', '2020-06-01', '2020-06-25', '2020-06-30']);
  const result = filterByPeriod(data, 30);
  assert.deepEqual(
    result.map((e) => e.date),
    ['2020-06-01', '2020-06-25', '2020-06-30']
  );
});

test('filterByPeriod: 원본 배열을 변형하지 않고 새 배열을 만든다', () => {
  const data = series(['2026-01-01', '2026-01-10', '2026-01-15']);
  const before = data.map((e) => e.date);
  const result = filterByPeriod(data, 7);
  assert.notEqual(result, data);
  assert.deepEqual(data.map((e) => e.date), before);
});

test('filterByPeriod: 연/월 경계와 윤년을 UTC 기준으로 넘는다', () => {
  const crossYear = series(['2025-12-28', '2025-12-31', '2026-01-01', '2026-01-03']);
  assert.deepEqual(
    filterByPeriod(crossYear, 3).map((e) => e.date),
    ['2025-12-31', '2026-01-01', '2026-01-03']
  );

  // 2024는 윤년이라 3/1의 30일 전은 1/31이다.
  const leap = series(['2024-01-30', '2024-01-31', '2024-02-29', '2024-03-01']);
  assert.deepEqual(
    filterByPeriod(leap, 30).map((e) => e.date),
    ['2024-01-31', '2024-02-29', '2024-03-01']
  );
});

test('filterByPeriod: 로컬 타임존이 바뀌어도 경계가 하루 밀리지 않는다', () => {
  const data = series(['2026-01-07', '2026-01-08', '2026-01-09', '2026-01-15']);
  const expected = ['2026-01-08', '2026-01-09', '2026-01-15'];

  const original = process.env.TZ;
  try {
    for (const tz of ['UTC', 'Pacific/Kiritimati', 'Etc/GMT+12', 'Asia/Seoul']) {
      process.env.TZ = tz;
      assert.deepEqual(
        filterByPeriod(data, 7).map((e) => e.date),
        expected,
        `TZ=${tz}`
      );
    }
  } finally {
    if (original === undefined) delete process.env.TZ;
    else process.env.TZ = original;
  }
});

test('filterByPeriod: 창이 전체 데이터보다 길면 전부 남는다', () => {
  const data = series(['2026-01-01', '2026-01-15']);
  assert.deepEqual(filterByPeriod(data, 3650).map((e) => e.date), ['2026-01-01', '2026-01-15']);
});
