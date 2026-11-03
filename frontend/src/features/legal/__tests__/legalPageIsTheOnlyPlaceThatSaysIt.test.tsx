// Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
// SPDX-License-Identifier: AGPL-3.0-or-later
/**
 * The legal floor, asserted from the frontend lane.
 *
 * scripts/check_no_promotional_strings.py is the ratchet that stops a
 * promotional string being added. This is the other half of the same
 * requirement, and it runs where the strings are consumed: it proves the Legal
 * page can actually be reached, that every locale can render it, and that the
 * sign-in screens no longer point at the project's origins.
 *
 * These read the source files rather than importing all 45 locale bundles. A
 * bundle is 5-7 MB and importing every one of them to look for nine keys would
 * make this the slowest test in the suite for no extra certainty.
 */
import { existsSync, readdirSync, readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * Walk up from wherever the runner started until the repository root is found.
 * Counting `../` from this file's own URL is the obvious way and it broke: the
 * import failed to collect at all under the jsdom environment. Searching for a
 * path that only exists at the root works from any cwd, including a monorepo
 * runner that starts somewhere else.
 */
function findRepoRoot(): string {
  let dir = process.cwd();
  for (let i = 0; i < 8; i += 1) {
    if (existsSync(resolve(dir, 'frontend/src/app/locales'))) return dir;
    const up = dirname(dir);
    if (up === dir) break;
    dir = up;
  }
  throw new Error(`could not find the repository root above ${process.cwd()}`);
}

const repoRoot = findRepoRoot();
const localesDir = `${repoRoot}/frontend/src/app/locales`;
const read = (p: string) => readFileSync(p, 'utf8');

/** The keys the Legal page and its footer link render through. */
const LEGAL_KEYS = [
  'legal.link',
  'legal.page_title',
  'legal.notice_heading',
  'legal.source_heading',
  'legal.source_body',
  'legal.machine_readable',
  'legal.licence_heading',
  'legal.licence_reference',
  'legal.unavailable',
];

/**
 * Claims that must not reach a user from the sign-in screens. The same list the
 * Python guard uses, narrowed to what those screens could plausibly say.
 */
const PROMO = /open[\s-]?source|AGPL|GitHub|Telegram|#1|DataDrivenConstruction|Data Driven Construction/i;

/**
 * The lines of a source file that are not comments.
 *
 * Block comments have to be tracked across lines: the comments that explain why
 * a promotional surface was removed name the thing they removed, and they run to
 * several lines whose continuations do not start with a comment marker. Testing
 * only the first line of each block flagged those explanations as violations.
 */
function codeLines(text: string): string[] {
  const out: string[] = [];
  let inBlock = false;
  for (const line of text.split('\n')) {
    if (inBlock) {
      if (line.includes('*/')) inBlock = false;
      continue;
    }
    const stripped = line.trim();
    if (stripped.startsWith('/*') || stripped.startsWith('{/*')) {
      if (!line.includes('*/')) inBlock = true;
      continue;
    }
    if (stripped.startsWith('//') || stripped.startsWith('*')) continue;
    out.push(line);
  }
  return out;
}

const localeFiles = () => readdirSync(localesDir).filter((f) => f.endsWith('.ts')).sort();

/** Every `"key": "value"` pair in a locale bundle. */
function valuesOf(file: string, prefix: string): string[] {
  const out: string[] = [];
  for (const line of read(`${localesDir}/${file}`).split('\n')) {
    const m = line.match(/^\s*"([^"]+)":\s*"(.*)",?\s*$/);
    if (m && m[1]!.startsWith(prefix)) out.push(m[2]!);
  }
  return out;
}

describe('the Legal page is reachable and translatable', () => {
  it('is declared as a public route, so it works before and after sign-in', () => {
    const app = read(`${repoRoot}/frontend/src/app/App.tsx`);
    expect(app).toContain('path="/legal"');
    // A redirect to AuthedHome here would send a signed-in user away from the
    // page that carries the licence, which is the opposite of the obligation.
    const route = app.split('\n').find((l) => l.includes('path="/legal"'))!;
    expect(route).not.toContain('isAuthenticated');
    expect(route).toContain('LegalPage');
  });

  it('ships in every locale, so no language falls back to a missing key', () => {
    const files = localeFiles();
    expect(files.length).toBe(45);
    // Six of the 45 are regional variants that resolve through a base language,
    // and enUSFallsBackToEnglish.test.ts fails the build when a variant
    // duplicates a value its base already carries. So "every locale can render
    // the page" means: the file carries the key, or its base does. Asserting the
    // key is physically present in all 45 was wrong, and it is the variant files
    // that would have had to carry dead weight to satisfy it.
    const base: Record<string, string> = {
      'en-GB.ts': 'en.ts',
      'en-US.ts': 'en.ts',
      'es-CL.ts': 'es.ts',
      'es-CO.ts': 'es.ts',
      'es-MX.ts': 'es.ts',
      'pt-BR.ts': 'pt.ts',
    };
    const missing: string[] = [];
    for (const f of files) {
      const text = read(`${localesDir}/${f}`);
      const fallback = base[f] ? read(`${localesDir}/${base[f]}`) : '';
      for (const k of LEGAL_KEYS) {
        if (!text.includes(`"${k}"`) && !fallback.includes(`"${k}"`)) missing.push(`${f}: ${k}`);
      }
    }
    // check_i18n_orphan_keys.py fails the build on exactly this, from the other
    // direction; naming the gaps here makes the failure readable in the lane
    // where the page lives.
    expect(missing).toEqual([]);
  });

  it('renders the three things the licence obliges it to, and nothing promotional', () => {
    const page = read(`${repoRoot}/frontend/src/features/legal/LegalPage.tsx`);
    expect(page).toContain('/api/source'); // the section 13 offer, as the server states it
    expect(page).toContain('legal-notice'); // the NOTICE attribution
    expect(page).toContain('legal-licence'); // the licence text
    expect(page).toContain('/legal/LICENSE.txt');
    expect(page).toContain('/legal/NOTICE.txt');
    // No marketing on the one page that is allowed to talk about the licence.
    for (const line of codeLines(page)) {
      expect(line, `promotional wording on the Legal page: ${line.trim()}`).not.toMatch(
        /free open|the #1|join our|community|star us|follow us/i,
      );
    }
  });

  it('keeps the licence and the NOTICE text with the build it describes', () => {
    // These are copies of the repository-root files, served as static assets so
    // the page can show the text without bundling 100 KB of it into the app.
    for (const f of ['LICENSE.txt', 'NOTICE.txt']) {
      const shipped = read(`${repoRoot}/frontend/public/legal/${f}`);
      const original = read(`${repoRoot}/${f === 'LICENSE.txt' ? 'LICENSE' : 'NOTICE'}`);
      expect(shipped, `${f} drifted from the repository root copy`).toBe(original);
    }
  });
});

describe('the sign-in screens say nothing about their origins', () => {
  it('has no promotional string left in any login.* locale value', () => {
    const offenders: string[] = [];
    for (const f of localeFiles()) {
      for (const v of valuesOf(f, 'login.')) if (PROMO.test(v)) offenders.push(`${f}: ${v.slice(0, 60)}`);
    }
    expect(offenders).toEqual([]);
  });

  it('no longer links to the upstream repository from the login pages or the sidebar', () => {
    for (const rel of [
      'frontend/src/features/auth/LoginPage.tsx',
      'frontend/src/features/auth/LoginPageNext.tsx',
      'frontend/src/app/layout/Sidebar.tsx',
    ]) {
      const text = read(`${repoRoot}/${rel}`);
      // SOURCE_REPO_URL is the upstream repository. The only line that may still
      // mention it is a comment explaining that it was removed.
      for (const line of codeLines(text)) {
        expect(line, `${rel} still points at the upstream repo: ${line.trim()}`).not.toContain(
          'SOURCE_REPO_URL',
        );
        expect(line, `${rel} still renders a licence badge: ${line.trim()}`).not.toMatch(
          /AGPL-3\.0|open[\s-]source/i,
        );
      }
    }
  });
});
