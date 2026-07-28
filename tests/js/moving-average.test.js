'use strict';

// movingAverage 단위 테스트.
// 이 함수가 존재하는 이유는 "수집 공백"이다 — 인덱스 기준 N개 평균이 아니라
// 달력 기준 최근 windowDays일만 묶어야, 며칠 건너뛴 데이터가 과거 값을
// 현재 구간으로 끌고 들어오지 않는다.

const test = require('node:test');
const assert = require('node:assert/strict');

const { movingAverage } = require('../../js/lib/shared.js');

function series(pairs) {
  return pairs.map(([date, median]) => ({ date, median }));
}

test('movingAverage: 수집 공백이 있으면 창 밖 과거 값을 섞지 않는다', () => {
  // 1/2 다음 관측이 1/20이다. 인덱스 기준 3개 평균이라면 (100+110+200)/3 =
  // 136.67 이 되지만, 달력 기준 7일 창에서는 1/20 하나만 남아야 한다.
  const data = series([
    ['2026-01-01', 100],
    ['2026-01-02', 110],
    ['2026-01-20', 200],
  ]);
  assert.deepEqual(movingAverage(data, 7), [100, 105, 200]);
});

test('movingAverage: 창 경계는 (windowDays - 1)일 전까지 포함(양끝 포함)', () => {
  // 기준일 2026-03-01, 7일 창 → 2026-02-23 부터 포함.
  const data = series([
    ['2026-02-22', 10], // 창 밖
    ['2026-02-23', 20], // 창 경계 — 포함
    ['2026-03-01', 30],
  ]);
  const result = movingAverage(data, 7);
  assert.equal(result[2], 25); // (30 + 20) / 2
});

test('movingAverage: 연속 데이터에서 창이 가득 차면 최근 7개만 평균', () => {
  const data = series([
    ['2026-01-01', 1],
    ['2026-01-02', 2],
    ['2026-01-03', 3],
    ['2026-01-04', 4],
    ['2026-01-05', 5],
    ['2026-01-06', 6],
    ['2026-01-07', 7],
    ['2026-01-08', 8],
    ['2026-01-09', 9],
    ['2026-01-10', 10],
  ]);
  const result = movingAverage(data, 7);
  assert.equal(result[0], 1); // 창에 자기 자신뿐
  assert.equal(result[3], 2.5); // (1+2+3+4)/4
  assert.equal(result[6], 4); // (1..7)/7
  assert.equal(result[7], 5); // (2..8)/7
  assert.equal(result[9], 7); // (4..10)/7
  assert.equal(result.length, data.length);
});

test('movingAverage: null median은 평균에서 제외하고, 전부 null이면 null', () => {
  const data = series([
    ['2026-01-01', null],
    ['2026-01-02', 100],
    ['2026-01-03', null],
    ['2026-01-04', 200],
  ]);
  assert.deepEqual(movingAverage(data, 7), [null, 100, 100, 150]);
});

test('movingAverage: 창 안이 전부 null이면 해당 지점은 null', () => {
  const data = series([
    ['2026-01-01', 100],
    ['2026-01-20', null],
  ]);
  assert.deepEqual(movingAverage(data, 7), [100, null]);
});

test('movingAverage: 소수 둘째 자리로 반올림', () => {
  const data = series([
    ['2026-01-01', 1],
    ['2026-01-02', 2],
    ['2026-01-03', 2],
  ]);
  // (1 + 2 + 2) / 3 = 1.6666... → 1.67
  assert.deepEqual(movingAverage(data, 7), [1, 1.5, 1.67]);
});

test('movingAverage: 빈 배열과 단일 항목', () => {
  assert.deepEqual(movingAverage([], 7), []);
  assert.deepEqual(movingAverage(series([['2026-01-01', 42]]), 7), [42]);
});

test('movingAverage: windowDays=1이면 그날 값만 본다', () => {
  const data = series([
    ['2026-01-01', 100],
    ['2026-01-02', 200],
  ]);
  assert.deepEqual(movingAverage(data, 1), [100, 200]);
});

test('movingAverage: 월/연 경계를 UTC로 계산한다 (로컬 타임존 무관)', () => {
  const data = series([
    ['2025-12-25', 10], // 창 밖
    ['2025-12-26', 20], // 2026-01-01 기준 7일 창의 경계
    ['2026-01-01', 30],
  ]);
  const expected = [10, 15, 25];

  const original = process.env.TZ;
  try {
    for (const tz of ['UTC', 'Pacific/Kiritimati', 'Etc/GMT+12', 'Asia/Seoul']) {
      process.env.TZ = tz;
      assert.deepEqual(movingAverage(data, 7), expected, `TZ=${tz}`);
    }
  } finally {
    if (original === undefined) delete process.env.TZ;
    else process.env.TZ = original;
  }
});
