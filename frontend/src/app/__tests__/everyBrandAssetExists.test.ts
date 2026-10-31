// DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
// Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
//
// A logo that points at a file nobody ever added.
//
// The About page signed the founder's bio with `/brand/ddc-logo-mark.svg`.
// That file never existed in `public/brand` (git history has no trace of it),
// so every visit to /about fired a 404 and showed an empty rounded tile where
// the mark should be. The build does not look at string paths into `public/`,
// the unit tests stub assets, and a broken <img> throws nothing, so the only
// thing that ever saw it was a real browser: the smoke-every-route sweep
// flagged the image as loaded-to-nothing on 2026-09-30.
//
// The check is narrow on purpose. It covers `/brand/...` literals, the one
// public folder the UI hard-codes paths into; other absolute image strings in
// `src` are fixtures or example URLs in tests, not files we ship. The
// population is floored so a regex that stops matching goes red instead of
// reporting "nothing missing".
import { describe, expect, it } from 'vitest';
import * as fs from 'node:fs';
import * as path from 'node:path';

const ROOT = path.resolve(__dirname, '..', '..', '..');
const SRC = path.join(ROOT, 'src');
const PUBLIC = path.join(ROOT, 'public');
const BRAND_LITERAL = /['"`](\/brand\/[A-Za-z0-9_./-]+)['"`]/g;

function brandLiterals(): Map<string, string> {
  const found = new Map<string, string>();
  const walk = (dir: string) => {
    for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
      const p = path.join(dir, e.name);
      if (e.isDirectory()) {
        if (e.name !== 'locales' && e.name !== '__snapshots__' && e.name !== 'test') walk(p);
      } else if (/\.tsx?$/.test(e.name) && !/\.test\.tsx?$/.test(e.name)) {
        for (const m of fs.readFileSync(p, 'utf8').matchAll(BRAND_LITERAL)) {
          if (!found.has(m[1]!)) found.set(m[1]!, path.relative(ROOT, p));
        }
      }
    }
  };
  walk(SRC);
  return found;
}

describe('brand assets referenced from src', () => {
  const literals = brandLiterals();

  it('the parser still matches /brand literals (control)', () => {
    // Control on the mechanism itself, independent of what src currently
    // holds: if the regex stops matching, the existence sweep below would
    // report "nothing missing" over an unscanned tree.
    const sample = `const a = '/brand/x.webp'; const b = "/brand/y.svg";`;
    expect([...sample.matchAll(BRAND_LITERAL)].map((m) => m[1])).toEqual([
      '/brand/x.webp',
      '/brand/y.svg',
    ]);
  });

  it('the product UI references no upstream brand asset', () => {
    // The founder-bio logo, the book cover and the avatar belonged to the
    // upstream project's marketing surfaces, which the product no longer
    // renders; the files remain in public/brand, credited in NOTICE.
    for (const [url] of literals) {
      expect(url.startsWith('/brand/ddc-')).toBe(false);
      expect(url.startsWith('/brand/artem-')).toBe(false);
    }
  });

  it('every /brand path is a file in public/brand', () => {
    const missing = [...literals].filter(([url]) => !fs.existsSync(path.join(PUBLIC, url))).map(([url, file]) => `${url} (${file})`);
    expect(missing).toEqual([]);
  });
});
