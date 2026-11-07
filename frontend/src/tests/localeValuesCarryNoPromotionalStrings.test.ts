// DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
// SPDX-License-Identifier: AGPL-3.0-or-later
/**
 * No locale value, in any of the 45 languages, advertises the project's origins.
 *
 * `scripts/check_no_promotional_strings.py` asserts this in CI and in the
 * pre-commit hook. This is the same assertion from inside the frontend suite, and
 * it exists because the two can drift: the Python guard reads the allowlist and
 * the word list from itself, so a value that becomes promotional in a language
 * nobody reviews is caught only by whichever of the two runs. A translator
 * restoring a marketing phrase in one file out of 45 is exactly the failure this
 * product has already shipped once, in the other direction.
 *
 * The allowlist is read from the same JSON the Python guard reads, so the two
 * cannot disagree about what is functional. What is functional is not vague: the
 * converter binaries and the pricing datasets are downloaded from where their
 * publisher puts them, the notification channels cannot be configured without
 * being named, and the licence statements are an AGPL-3.0 obligation rather than
 * promotion. Each carries a written reason in that file.
 */
import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

const LOCALES = join(__dirname, '..', 'app', 'locales');
// src/tests -> src -> frontend -> repository root, where scripts/ lives.
const SCRIPTS = join(__dirname, '..', '..', '..', 'scripts');

// A plain string[], not `as const`: the list is compared and pushed to, and a
// readonly literal union makes "free (marketing)" unassignable to its own result.
const FORBIDDEN: string[] = [
  'open source',
  'open-source',
  'opensource',
  'AGPL',
  'GitHub',
  'Telegram',
  'openconstructionerp',
  'DataDrivenConstruction',
  'Data Driven Construction',
  '#1',
];

/** "#1" only as a rank claim: IFC entity ids are written "#1=IFCPROJECT". */
const RANK_CLAIM = /\b(?:the|world'?s|region'?s|industry'?s)\s+#1\b|^#1(?![=\w-])/i;
/** AGPL as a word: it appears inside Danish "Tagplantrum" and Filipino "Magplano". */
const AGPL_WORD = /\bAGPL\b/;
const HEX_TAIL = /#1[0-9a-fA-F]/g;
const FREE_MARKETING =
  /\b(?:100%|completely|totally|entirely|absolutely|forever|always)\s+free\b|\bfree\s+(?:and\s+)?open[\s-]?source\b|\bfree\s+forever\b|\b(?:it'?s|it\s+is)\s+free\b/i;
/** "free text" is a field type in this domain, not a price claim. */
const FREE_NOT_MARKETING = /\bfree[\s-]+(?:text|texts|form|format|response|entry|issue|hand)\b/i;

const PAIR = /^\s*"([^"]+)":\s*"(.*)",?\s*$/;

function offenders(value: string): string[] {
  const found: string[] = FORBIDDEN.filter((w) => value.toLowerCase().includes(w.toLowerCase()));
  const at = found.indexOf('#1');
  if (at >= 0 && !RANK_CLAIM.test(value.replace(HEX_TAIL, ''))) found.splice(at, 1);
  const ag = found.indexOf('AGPL');
  if (ag >= 0 && !AGPL_WORD.test(value)) found.splice(ag, 1);
  if (FREE_MARKETING.test(value) && !FREE_NOT_MARKETING.test(value)) found.push('free (marketing)');
  return found;
}

function allowlist(): { keys: Set<string>; exact: Set<string>; sub: string[] } {
  const raw = JSON.parse(readFileSync(join(SCRIPTS, 'promotional_allowlist.json'), 'utf8')) as Record<
    string,
    string
  >;
  // Three forms, and all three have to be here or this test and the Python
  // guard disagree about the same file. A bare entry is an i18n key, which is
  // how the functional surfaces are named - integrations.telegram cannot be a
  // notification channel without saying Telegram - and the same entry doubles as
  // a substring for component literals, which have no key. An "=literal" entry
  // matches only a value that is exactly that literal, so one word can be
  // allowed as an identifier without allowing every sentence containing it.
  const keys = new Set<string>(Object.keys(raw));
  const exact = new Set<string>();
  const sub: string[] = [];
  for (const key of Object.keys(raw)) {
    if (key.startsWith('=')) exact.add(key.slice(1));
    else sub.push(key);
  }
  return { keys, exact, sub };
}

describe('locale values', () => {
  const files = readdirSync(LOCALES)
    .filter((f) => f.endsWith('.ts'))
    .sort();

  it('has every language file this test is meant to cover', () => {
    expect(files.length).toBe(45);
  });

  it(
    'carry no promotional or upstream-attribution wording in any language',
    () => {
      const { keys, exact, sub } = allowlist();
      const bad: string[] = [];
      let checked = 0;
      for (const file of files) {
        for (const line of readFileSync(join(LOCALES, file), 'utf8').split('\n')) {
          const m = PAIR.exec(line);
          // noUncheckedIndexedAccess makes every capture group string|undefined,
          // so the groups are read and narrowed rather than destructured.
          const key = m?.[1];
          const value = m?.[2];
          if (key === undefined || value === undefined) continue;
          checked += 1;
          const found = offenders(value);
          if (!found.length) continue;
          if (keys.has(key) || exact.has(value) || sub.some((pat) => value.includes(pat))) continue;
          bad.push(`${file} "${key}": ${found.join(', ')} -> ${value.slice(0, 80)}`);
        }
      }
      expect(checked).toBeGreaterThan(2_000_000);
      expect(bad, `promotional locale values:\n${bad.slice(0, 20).join('\n')}`).toEqual([]);
    },
    240_000,
  );

  it('leaves both promotional-debt baselines empty', () => {
    for (const name of ['promotional_debt_baseline.json', 'promotional_debt_baseline_backend.json']) {
      const counts = (JSON.parse(readFileSync(join(SCRIPTS, name), 'utf8')) as { counts: object }).counts;
      expect(counts, `${name} carries debt again`).toEqual({});
    }
  });
});
