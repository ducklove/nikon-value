'use strict';

// formatMoney / formatUsd / getExchangeRate / normalizeCurrency 단위 테스트.
// 통화 토글(USD ↔ KRW)의 표시 로직 전체가 여기 걸려 있다.

const test = require('node:test');
const assert = require('node:assert/strict');

const {
  formatMoney,
  formatUsd,
  getExchangeRate,
  normalizeCurrency,
} = require('../../js/lib/shared.js');

const RATE = { rate: 1380.25, source: '한국은행', reference_date: '2026-01-15' };

test('formatMoney: USD 정수는 소수점 없이 천 단위 구분', () => {
  assert.equal(formatMoney(0), '$0');
  assert.equal(formatMoney(7), '$7');
  assert.equal(formatMoney(1200), '$1,200');
  assert.equal(formatMoney(1234567), '$1,234,567');
});

test('formatMoney: USD 소수는 항상 두 자리', () => {
  assert.equal(formatMoney(1234.5), '$1,234.50');
  assert.equal(formatMoney(0.5), '$0.50');
  assert.equal(formatMoney(1234.567), '$1,234.57'); // 반올림
  assert.equal(formatMoney(1234.001), '$1,234.00');
});

test('formatMoney: 숫자로 변환 가능한 문자열도 받는다 (dataset 값)', () => {
  // data-money-usd 속성은 항상 문자열로 들어온다.
  assert.equal(formatMoney('1200'), '$1,200');
  assert.equal(formatMoney('1234.5'), '$1,234.50');
});

test('formatMoney: KRW는 환율을 곱해 정수로 반올림', () => {
  assert.equal(formatMoney(1200, { currency: 'krw', exchangeData: RATE }), '₩1,656,300');
  // 100.5 * 1000.5 = 100550.25 → 100550
  assert.equal(
    formatMoney(100.5, { currency: 'krw', exchangeData: { rate: 1000.5 } }),
    '₩100,550'
  );
  // 0.5 * 1001 = 500.5 → 501 (Math.round는 .5를 올림)
  assert.equal(formatMoney(0.5, { currency: 'krw', exchangeData: { rate: 1001 } }), '₩501');
  // KRW는 소수 자릿수를 붙이지 않는다
  assert.equal(formatMoney(1.234, { currency: 'krw', exchangeData: { rate: 1000 } }), '₩1,234');
});

test('formatMoney: 환율이 없거나 이상하면 KRW는 "-"', () => {
  assert.equal(formatMoney(1200, { currency: 'krw' }), '-');
  assert.equal(formatMoney(1200, { currency: 'krw', exchangeData: null }), '-');
  assert.equal(formatMoney(1200, { currency: 'krw', exchangeData: {} }), '-');
  assert.equal(formatMoney(1200, { currency: 'krw', exchangeData: { rate: 0 } }), '-');
  assert.equal(formatMoney(1200, { currency: 'krw', exchangeData: { rate: -5 } }), '-');
  assert.equal(formatMoney(1200, { currency: 'krw', exchangeData: { rate: 'abc' } }), '-');
});

test('formatMoney: 음수는 통화 기호 앞에 부호를 붙인다', () => {
  assert.equal(formatMoney(-1200), '-$1,200');
  assert.equal(formatMoney(-12.5), '-$12.50');
  assert.equal(formatMoney(-1200, { currency: 'krw', exchangeData: RATE }), '-₩1,656,300');
});

test("formatMoney: signDisplay 'always'는 양수에만 +를 붙인다", () => {
  assert.equal(formatMoney(1200, { signDisplay: 'always' }), '+$1,200');
  assert.equal(formatMoney(12.5, { signDisplay: 'always' }), '+$12.50');
  assert.equal(formatMoney(-1200, { signDisplay: 'always' }), '-$1,200');
  assert.equal(formatMoney(0, { signDisplay: 'always' }), '$0'); // 0에는 부호 없음
  assert.equal(
    formatMoney(1200, { currency: 'krw', exchangeData: RATE, signDisplay: 'always' }),
    '+₩1,656,300'
  );
});

test("formatMoney: signDisplay 기본값 'auto'는 +를 붙이지 않는다", () => {
  assert.equal(formatMoney(1200), '$1,200');
  assert.equal(formatMoney(1200, { signDisplay: 'auto' }), '$1,200');
});

test('formatMoney: 값이 없거나 숫자가 아니면 "-"', () => {
  assert.equal(formatMoney(null), '-');
  assert.equal(formatMoney(undefined), '-');
  assert.equal(formatMoney(''), '-');
  assert.equal(formatMoney(NaN), '-');
  assert.equal(formatMoney('abc'), '-');
  assert.equal(formatMoney(Infinity), '-');
  assert.equal(formatMoney(-Infinity), '-');
  // KRW에서도 동일 (환율 조회 전에 걸러진다)
  assert.equal(formatMoney(null, { currency: 'krw', exchangeData: RATE }), '-');
});

test('formatMoney: 알 수 없는 통화 코드는 USD로 취급', () => {
  assert.equal(formatMoney(1200, { currency: 'eur' }), '$1,200');
});

test('getExchangeRate: 양수 유한값만 통과', () => {
  assert.equal(getExchangeRate({ rate: 1380.25 }), 1380.25);
  assert.equal(getExchangeRate({ rate: '1380.25' }), 1380.25);
  assert.equal(getExchangeRate({ rate: 0 }), null);
  assert.equal(getExchangeRate({ rate: -1 }), null);
  assert.equal(getExchangeRate({}), null);
  assert.equal(getExchangeRate(null), null);
  assert.equal(getExchangeRate(undefined), null);
});

test('normalizeCurrency: 환율이 있어야만 krw를 허용', () => {
  assert.equal(normalizeCurrency('krw', RATE), 'krw');
  assert.equal(normalizeCurrency('krw', null), 'usd');
  assert.equal(normalizeCurrency('krw', { rate: 0 }), 'usd');
  assert.equal(normalizeCurrency('usd', RATE), 'usd');
  assert.equal(normalizeCurrency('eur', RATE), 'usd');
  assert.equal(normalizeCurrency(null, RATE), 'usd');
});

test('formatUsd: 관심목록 합산은 정수 반올림 USD', () => {
  assert.equal(formatUsd(1234.6), '$1,235');
  assert.equal(formatUsd(0), '$0');
  assert.equal(formatUsd(1234567.4), '$1,234,567');
  assert.equal(formatUsd(null), '-');
  assert.equal(formatUsd(undefined), '-');
  assert.equal(formatUsd(NaN), '-');
});
