const test = require("node:test");
const assert = require("node:assert/strict");
const { sayHi, sayFoobar } = require("../hello-echo.js");

test("sayHi returns exactly 'HI'", () => {
  assert.strictEqual(sayHi(), "HI");
});

test("sayFoobar returns exactly 'foobar'", () => {
  assert.strictEqual(sayFoobar(), "foobar");
});

test("both exports are zero-arity functions", () => {
  assert.strictEqual(typeof sayHi, "function");
  assert.strictEqual(sayHi.length, 0);
  assert.strictEqual(typeof sayFoobar, "function");
  assert.strictEqual(sayFoobar.length, 0);
});

test("extra arguments do not affect return value", () => {
  assert.strictEqual(sayHi("unexpected", 42), "HI");
  assert.strictEqual(sayFoobar("unexpected", 42), "foobar");
});
