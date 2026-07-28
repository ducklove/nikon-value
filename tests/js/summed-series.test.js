'use strict';

// buildSummedSeries 단위 테스트 (관심 목록 가치 대시보드의 합산 추이).
//
// 규칙 변경 이력
// ----------------------------------------------------------------------------
// 예전 규칙은 "모든 제품이 값을 가진 날짜만 포함"(known === maps.length)이었다.
// 아래 [규칙 변경] 표시가 붙은 테스트들이 그 동작을 고정하고 있었는데, 사용자
// 체감 결함이 명확해서 이번에 규칙을 바꾸고 테스트도 새 규칙으로 갱신했다.
//   - 무엇이 문제였나: 가장 늦게 시작한 제품 하나가 전체 구간을 결정했다.
//     관심목록에 어제 추가된 신제품이 하나만 있어도 1년치 추이가 이틀로 잘렸다.
//   - 무엇으로 바꿨나: 값이 하나라도 있는 날은 전부 포함하고, 포인트마다
//     known/missing(그날 합산에 들어간/빠진 제품 수)과 연쇄 지수 index를 같이
//     내보낸다. "값 하나라도 있으면 포함"만 했으면 합류일에 합산액이 계단처럼
//     뛰는 더 나쁜 결함이 되므로, 절대액(total)과 구성 변화에 둔감한 축(index)을
//     함께 돌려주고 UI가 둘의 차이를 설명한다(buildSummedSeriesNote).
//
// index(연쇄 지수)는 "그날과 그 전날 모두 값이 있던 제품들"의 변동률만 이어
// 붙인다. 그래서 신규 제품이 합류해도 index는 튀지 않는다 — 이게 이 변경의
// 핵심이라 아래 "신규 제품 합류" 테스트들이 그 성질을 직접 검증한다.

const test = require('node:test');
const assert = require('node:assert/strict');

const { buildSummedSeries, buildSummedSeriesNote } = require('../../js/lib/shared.js');

function history(pairs) {
  return pairs.map(([date, median]) => ({ date, median }));
}

// 포인트 비교를 읽기 쉽게 — 관심 없는 필드를 매번 쓰지 않기 위한 헬퍼.
function shape(series) {
  return series.map((p) => ({ date: p.date, total: p.total, known: p.known, missing: p.missing }));
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

  assert.deepEqual(shape(buildSummedSeries([a, b])), [
    { date: '2026-01-01', total: 150, known: 2, missing: 0 }, // 100 + 50
    { date: '2026-01-02', total: 160, known: 2, missing: 0 }, // 100(유지) + 60
    { date: '2026-01-03', total: 180, known: 2, missing: 0 }, // 120 + 60(유지)
  ]);
});

test('buildSummedSeries: 제품이 하나면 그 제품의 시계열이 그대로 나온다', () => {
  const a = history([
    ['2026-01-01', 100],
    ['2026-01-02', 110],
  ]);
  assert.deepEqual(shape(buildSummedSeries([a])), [
    { date: '2026-01-01', total: 100, known: 1, missing: 0 },
    { date: '2026-01-02', total: 110, known: 1, missing: 0 },
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
  // 1/1은 a에 값이 없어 b만 합산되고(missing 1), 1/2부터 둘 다 들어간다.
  assert.deepEqual(shape(buildSummedSeries([a, b])), [
    { date: '2026-01-01', total: 50, known: 1, missing: 1 },
    { date: '2026-01-02', total: 150, known: 2, missing: 0 },
  ]);
});

// --- 새 규칙: "값이 하나라도 있는 날은 포함, 구성은 known/missing으로 노출" ---

test('[규칙 변경] 최근 추가된 제품이 있어도 이전 구간이 잘리지 않는다', () => {
  // 예전에는 신규 제품의 첫 관측일(1/4)부터 2건만 남았다.
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
  assert.equal(series.length, 5);
  assert.deepEqual(shape(series), [
    { date: '2026-01-01', total: 100, known: 1, missing: 1 },
    { date: '2026-01-02', total: 100, known: 1, missing: 1 },
    { date: '2026-01-03', total: 100, known: 1, missing: 1 },
    { date: '2026-01-04', total: 110, known: 2, missing: 0 }, // 신규 합류로 절대액 점프
    { date: '2026-01-05', total: 110, known: 2, missing: 0 },
  ]);
});

test('[규칙 변경] 겹치는 구간이 없어도 두 제품의 전체 구간이 남는다', () => {
  // 예전에는 늦게 시작한 쪽의 첫 관측일(2/1)부터 2건만 남았다.
  const older = history([
    ['2026-01-01', 100],
    ['2026-01-02', 100],
  ]);
  const newer = history([
    ['2026-02-01', 10],
    ['2026-02-02', 10],
  ]);

  assert.deepEqual(shape(buildSummedSeries([older, newer])), [
    { date: '2026-01-01', total: 100, known: 1, missing: 1 },
    { date: '2026-01-02', total: 100, known: 1, missing: 1 },
    { date: '2026-02-01', total: 110, known: 2, missing: 0 },
    { date: '2026-02-02', total: 110, known: 2, missing: 0 },
  ]);
});

test('[규칙 변경] 제품이 3개면 가장 이른 시작일부터 그려지고 구성이 단계적으로 채워진다', () => {
  // 예전에는 가장 늦게 시작한 c 때문에 1/3 한 점만 남았다.
  const a = history([['2026-01-01', 1], ['2026-01-02', 1], ['2026-01-03', 1]]);
  const b = history([['2026-01-02', 2], ['2026-01-03', 2]]);
  const c = history([['2026-01-03', 3]]);

  assert.deepEqual(shape(buildSummedSeries([a, b, c])), [
    { date: '2026-01-01', total: 1, known: 1, missing: 2 },
    { date: '2026-01-02', total: 3, known: 2, missing: 1 },
    { date: '2026-01-03', total: 6, known: 3, missing: 0 },
  ]);
});

// --- 연쇄 지수: 신규 제품 합류가 추이를 왜곡하지 않는다 ---------------------
// 이 결함의 본질은 "구간이 잘리는 것"이 아니라 "합산 절대액의 의미가 구간마다
// 달라지는 것"이다. index는 그 문제를 다루는 축이라 아래 테스트들이 가장 중요하다.

test('연쇄 지수: 신규 제품이 합류해도 시세가 그대로면 지수는 100을 유지한다', () => {
  const long = history([
    ['2026-01-01', 100],
    ['2026-01-02', 100],
    ['2026-01-03', 100],
  ]);
  const joiner = history([['2026-01-03', 900]]); // 합산액을 10배로 만드는 신규 제품

  const series = buildSummedSeries([long, joiner]);
  assert.deepEqual(series.map((p) => p.total), [100, 100, 1000]); // 절대액은 계단
  assert.deepEqual(series.map((p) => p.index), [100, 100, 100]); // 지수는 평평
});

test('연쇄 지수: 합류 이후 변동은 새 구성 전체의 변동률로 이어진다', () => {
  const a = history([
    ['2026-01-01', 100],
    ['2026-01-02', 100],
    ['2026-01-03', 110],
  ]);
  const b = history([
    ['2026-01-02', 100],
    ['2026-01-03', 110],
  ]);

  const series = buildSummedSeries([a, b]);
  // 1/2: b 합류일 — a만으로 계산하므로 변동 없음.
  // 1/3: 둘 다 있으니 (110+110)/(100+100) = 1.1 → 110.
  assert.deepEqual(series.map((p) => p.index), [100, 100, 110]);
  assert.deepEqual(series.map((p) => p.total), [100, 200, 220]);
});

test('연쇄 지수: 시세 변동만 있으면 지수와 합산액의 변동률이 일치한다', () => {
  const a = history([['2026-01-01', 100], ['2026-01-02', 120]]);
  const b = history([['2026-01-01', 100], ['2026-01-02', 80]]);

  const series = buildSummedSeries([a, b]);
  assert.deepEqual(series.map((p) => p.total), [200, 200]);
  assert.deepEqual(series.map((p) => p.index), [100, 100]);
});

test('연쇄 지수: 첫 포인트는 항상 100이고 forward-fill 구간에서는 변하지 않는다', () => {
  const a = history([['2026-01-01', 100], ['2026-01-05', 150]]);
  const b = history([['2026-01-01', 100], ['2026-01-03', 100]]);

  const series = buildSummedSeries([a, b]);
  assert.deepEqual(series.map((p) => p.date), ['2026-01-01', '2026-01-03', '2026-01-05']);
  assert.deepEqual(series.map((p) => p.index), [100, 100, 125]); // (150+100)/(100+100)
});

test('연쇄 지수: 합산이 0인 구간에서는 직전 지수를 유지한다(0으로 나누지 않는다)', () => {
  const a = history([['2026-01-01', 0], ['2026-01-02', 0], ['2026-01-03', 50]]);
  const series = buildSummedSeries([a]);
  assert.deepEqual(series.map((p) => p.index), [100, 100, 100]);
  assert.deepEqual(series.map((p) => p.total), [0, 0, 50]);
});

// --- 경계 입력 -------------------------------------------------------------

test('buildSummedSeries: 빈 입력', () => {
  assert.deepEqual(buildSummedSeries([]), []);
  assert.deepEqual(buildSummedSeries(null), []);
});

test('buildSummedSeries: 히스토리가 비어 있거나 null이어도 죽지 않는다', () => {
  assert.deepEqual(buildSummedSeries([[]]), []);
  assert.deepEqual(buildSummedSeries([null]), []);
  assert.deepEqual(buildSummedSeries([undefined, []]), []);
});

test('[규칙 변경] 한 제품이 빈 히스토리여도 나머지 제품의 추이는 살아남는다', () => {
  // 예전에는 known이 절대 채워지지 않아 결과가 통째로 비었다.
  assert.deepEqual(shape(buildSummedSeries([history([['2026-01-01', 100]]), []])), [
    { date: '2026-01-01', total: 100, known: 1, missing: 1 },
  ]);
});

test('buildSummedSeries: 항목이 null이거나 median 키가 없어도 무시한다', () => {
  const a = [null, { date: '2026-01-01' }, { date: '2026-01-02', median: 100 }];
  assert.deepEqual(shape(buildSummedSeries([a])), [
    { date: '2026-01-02', total: 100, known: 1, missing: 0 },
  ]);
});

test('buildSummedSeries: 같은 날짜가 중복되면 마지막 값이 이긴다', () => {
  const a = history([
    ['2026-01-01', 100],
    ['2026-01-01', 200],
  ]);
  assert.deepEqual(shape(buildSummedSeries([a])), [
    { date: '2026-01-01', total: 200, known: 1, missing: 0 },
  ]);
});

test('buildSummedSeries: median 0은 값이 있는 것으로 취급한다', () => {
  const a = history([['2026-01-01', 0]]);
  const b = history([['2026-01-01', 50]]);
  assert.deepEqual(shape(buildSummedSeries([a, b])), [
    { date: '2026-01-01', total: 50, known: 2, missing: 0 },
  ]);
});

// --- 안내 문구 -------------------------------------------------------------
// 문구가 데이터 상태와 어긋나면 결함을 감추는 셈이 되므로 같이 고정한다.

test('buildSummedSeriesNote: 구성 변화가 없으면 합산액을 그대로 읽어도 된다고 말한다', () => {
  const series = buildSummedSeries([
    history([['2026-01-01', 100], ['2026-01-02', 110]]),
    history([['2026-01-01', 50], ['2026-01-02', 50]]),
  ]);
  const note = buildSummedSeriesNote(series);
  assert.match(note, /히스토리가 있는 2개 제품/);
  assert.match(note, /2026-01-01 ~ 2026-01-02/);
  assert.match(note, /전 구간에서 2개 모두 시세 기록이 있어/);
  // 계단·점선 경고는 해당 없는 상태라 나오면 안 된다.
  assert.doesNotMatch(note, /계단/);
  assert.doesNotMatch(note, /점선/);
});

test('buildSummedSeriesNote: 신규 합류가 있으면 계단과 점선 구간을 설명한다', () => {
  const series = buildSummedSeries([
    history([['2026-01-01', 100], ['2026-01-02', 100], ['2026-01-03', 100]]),
    history([['2026-01-03', 900]]),
  ]);
  const note = buildSummedSeriesNote(series, { mode: 'indexed' });
  assert.match(note, /2026-01-01에는 1개만 합산되고 이후 1번에 걸쳐 2개까지 늘어납니다/);
  assert.match(note, /계단처럼 뛰므로/);
  assert.match(note, /실선은 2026-01-03부터 2개가 모두 반영된 구간/);
  assert.match(note, /지금 보는 지수\(첫날=100\)/);
});

test('buildSummedSeriesNote: 합산액 보기에서는 계단이 보인다고 정직하게 말한다', () => {
  const series = buildSummedSeries([
    history([['2026-01-01', 100], ['2026-01-02', 100]]),
    history([['2026-01-02', 900]]),
  ]);
  const note = buildSummedSeriesNote(series, { mode: 'absolute' });
  assert.match(note, /지금 보는 합산액에는 이 계단이 그대로 들어 있습니다/);
  assert.match(note, /지수\(첫날=100\) 보기로 바꾸세요/);
});

test('buildSummedSeriesNote: 끝까지 구성이 안 채워지면 전 구간 점선이라고 알린다', () => {
  const series = buildSummedSeries([
    history([['2026-01-01', 100], ['2026-01-02', 100], ['2026-01-03', 100]]),
    history([['2026-01-02', 50]]),
    history([]), // 시세 기록이 아예 없는 제품
  ]);
  const note = buildSummedSeriesNote(series);
  assert.match(note, /아직 1개는 시세 기록이 없어 마지막 날도 2개 기준입니다\(전 구간 점선\)/);
});

test('buildSummedSeriesNote: 빈 시계열', () => {
  assert.equal(buildSummedSeriesNote([]), '추이를 그릴 데이터가 아직 부족합니다.');
  assert.equal(buildSummedSeriesNote(null), '추이를 그릴 데이터가 아직 부족합니다.');
});
