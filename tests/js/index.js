'use strict';

// `node --test tests/js/` 진입점.
//
// Node 22의 테스트 러너는 위치 인자를 파일 또는 글롭으로만 해석한다
// (디렉터리를 넘기면 재귀 탐색이 아니라 그 경로를 모듈로 require 한다).
// 그래서 디렉터리를 넘겼을 때 해석되는 index.js가 같은 폴더의 *.test.js를
// 전부 불러오는 역할을 맡는다.
//
// 아래 세 가지가 모두 같은 스위트를 실행한다:
//   node --test tests/js/
//   node --test tests/js/*.test.js
//   node --test            (인자 없이 저장소 루트에서 재귀 탐색)
// index.js는 *.test.js 패턴에 걸리지 않으므로 중복 실행되지 않는다.

const fs = require('node:fs');
const path = require('node:path');

for (const name of fs.readdirSync(__dirname).sort()) {
  if (name.endsWith('.test.js')) require(path.join(__dirname, name));
}
