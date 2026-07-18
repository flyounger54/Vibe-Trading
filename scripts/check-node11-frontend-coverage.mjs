#!/usr/bin/env node
import fs from "node:fs";
import path from "node:path";

const [summaryPath] = process.argv.slice(2);
if (!summaryPath) {
  console.error("usage: check-node11-frontend-coverage.mjs COVERAGE_SUMMARY_JSON");
  process.exit(2);
}
const report = JSON.parse(fs.readFileSync(summaryPath, "utf8"));
const floor = { lines: 25, branches: 24 };
const total = report.total;
const failures = [];

for (const metric of Object.keys(floor)) {
  if (total[metric].pct < floor[metric]) failures.push(`total ${metric}=${total[metric].pct}%`);
}

const critical = [
  ["apiTransport.ts", 60, 55],
  ["fetchSSE.ts", 90, 70],
  [path.join("stores", "agent.ts"), 65, 60],
  ["useSSE.ts", 90, 70],
];
for (const [suffix, minLines, minBranches] of critical) {
  const entry = Object.entries(report).find(([key]) => key.endsWith(suffix));
  if (!entry) {
    failures.push(`missing critical coverage entry: ${suffix}`);
    continue;
  }
  const [, value] = entry;
  if (value.lines.pct < minLines || value.branches.pct < minBranches) {
    failures.push(`${suffix} line=${value.lines.pct}% branch=${value.branches.pct}%`);
  }
}

console.log(`frontend coverage: line=${total.lines.pct}% branch=${total.branches.pct}%`);
if (failures.length) {
  console.error(`coverage gate failed: ${failures.join("; ")}`);
  process.exit(1);
}
