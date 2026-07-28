'use strict';

// buildCompareSeries 단위 테스트.
//
// 제품마다 관측 시작일도, 관측 공백도 다르다. 비교 차트는 그것들을 하나의
// x축(날짜 합집합)에 올려야 하고, 값이 없는 날은 null로 남겨야 Chart.js가
// 선을 잘못 잇지 않는다. 지수화(첫날=100)는 가격대가 3배씩 차이 나는 모델을
// 같은 축에서 비교하기 위한 모드다.

const test = require('node:test');
const assert = require('node:assert/strict');

const { buildCompareSeries } = require('../../js/lib/shared.js');

function item(id, entries) {
  return { id, label: id.toUpperCase(), history: entries };
}

test('buildCompareSeries: 날짜 합집합을 정렬해 공통 x축을 만든다', () => {
  const result = buildCompareSeries([
    item('a', [{ date: '2026-01-03', median: 30 }, { date: '2026-01-01', median: 10 }]),
    item('b', [{ date: '2026-01-02', median: 20 }]),
  ]);

  assert.deepEqual(result.labels, ['2026-01-01', '2026-01-02', '2026-01-03']);
  assert.deepEqual(result.series[0].data, [10, null, 30]);
  assert.deepEqual(result.series[1].data, [null, 20, null]);
});

test('buildCompareSeries: 라벨과 id를 그대로 실어 보낸다', () => {
  const result = buildCompareSeries([item('nikon-z8', [{ date: '2026-01-01', median: 10 }])]);
  assert.equal(result.series[0].id, 'nikon-z8');
  assert.equal(result.series[0].label, 'NIKON-Z8');
});

test('buildCompareSeries: 값이 없는 제품은 시리즈에서 빠진다', () => {
  const result = buildCompareSeries([
    item('a', [{ date: '2026-01-01', median: 10 }]),
    item('empty', []),
    item('all-null', [{ date: '2026-01-01', median: null }]),
  ]);

  assert.deepEqual(result.series.map((s) => s.id), ['a']);
});

test('buildCompareSeries: 잘못된 입력에도 빈 결과를 돌려준다', () => {
  for (const input of [null, undefined, [], [null], [{}], [{ history: 'nope' }]]) {
    const result = buildCompareSeries(input);
    assert.deepEqual(result.labels, [], JSON.stringify(input));
    assert.deepEqual(result.series, []);
  }
});

test('buildCompareSeries: 날짜가 없거나 이상한 항목은 버린다', () => {
  const result = buildCompareSeries([
    item('a', [
      { date: '2026-01-01', median: 10 },
      { median: 99 },
      { date: '', median: 99 },
      { date: 20260102, median: 99 },
      null,
      { date: '2026-01-02', median: 20 },
    ]),
  ]);

  assert.deepEqual(result.labels, ['2026-01-01', '2026-01-02']);
  assert.deepEqual(result.series[0].data, [10, 20]);
});

test('buildCompareSeries: 숫자가 아닌 값은 버린다', () => {
  const result = buildCompareSeries([
    item('a', [
      { date: '2026-01-01', median: 10 },
      { date: '2026-01-02', median: 'oops' },
      { date: '2026-01-03', median: Infinity },
      { date: '2026-01-04', median: 40 },
    ]),
  ]);

  assert.deepEqual(result.labels, ['2026-01-01', '2026-01-04']);
  assert.deepEqual(result.series[0].data, [10, 40]);
});

test('buildCompareSeries: 같은 날짜가 두 번 있으면 마지막 값을 쓴다', () => {
  const result = buildCompareSeries([
    item('a', [
      { date: '2026-01-01', median: 10 },
      { date: '2026-01-01', median: 15 },
    ]),
  ]);

  assert.deepEqual(result.series[0].data, [15]);
});

test('buildCompareSeries: metric=count는 0을 유효한 값으로 취급한다', () => {
  const result = buildCompareSeries(
    [item('a', [
      { date: '2026-01-01', median: 100, count: 0 },
      { date: '2026-01-02', median: 100, count: 4 },
    ])],
    { metric: 'count' }
  );

  // 중앙값이었다면 null이 됐을 자리지만, 매물 0건은 "정보 없음"이 아니라 "0개"다.
  assert.deepEqual(result.series[0].data, [0, 4]);
});

test('buildCompareSeries: metric=count에서 count 누락은 0으로 본다', () => {
  const result = buildCompareSeries(
    [item('a', [{ date: '2026-01-01' }, { date: '2026-01-02', count: 3 }])],
    { metric: 'count' }
  );

  assert.deepEqual(result.series[0].data, [0, 3]);
});

test('buildCompareSeries: 알 수 없는 metric은 median으로 되돌린다', () => {
  const entries = [{ date: '2026-01-01', median: 10, count: 7 }];
  assert.deepEqual(
    buildCompareSeries([item('a', entries)], { metric: 'hax' }).series[0].data,
    [10]
  );
});

test('buildCompareSeries: 지수화는 각 시리즈의 첫 유효값을 100으로 맞춘다', () => {
  const result = buildCompareSeries(
    [
      item('cheap', [
        { date: '2026-01-01', median: 1000 },
        { date: '2026-01-02', median: 1100 },
      ]),
      item('pricey', [
        { date: '2026-01-01', median: 6000 },
        { date: '2026-01-02', median: 5400 },
      ]),
    ],
    { mode: 'indexed' }
  );

  assert.deepEqual(result.series[0].data, [100, 110]);
  assert.deepEqual(result.series[1].data, [100, 90]);
});

test('buildCompareSeries: 지수화 기준은 시리즈별 첫 관측일이다 (시작일이 달라도)', () => {
  const result = buildCompareSeries(
    [
      item('early', [
        { date: '2026-01-01', median: 200 },
        { date: '2026-01-03', median: 300 },
      ]),
      item('late', [{ date: '2026-01-03', median: 50 }]),
    ],
    { mode: 'indexed' }
  );

  assert.deepEqual(result.labels, ['2026-01-01', '2026-01-03']);
  assert.deepEqual(result.series[0].data, [100, 150]);
  assert.deepEqual(result.series[1].data, [null, 100]);
});

test('buildCompareSeries: 0에서 시작하면 첫 0이 아닌 값을 지수화 기준으로 잡는다', () => {
  // 0으로 나눌 수 없으므로 0 구간은 null로 남기고, 매물이 처음 나온 날을 100으로 본다.
  const result = buildCompareSeries(
    [item('a', [
      { date: '2026-01-01', count: 0 },
      { date: '2026-01-02', count: 5 },
      { date: '2026-01-03', count: 10 },
    ])],
    { metric: 'count', mode: 'indexed' }
  );

  assert.deepEqual(result.series[0].data, [null, 100, 200]);
});

test('buildCompareSeries: 끝까지 0뿐이면 지수화할 게 없어 시리즈가 빠진다', () => {
  const result = buildCompareSeries(
    [item('a', [
      { date: '2026-01-01', count: 0 },
      { date: '2026-01-02', count: 0 },
    ])],
    { metric: 'count', mode: 'indexed' }
  );

  assert.deepEqual(result.series, []);
});

test('buildCompareSeries: first/last/points로 범례와 표를 채울 수 있다', () => {
  const result = buildCompareSeries([
    item('a', [
      { date: '2026-01-01', median: 10 },
      { date: '2026-01-02', median: null },
      { date: '2026-01-03', median: 30 },
    ]),
  ]);

  assert.equal(result.series[0].first, 10);
  assert.equal(result.series[0].last, 30);
  assert.equal(result.series[0].points, 2);
});

test('buildCompareSeries: 원본 히스토리를 변형하지 않는다', () => {
  const entries = [{ date: '2026-01-01', median: 10 }];
  const snapshot = JSON.stringify(entries);
  buildCompareSeries([item('a', entries)], { mode: 'indexed' });
  assert.equal(JSON.stringify(entries), snapshot);
});
