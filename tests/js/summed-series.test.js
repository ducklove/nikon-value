'use strict';

// buildSummedSeries 단위 테스트 (관심 목록 가치 대시보드의 합산 추이).
//
// 주의: 아래 "전 제품 값 보유" 테스트들은 현재 동작을 그대로 고정한 것이지
// 바람직한 동작을 규정한 것이 아니다. 관심목록에 최근 추가된 제품이 하나라도
// 있으면 그 제품의 첫 관측일 이전 구간이 통째로 잘려 차트가 짧아진다.
// 개선안은 리뷰 노트 참고 — 이번 변경에서는 동작을 바꾸지 않는다.

const test = require('node:test');
const assert = require('node:assert/strict');

const { buildSummedSeries } = require('../../js/lib/shared.js');

function history(pairs) {
  return pairs.map(([date, median]) => ({ date, median }));
}

test('buildSummedSeries: 관측이 빠진 날은 forward-fill로 직전 값을 유지한다', () => {
  const a = history([
    ['2026-01-01', 100],
    ['2026-01-03', 120],
  ]);
  const b = history([
    ['2026-01-01', 50],
    ['2026-01-02', 60],
  ]);

  assert.deepEqual(buildSummedSeries([a, b]), [
    { date: '2026-01-01', total: 150 }, // 100 + 50
    { date: '2026-01-02', total: 160 }, // 100(유지) + 60
    { date: '2026-01-03', total: 180 }, // 120 + 60(유지)
  ]);
});

test('buildSummedSeries: 제품이 하나면 그 제품의 시계열이 그대로 나온다', () => {
  const a = history([
    ['2026-01-01', 100],
    ['2026-01-02', 110],
  ]);
  assert.deepEqual(buildSummedSeries([a]), [
    { date: '2026-01-01', total: 100 },
    { date: '2026-01-02', total: 110 },
  ]);
});

test('buildSummedSeries: 날짜는 입력 순서와 무관하게 오름차순 정렬된다', () => {
  const a = history([
    ['2026-01-03', 30],
    ['2026-01-01', 10],
    ['2026-01-02', 20],
  ]);
  assert.deepEqual(
    buildSummedSeries([a]).map((p) => p.date),
    ['2026-01-01', '2026-01-02', '2026-01-03']
  );
});

test('buildSummedSeries: median이 null인 관측은 값이 없는 것으로 본다', () => {
  const a = history([
    ['2026-01-01', null],
    ['2026-01-02', 100],
  ]);
  const b = history([
    ['2026-01-01', 50],
    ['2026-01-02', 50],
  ]);
  // 1/1은 a에 값이 없어 제외되고, 1/2부터 합산된다.
  assert.deepEqual(buildSummedSeries([a, b]), [{ date: '2026-01-02', total: 150 }]);
});

// --- 현재 규칙: "모든 제품이 값을 가진 날짜만 포함" -------------------------

test('[현재 동작] 최근 추가된 제품 하나가 구간 전체를 잘라낸다', () => {
  const long = history([
    ['2026-01-01', 100],
    ['2026-01-02', 100],
    ['2026-01-03', 100],
    ['2026-01-04', 100],
    ['2026-01-05', 100],
  ]);
  const recent = history([
    ['2026-01-04', 10],
    ['2026-01-05', 10],
  ]);

  const series = buildSummedSeries([long, recent]);
  // 1년치 히스토리가 있어도 신규 제품의 첫 관측일부터만 남는다.
  assert.deepEqual(series, [
    { date: '2026-01-04', total: 110 },
    { date: '2026-01-05', total: 110 },
  ]);
  assert.equal(series.length, 2);
});

test('[현재 동작] 겹치는 구간이 없으면 결과가 비어 버린다', () => {
  const older = history([
    ['2026-01-01', 100],
    ['2026-01-02', 100],
  ]);
  const newer = history([
    ['2026-02-01', 10],
    ['2026-02-02', 10],
  ]);
  // newer의 첫 관측(2/1)부터는 older가 forward-fill되므로 2건이 남는다.
  assert.deepEqual(buildSummedSeries([older, newer]), [
    { date: '2026-02-01', total: 110 },
    { date: '2026-02-02', total: 110 },
  ]);
});

test('[현재 동작] 제품이 3개면 가장 늦게 시작한 제품이 시작점을 결정한다', () => {
  const a = history([['2026-01-01', 1], ['2026-01-02', 1], ['2026-01-03', 1]]);
  const b = history([['2026-01-02', 2], ['2026-01-03', 2]]);
  const c = history([['2026-01-03', 3]]);

  assert.deepEqual(buildSummedSeries([a, b, c]), [{ date: '2026-01-03', total: 6 }]);
});

// --- 경계 입력 -------------------------------------------------------------

test('buildSummedSeries: 빈 입력', () => {
  assert.deepEqual(buildSummedSeries([]), []);
});

test('buildSummedSeries: 히스토리가 비어 있거나 null이어도 죽지 않는다', () => {
  assert.deepEqual(buildSummedSeries([[]]), []);
  assert.deepEqual(buildSummedSeries([null]), []);
  assert.deepEqual(buildSummedSeries([undefined, []]), []);
  // 한 제품이 빈 히스토리면 known이 절대 채워지지 않아 결과도 비어 있다.
  assert.deepEqual(buildSummedSeries([history([['2026-01-01', 100]]), []]), []);
});

test('buildSummedSeries: 항목이 null이거나 median 키가 없어도 무시한다', () => {
  const a = [null, { date: '2026-01-01' }, { date: '2026-01-02', median: 100 }];
  assert.deepEqual(buildSummedSeries([a]), [{ date: '2026-01-02', total: 100 }]);
});

test('buildSummedSeries: 같은 날짜가 중복되면 마지막 값이 이긴다', () => {
  const a = history([
    ['2026-01-01', 100],
    ['2026-01-01', 200],
  ]);
  assert.deepEqual(buildSummedSeries([a]), [{ date: '2026-01-01', total: 200 }]);
});

test('buildSummedSeries: median 0은 값이 있는 것으로 취급한다', () => {
  const a = history([['2026-01-01', 0]]);
  const b = history([['2026-01-01', 50]]);
  assert.deepEqual(buildSummedSeries([a, b]), [{ date: '2026-01-01', total: 50 }]);
});
