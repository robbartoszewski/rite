#!/usr/bin/env node
// copy-lint — fails the build when site copy breaks the voice rules (BRAND.md, brief §7) or makes a claim brief §12 forbids.
// Usage: node scripts/copy-lint.mjs dist --strict     (built output: unfilled placeholders are errors)
//        node scripts/copy-lint.mjs src/content       (sources: placeholders are warnings, so drafts can be worked on)
// No dependencies. Exit code 1 on any error. It is a tripwire, not a substitute for the line-by-line §12 pass before launch.
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, extname } from "node:path";

const args = process.argv.slice(2);
const strict = args.includes("--strict");
const root = args.find((a) => !a.startsWith("--")) ?? "dist";

// Hype, filler and anthropomorphism (brief §7). Matched as whole words/phrases, case-insensitive.
const BANNED = [
  "revolutionary", "seamless", "seamlessly", "powerful", "cutting-edge", "enterprise-grade", "next-generation",
  "game-changing", "effortless", "effortlessly", "blazing-fast", "blazing fast", "world-class", "unlock", "unlocks",
  "unleash", "supercharge", "elevate", "empower", "empowers", "transform your workflow", "the future of",
  "at scale", "10x", "dramatically", "significantly", "massively", "ai-powered",
  "collaborate", "collaborates", "collaboration", "team of agents", "agent team", "agents think", "agents understand",
];

// Claims the site must never make (brief §12). Substrings, case-insensitive, checked after ALLOW sentences are removed.
const FORBIDDEN = [
  // maturity
  "production-ready", "production ready", "battle-tested", "generally available", "stable",
  // social proof
  "trusted by", "developers love", "join thousands", "customers", "testimonial", "github stars",
  // Unattended operation. "runs unattended" came off this list on 2026-10-09:
  // the owner confirmed it is honest for the mixed fleet — you start a run and
  // it works the board for hours without you. What stays forbidden is the
  // stronger claim that nothing on this product does: starting itself on a
  // schedule, running with nobody reachable, or a fully local loop that closes
  // on its own. SPEC §9.12 still refuses to start a Claude session from
  // anything scheduled, and that is the line these words would cross.
  "while you sleep", "overnight", "fully autonomous", "autonomous", "hands-off", "set and forget",
  "24/7", "zero supervision", "no human", "fire and forget", "self-driving",
  "unattended operation is supported", "fully local loop", "no supervision",
  // network boundary
  "never leaves", "nothing leaves", "stays on your machine", "leaves your machine never",
  // sandbox
  "secure", "isolated", "contained", "airtight", "containment",
  // platforms and packaging
  "windows", "pypi", "pipx install rite", "homebrew", "brew install", "pip install", "docker support",
  // commercial
  "pricing", "per seat", "/seat", "free tier", "free trial", "contact sales", "request a demo",
  "get started free", "early access", "waitlist", "€", "£", "usd",
  // product shape
  "dashboard", "hosted", "cloud version", "web ui", "our api",
  // compliance and benchmarks
  "soc 2", "soc2", "iso 27001", "gdpr", "hipaa", "times faster", "× faster", "x faster",
  // routing
  "capability-based routing", "routes questions",
];

// Exact sentences where a forbidden word is the honest answer. Keep this list short; every entry is reviewed.
const ALLOW = [
  "is it production-ready?",
  "is the sandbox secure?",
  "windows is not attempted",
  "not on pypi",
  "is it on pypi?",
  "not containment",
  "a guard rail against mistakes, not containment",
  "never \"early access\"",
  // A verbatim quote of SPEC.md §9.12, cited as a source label in a post.
  "nothing rite runs unattended starts a claude session",
];

const PLACEHOLDERS = [/TODO\(/, /\[PUBLISH DATE\]/, /\[AUTHOR NAME\]/, /\[[A-Z][A-Z ]{3,}\]/];

const files = [];
(function walk(dir) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    const s = statSync(p);
    if (s.isDirectory()) walk(p);
    else if ([".html", ".md", ".mdx", ".astro", ".xml"].includes(extname(p))) files.push(p);
  }
})(root);

const decode = (t) => t.replace(/&amp;/g, "&").replace(/&#39;|&rsquo;|&#x27;/g, "'").replace(/&quot;|&ldquo;|&rdquo;/g, '"').replace(/&lt;/g, "<").replace(/&gt;/g, ">");

// Visible text plus the text people quote: meta content, alt, title, aria-label and JSON-LD.
const textOf = (src, ext) => {
  let t = src;
  if (ext === ".html" || ext === ".astro" || ext === ".xml") {
    const extra = [];
    for (const m of t.matchAll(/\s(?:content|alt|title|aria-label)="([^"]*)"/gi)) extra.push(m[1]);
    for (const m of t.matchAll(/<script[^>]*type="application\/ld\+json"[^>]*>([\s\S]*?)<\/script>/gi)) extra.push(m[1]);
    t = t.replace(/<script[\s\S]*?<\/script>/gi, " ").replace(/<style[\s\S]*?<\/style>/gi, " ")
         .replace(/<!--[\s\S]*?-->/g, " ").replace(/<pre[\s\S]*?<\/pre>/gi, " ").replace(/<[^>]+>/g, " ");
    t += " " + extra.join(" ");
  } else {
    t = t.replace(/<!--[\s\S]*?-->/g, " ").replace(/```[\s\S]*?```/g, " ");
  }
  return decode(t).replace(/\s+/g, " ");
};

const esc = (w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
let errors = 0, warnings = 0;
for (const f of files) {
  const raw = readFileSync(f, "utf8");
  const visible = textOf(raw, extname(f));
  let t = visible.toLowerCase();
  for (const a of ALLOW) t = t.split(a).join(" ");
  for (const w of BANNED) {
    if (new RegExp(`(^|[^a-z0-9-])${esc(w)}([^a-z0-9-]|$)`, "i").test(t)) { console.error(`ERROR ${f}: banned word "${w}" (brief §7)`); errors++; }
  }
  for (const c of FORBIDDEN) {
    const re = /^[a-z]/.test(c) ? new RegExp(`(^|[^a-z0-9])${esc(c)}`, "i") : new RegExp(esc(c), "i");
    if (re.test(t)) { console.error(`ERROR ${f}: forbidden claim "${c}" (brief §12)`); errors++; }
  }
  if (/[^\s!<]!(?=[\s"'”’)\]]|$)/.test(visible)) { console.error(`ERROR ${f}: exclamation mark in copy (brief §7)`); errors++; }
  // A double-escaped entity ("&amp;rsquo;") renders as literal text on the page.
  // decode() below would launder it, so this checks the raw source.
  if (/&amp;(?:[a-z]+|#\d+|#x[0-9a-f]+);/i.test(raw)) { console.error(`ERROR ${f}: double-escaped HTML entity renders as literal text`); errors++; }
  for (const re of PLACEHOLDERS) {
    if (re.test(raw.replace(/<!--[\s\S]*?-->/g, ""))) {
      const msg = `${f}: unfilled placeholder ${re}`;
      if (strict) { console.error("ERROR " + msg); errors++; } else { console.warn("warn  " + msg); warnings++; }
    }
  }
}
console.log(`copy-lint${strict ? " --strict" : ""}: ${files.length} files, ${errors} error(s), ${warnings} warning(s)`);
process.exit(errors ? 1 : 0);
