'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { sayHi, sayFoobar } = require('../hello-echo');

test('sayHi() returns exactly the string "HI"', () => {
  assert.equal(sayHi(), 'HI');
});

test('sayFoobar() returns exactly the string "foobar"', () => {
  assert.equal(sayFoobar(), 'foobar');
});

test('sayHi and sayFoobar are exported as arity-0 functions', () => {
  assert.equal(typeof sayHi, 'function');
  assert.equal(sayHi.length, 0);
  assert.equal(typeof sayFoobar, 'function');
  assert.equal(sayFoobar.length, 0);
});

test('extra unexpected arguments do not change the returned string', () => {
  assert.equal(sayHi('extra', 1, {}), 'HI');
  assert.equal(sayFoobar('extra', 1, {}), 'foobar');
});
