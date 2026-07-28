'use strict';

// escapeHtml 단위 테스트.
// 예전에는 site.js(정규식, " 처리) / auth.js(DOM textContent) / admin.js(esc)
// 세 구현이 따로 있었고 출력이 서로 달랐다. 지금은 하나로 합쳐졌으므로
// "속성값 삽입 지점에서도 안전한가"를 이 파일이 지킨다.

const test = require('node:test');
const assert = require('node:assert/strict');

const { escapeHtml } = require('../../js/lib/shared.js');

test('escapeHtml: & < > " \' 다섯 문자를 모두 이스케이프한다', () => {
  assert.equal(escapeHtml('&'), '&amp;');
  assert.equal(escapeHtml('<'), '&lt;');
  assert.equal(escapeHtml('>'), '&gt;');
  assert.equal(escapeHtml('"'), '&quot;');
  assert.equal(escapeHtml("'"), '&#39;');
  assert.equal(escapeHtml(`&<>"'`), '&amp;&lt;&gt;&quot;&#39;');
});

test('escapeHtml: &를 가장 먼저 치환해 이중 이스케이프가 생기지 않는다', () => {
  // '&'를 마지막에 치환하면 &lt; → &amp;lt; 가 아니라 &amp;amp;lt; 가 된다.
  assert.equal(escapeHtml('&lt;'), '&amp;lt;');
  assert.equal(escapeHtml('&amp;'), '&amp;amp;');
});

test('escapeHtml: 이스케이프할 문자가 없으면 원문 그대로', () => {
  assert.equal(escapeHtml('Nikon Z9'), 'Nikon Z9');
  assert.equal(escapeHtml('니콘 Z9 24-70mm f/2.8'), '니콘 Z9 24-70mm f/2.8');
});

test('escapeHtml: null/undefined는 빈 문자열', () => {
  // 과거 auth.js의 DOM 버전은 undefined를 "undefined" 문자열로 렌더링했다.
  assert.equal(escapeHtml(null), '');
  assert.equal(escapeHtml(undefined), '');
});

test('escapeHtml: 0과 false는 문자열로 변환된다 (falsy 조기 반환 아님)', () => {
  // 과거 admin.js의 esc()는 `if (!str) return ''` 이라 0을 빈 문자열로 지웠다.
  assert.equal(escapeHtml(0), '0');
  assert.equal(escapeHtml(false), 'false');
  assert.equal(escapeHtml(''), '');
  assert.equal(escapeHtml(1234.5), '1234.5');
});

test('escapeHtml: 실제 공격 문자열 — 텍스트 삽입 지점', () => {
  assert.equal(
    escapeHtml('<script>alert(1)</script>'),
    '&lt;script&gt;alert(1)&lt;/script&gt;'
  );
  assert.equal(
    escapeHtml('<img src=x onerror=alert(1)>'),
    '&lt;img src=x onerror=alert(1)&gt;'
  );
  assert.equal(
    escapeHtml('<svg/onload=alert(1)>'),
    '&lt;svg/onload=alert(1)&gt;'
  );
});

test('escapeHtml: 실제 공격 문자열 — 속성값 탈출 시도', () => {
  // site.js의 카드 썸네일(src="...", alt="...")과 admin.js의 title="..." /
  // value="..." 처럼 큰따옴표로 감싼 속성에 그대로 들어가는 자리들.
  assert.equal(
    escapeHtml('" onerror="alert(1)'),
    '&quot; onerror=&quot;alert(1)'
  );
  assert.equal(
    escapeHtml("' onmouseover='alert(1)"),
    '&#39; onmouseover=&#39;alert(1)'
  );
  assert.equal(
    escapeHtml('"><img src=x onerror=alert(1)>'),
    '&quot;&gt;&lt;img src=x onerror=alert(1)&gt;'
  );
  assert.equal(
    escapeHtml('javascript:alert(1)"'),
    'javascript:alert(1)&quot;'
  );
});

test('escapeHtml: 출력에는 속성/태그를 깨뜨릴 수 있는 원시 문자가 남지 않는다', () => {
  const payloads = [
    `"><script>alert('xss')</script>`,
    `' onfocus='alert(1)`,
    `</title><style>@import'x'</style>`,
    `&<>"'`,
    `<a href="#">링크</a>`,
  ];
  for (const payload of payloads) {
    const escaped = escapeHtml(payload);
    assert.equal(/[<>"']/.test(escaped), false, `원시 문자가 남았다: ${escaped}`);
  }
});
