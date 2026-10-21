import { strict as assert } from "node:assert";
import { readFileSync } from "node:fs";

const html = readFileSync("index.html", "utf8");
const css = readFileSync("styles.css", "utf8");
const js = readFileSync("app.js", "utf8");

assert.match(html, /id="registrations-file"/);
assert.match(html, /id="view-checkin"/);
assert.match(html, /id="view-certificates"/);
assert.match(html, /id="view-exports"/);
assert.match(html, /app\.js/);
assert.match(css, /@media \(max-width: 780px\)/);
assert.match(css, /@media print/);
assert.match(js, /function parseCSV/);
assert.match(js, /function validateRegistrations/);
assert.match(js, /function validateSchedule/);
assert.match(js, /function renderCheckin/);
assert.match(js, /function downloadAll/);
assert.ok(!/fetch\s*\(/.test(js), "browser app must not upload data");
assert.ok(!/XMLHttpRequest/.test(js), "browser app must not use XMLHttpRequest");
console.log("web static checks passed");
