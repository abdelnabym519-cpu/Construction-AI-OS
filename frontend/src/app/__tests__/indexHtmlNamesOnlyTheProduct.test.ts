import { describe, it, expect } from 'vitest';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { resolve, join } from 'node:path';

import { APP_NAME } from '@/shared/lib/brandName';

/**
 * What the browser chrome says before any React has run.
 *
 * `frontend/index.html` is the first thing a visitor's browser reads: its <title>
 * is the tab, its meta description is the search result and the chat preview, its
 * og:* and twitter:* values are the card, and its JSON-LD block is what a crawler
 * records about the product. None of it is under `frontend/src`, so no component
 * test renders it and no locale key carries it - which is how the tab kept reading
 * "Construction AI OS - Open-Source Construction Cost Estimation Platform" after
 * every string the product renders had been cleaned. An owner's screenshot of the
 * rebranded sign-in page showed exactly that.
 *
 * These cases read the file the way the browser does and judge only the values a
 * reader or a crawler is given. `scripts/check_no_promotional_strings.py` reads the
 * same file with the same intent and is the ratchet across every browser surface
 * (the PWA manifest, the installer descriptions, the served legal pages); this test
 * is the half that can say something positive rather than only negative - that the
 * tab names the display name and nothing else, and that the structured data still
 * carries the version `scripts/check_version_sync.py` reads out of it.
 *
 * Both halves assert on a set of values they counted first. An extractor that
 * stopped matching would otherwise report nothing to judge and pass by finding no
 * offenders, which is the one way a test like this can rot into a decoration.
 */

const HTML = readFileSync(resolve(__dirname, '../../../index.html'), 'utf-8');

/**
 * Words that must not reach a reader of the browser chrome.
 *
 * The same list the guard uses, plus the two the owner's brief names for this
 * surface specifically: "free", because a price claim in structured data is a claim
 * about a deployment somebody else may be selling under a commercial licence, and
 * "#1", because a rank claim in a title is advertising rather than description.
 */
const FORBIDDEN = [
  'open source',
  'open-source',
  'opensource',
  'AGPL',
  'GitHub',
  'Telegram',
  'OpenConstructionERP',
  'DataDrivenConstruction',
  'Data Driven Construction',
  'openconstructionerp.com',
  'datadrivenconstruction.io',
  'free',
];

/** "#1" as a rank claim. A hex colour continues with a hex digit; the claim does not. */
const HEX_TAIL = /#1[0-9a-fA-F]/g;

function offenders(value: string): string[] {
  const found = FORBIDDEN.filter((word) => value.toLowerCase().includes(word.toLowerCase()));
  if (/#1/.test(value.replace(HEX_TAIL, ''))) found.push('#1 (rank claim)');
  return found;
}

/** The text of every <title> element, in document order. */
function titles(html: string): string[] {
  return [...html.matchAll(/<title\b[^>]*>([\s\S]*?)<\/title\s*>/gi)].map((m) => m[1].trim());
}

/** Every meta tag's content, keyed by the name or property it is served under. */
function metaValues(html: string): Array<{ key: string; value: string }> {
  const out: Array<{ key: string; value: string }> = [];
  for (const tag of html.matchAll(/<meta\b[^>]*>/gi)) {
    const name = tag[0].match(/\b(?:name|property)\s*=\s*"([^"]+)"/i);
    const content = tag[0].match(/\bcontent\s*=\s*"([^"]*)"/i);
    if (name && content) out.push({ key: name[1], value: content[1] });
  }
  return out;
}

/** The schema.org block, parsed - so a value is judged as data, not as markup. */
function jsonLd(html: string): Record<string, unknown> {
  const block = html.match(/<script type="application\/ld\+json">([\s\S]*?)<\/script>/i);
  expect(block, 'index.html carries no JSON-LD block at all').toBeTruthy();
  return JSON.parse(block![1]) as Record<string, unknown>;
}

/** Every string in the JSON-LD block, with the property name it sits under. */
function jsonLdStrings(value: unknown, path = ''): Array<{ key: string; value: string }> {
  if (typeof value === 'string') return [{ key: path || '(root)', value }];
  if (Array.isArray(value)) {
    return value.flatMap((item, i) => jsonLdStrings(item, `${path}[${i}]`));
  }
  if (value && typeof value === 'object') {
    return Object.entries(value as Record<string, unknown>).flatMap(([k, v]) =>
      jsonLdStrings(v, path ? `${path}.${k}` : k),
    );
  }
  return [];
}

describe('the browser chrome in frontend/index.html', () => {
  it('names the display name in the tab and nothing else', () => {
    const found = titles(HTML);
    // Counted, not assumed: one <title>, and it is the whole tab.
    expect(found).toHaveLength(1);
    expect(found[0]).toBe(APP_NAME);
    expect(offenders(found[0])).toEqual([]);
  });

  it('carries no claim about origins, licence or price in any meta value', () => {
    const metas = metaValues(HTML);
    // The head carries the viewport, the description, the keywords, the robots
    // directive, five og:* values, three twitter:* values, the application name and
    // the theme colour. Fewer than ten means the extractor stopped matching, and an
    // empty set would pass every assertion below without judging anything.
    expect(metas.length).toBeGreaterThanOrEqual(10);

    const named = metas.map((m) => `${m.key}=${m.value.slice(0, 60)}`);
    expect(named.some((n) => n.startsWith('og:title='))).toBe(true);
    expect(named.some((n) => n.startsWith('twitter:title='))).toBe(true);
    expect(named.some((n) => n.startsWith('description='))).toBe(true);

    for (const meta of metas) {
      expect(offenders(meta.value), `meta ${meta.key}: ${meta.value}`).toEqual([]);
    }
  });

  it('says in the structured data only what the product is', () => {
    const ld = jsonLd(HTML);
    const strings = jsonLdStrings(ld);
    expect(strings.length).toBeGreaterThanOrEqual(6);

    expect(ld['@type']).toBe('SoftwareApplication');
    expect(ld.name).toBe(APP_NAME);
    for (const entry of strings) {
      expect(offenders(entry.value), `JSON-LD ${entry.key}: ${entry.value}`).toEqual([]);
    }
  });

  it('still tells a crawler which release this is', () => {
    // scripts/check_version_sync.py reads this literal out of this file and fails the
    // build when it drifts from the backend version. Cleaning the block must not take
    // the one value another guard depends on with it.
    const ld = jsonLd(HTML);
    expect(typeof ld.softwareVersion).toBe('string');
    expect(ld.softwareVersion).toMatch(/^\d+\.\d+\.\d+$/);
  });

  it('keeps the attribution the licence requires, out of sight of the reader', () => {
    // AGPL-3.0 section 5 asks a modified version to keep the attribution notices
    // intact. In this file that notice is an HTML comment: preserved, and not rendered.
    // Asserting both halves is the point - a cleanup that deleted the notice to make
    // the page cleaner would break the licence, and one that moved it into a meta tag
    // would put it back in front of the reader this suite exists to protect.
    expect(HTML).toMatch(/<!--[^>]*OpenConstructionERP[\s\S]*?-->/);
    expect(offenders(HTML.replace(/<!--[\s\S]*?-->/g, ' ')).filter((w) => w !== 'free')).toEqual(
      // Nothing outside a comment carries an upstream name or an origin claim.
      [],
    );
  });
});

describe('the tab title at runtime', () => {
  const SRC = resolve(__dirname, '../..');

  /** Every `document.title = ...` assignment in the product source, with its file. */
  function titleAssignments(): Array<{ file: string; line: number; code: string }> {
    const out: Array<{ file: string; line: number; code: string }> = [];
    const walk = (dir: string) => {
      for (const entry of readdirSync(dir)) {
        const full = join(dir, entry);
        if (statSync(full).isDirectory()) {
          if (entry === '__tests__' || entry === 'locales') continue;
          walk(full);
          continue;
        }
        if (!/\.(ts|tsx)$/.test(entry) || /\.test\./.test(entry)) continue;
        const lines = readFileSync(full, 'utf-8').split('\n');
        lines.forEach((code, i) => {
          if (code.includes('document.title')) {
            out.push({ file: full.slice(SRC.length + 1), line: i + 1, code: code.trim() });
          }
        });
      }
    };
    walk(SRC);
    return out;
  }

  it('is set from the brand or the display name, and from no hardcoded claim', () => {
    const found = titleAssignments();
    // One assignment sets the tab: AppLayout, from the white-label company name when
    // there is one and the display name when there is not. Zero would mean the reader
    // above stopped finding anything, and the assertion after it would judge nothing.
    expect(found.length).toBeGreaterThanOrEqual(1);

    for (const site of found) {
      expect(offenders(site.code), `${site.file}:${site.line} ${site.code}`).toEqual([]);
    }

    const layout = found.find((f) => f.file.endsWith('AppLayout.tsx'));
    expect(layout, 'AppLayout no longer sets document.title').toBeTruthy();
    expect(layout!.code).toContain('suffix');
    // The suffix is the brand name when the workspace is white-labelled and APP_NAME
    // otherwise, read here rather than trusted: a literal product name typed into the
    // assignment would survive every other check in this file.
    const source = readFileSync(resolve(SRC, 'app/layout/AppLayout.tsx'), 'utf-8');
    expect(source).toContain('const suffix = brandName ?? APP_NAME;');
  });
});
