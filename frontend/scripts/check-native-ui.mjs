import { readdirSync, readFileSync } from 'node:fs';
import { join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../src/', import.meta.url));
const forbidden = [
  ['原生下拉', /<select\b/],
  ['原生日期控件', /type\s*=\s*["'](?:date|datetime-local|month|week|range|color)["']/],
  ['原生 confirm / alert / prompt', /window\.(?:confirm|alert|prompt)\s*\(/],
  ['原生 dialog', /<dialog\b/],
  ['contenteditable', /contenteditable(?:=|\s|>)/],
];

const files = [];
function walk(dir) {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) walk(full);
    else if (entry.name.endsWith('.vue')) files.push(full);
  }
}
walk(root);
files.sort();

const offenses = [];
for (const file of files) {
  const text = readFileSync(file, 'utf8');
  const lines = text.split(/\r?\n/);
  lines.forEach((line, index) => {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('//') || trimmed.startsWith('*') || trimmed.startsWith('/*') || trimmed.startsWith('<!--')) return;
    for (const [name, pattern] of forbidden) {
      if (pattern.test(line)) {
        offenses.push({ file: relative(root, file), line: index + 1, name, text: trimmed });
      }
    }
  });
}

if (offenses.length) {
  console.error(`发现 ${offenses.length} 处原生 UI 控件：`);
  for (const item of offenses) {
    console.error(`  ${item.file}:${item.line}  ${item.name}  ${item.text}`);
  }
  process.exit(1);
}

console.log(`原生 UI 审计通过：${files.length} 个 Vue 文件，无原生下拉/日期/弹窗/dialog。`);
