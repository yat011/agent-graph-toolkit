'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { sayHi, sayFoobar } = require('../hello-echo.js');

test('sayHi() returns exactly the string "HI"', () => {
  assert.strictEqual(sayHi(), 'HI');
});

test('sayFoobar() returns exactly the string "foobar"', () => {
  assert.strictEqual(sayFoobar(), 'foobar');
});

test('both exports are functions of arity 0', () => {
  assert.strictEqual(typeof sayHi, 'function');
  assert.strictEqual(sayHi.length, 0);
  assert.strictEqual(typeof sayFoobar, 'function');
  assert.strictEqual(sayFoobar.length, 0);
});

test('calling either function with extra arguments still returns the same fixed string', () => {
  assert.strictEqual(sayHi('unexpected', 42), 'HI');
  assert.strictEqual(sayFoobar('unexpected', 42), 'foobar');
});
