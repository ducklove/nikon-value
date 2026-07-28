'use strict';

// parseCompareIds 단위 테스트.
//
// compare.html은 ?ids=a,b,c 를 그대로 받는다. URL 파라미터는 신뢰할 수 없는
// 입력이므로 fetch·DOM에 닿기 전에 여기서 전부 걸러야 한다. 이 테스트는
// "이상한 입력이 들어와도 페이지가 안전하고 조용히 동작한다"를 고정한다.

const test = require('node:test');
const assert = require('node:assert/strict');

const { parseCompareIds } = require('../../js/lib/shared.js');

const KNOWN = ['nikon-z8', 'nikon-z9', 'nikon-z6iii', 'nikon-zf', 'nikon-z5', 'nikon-d850'];

test('parseCompareIds: 정상 입력은 순서를 유지한 채 통과한다', () => {
  const result = parseCompareIds('nikon-z8,nikon-z9,nikon-z6iii', KNOWN, 5);
  assert.deepEqual(result.ids, ['nikon-z8', 'nikon-z9', 'nikon-z6iii']);
  assert.deepEqual(result.unknown, []);
  assert.equal(result.overflow, 0);
  assert.equal(result.truncated, false);
});

test('parseCompareIds: 공백과 빈 조각은 무시한다', () => {
  const result = parseCompareIds(' nikon-z8 , ,, nikon-z9 ,', KNOWN, 5);
  assert.deepEqual(result.ids, ['nikon-z8', 'nikon-z9']);
  assert.deepEqual(result.unknown, []);
});

test('parseCompareIds: 문자열이 아니거나 비어 있으면 빈 결과', () => {
  for (const raw of [null, undefined, '', 0, 42, {}, [], true]) {
    const result = parseCompareIds(raw, KNOWN, 5);
    assert.deepEqual(result.ids, [], `raw=${JSON.stringify(raw)}`);
    assert.deepEqual(result.unknown, []);
  }
});

test('parseCompareIds: 카탈로그에 없는 ID는 버리고 이유를 돌려준다', () => {
  const result = parseCompareIds('nikon-z8,does-not-exist,nikon-z9', KNOWN, 5);
  assert.deepEqual(result.ids, ['nikon-z8', 'nikon-z9']);
  assert.deepEqual(result.unknown, ['does-not-exist']);
});

test('parseCompareIds: 경로 조작·스크립트 문자열도 그냥 알 수 없는 ID일 뿐이다', () => {
  const hostile = [
    '../../../etc/passwd',
    '<script>alert(1)</script>',
    'nikon-z8/../../secret',
    "'; DROP TABLE products;--",
    '%2e%2e%2fdata',
    'javascript:alert(1)',
  ].join(',');

  const result = parseCompareIds(hostile, KNOWN, 5);

  // 하나도 통과하지 않아야 한다 — fetch 시도 자체가 없다.
  assert.deepEqual(result.ids, []);
  assert.equal(result.unknown.length, 5, '보고용 목록은 5개까지만 모은다');
});

test('parseCompareIds: 알 수 없는 ID 목록은 중복을 접고 5개에서 멈춘다', () => {
  const raw = 'x,x,x,a,b,c,d,e,f,g,h';
  const result = parseCompareIds(raw, KNOWN, 5);
  assert.deepEqual(result.unknown, ['x', 'a', 'b', 'c', 'd']);
});

test('parseCompareIds: 중복 ID는 첫 등장만 남긴다', () => {
  const result = parseCompareIds('nikon-z8,nikon-z8,nikon-z9,nikon-z8', KNOWN, 5);
  assert.deepEqual(result.ids, ['nikon-z8', 'nikon-z9']);
  assert.equal(result.duplicated, 2);
});

test('parseCompareIds: 상한을 넘으면 잘라내고 넘친 개수를 보고한다', () => {
  const result = parseCompareIds(KNOWN.join(','), KNOWN, 3);
  assert.deepEqual(result.ids, ['nikon-z8', 'nikon-z9', 'nikon-z6iii']);
  assert.equal(result.overflow, 3);
});

test('parseCompareIds: max가 비정상이면 기본 5개로 되돌린다', () => {
  for (const max of [0, -1, NaN, undefined, 'many', Infinity]) {
    const result = parseCompareIds(KNOWN.join(','), KNOWN, max);
    assert.equal(result.ids.length, 5, `max=${max}`);
  }
});

test('parseCompareIds: 병적으로 긴 입력에도 상한 안에서 끝난다', () => {
  // 100만 자 × 쉼표 폭탄. 조각 수·길이 상한이 없으면 여기서 시간이 폭발한다.
  const bomb = 'nikon-z8,'.repeat(200000);
  const started = Date.now();
  const result = parseCompareIds(bomb, KNOWN, 5);
  assert.ok(Date.now() - started < 1000, '입력 길이에 비례해 시간을 쓰면 안 된다');
  assert.deepEqual(result.ids, ['nikon-z8']);
  assert.equal(result.truncated, true);
});

test('parseCompareIds: 조각 수가 상한을 넘으면 truncated로 알린다', () => {
  const raw = new Array(80).fill('unknown-model').join(',') + ',nikon-z8';
  const result = parseCompareIds(raw, KNOWN, 5);
  assert.equal(result.truncated, true);
  // 상한 밖으로 밀린 nikon-z8은 통과하지 않는다.
  assert.deepEqual(result.ids, []);
});

test('parseCompareIds: knownIds는 Set과 배열을 모두 받는다', () => {
  const fromSet = parseCompareIds('nikon-z8', new Set(KNOWN), 5);
  const fromArray = parseCompareIds('nikon-z8', KNOWN, 5);
  assert.deepEqual(fromSet.ids, fromArray.ids);
});

test('parseCompareIds: knownIds가 비어 있으면 아무것도 통과하지 않는다', () => {
  assert.deepEqual(parseCompareIds('nikon-z8', [], 5).ids, []);
  assert.deepEqual(parseCompareIds('nikon-z8', null, 5).ids, []);
});
