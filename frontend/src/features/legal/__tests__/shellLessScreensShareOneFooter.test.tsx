// Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
// SPDX-License-Identifier: AGPL-3.0-or-later
/**
 * One footer for every screen that has no app shell.
 *
 * The register page used to render a GitHub mark - an inline <svg> holding the
 * octocat path - beside a hardcoded "Open Source · AGPL-3.0" badge, next to the
 * same Legal link every other screen showed. It survived two passes of
 * scripts/check_no_promotional_strings.py because the guard read quoted string
 * literals and JSX attributes line by line, and that badge was neither: it was
 * bare JSX text spread over two lines between </svg> and <Link>, so no quoted
 * literal ever existed for the line-based reader to find. The guard now extracts
 * assembled strings (spanning JSX text, joined literals, template literals) and
 * knows the octocat path as an icon signature; this is the half of the
 * requirement that runs where the markup lives.
 *
 * What is asserted:
 *
 *   1. the shared component renders the build version and exactly one link to
 *      /legal, and renders no licence name, no repository and no mark;
 *   2. the `legal_link_visible` setting drops the link and keeps the version;
 *   3. all six screens outside the app shell render that component and carry no
 *      footer markup of their own;
 *   4. no component anywhere under frontend/src holds the octocat path.
 *
 * The password screens are in the list because they had no footer at all: a user
 * who arrived from the reset e-mail could not reach the licence from there.
 */
import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

/** `t` returns the defaultValue, so an assertion reads as the label a user sees. */
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, opts?: { defaultValue?: string }) => opts?.defaultValue ?? key,
    i18n: { language: 'en', changeLanguage: vi.fn() },
  }),
}));

/** Pinned, as visual-regression.test.tsx does, so a release cannot move the test. */
vi.mock('@/shared/lib/version', async () => {
  const actual =
    await vi.importActual<typeof import('@/shared/lib/version')>('@/shared/lib/version');
  return { ...actual, APP_VERSION: '0.0.0-test' };
});

import { LegalFooter } from '../LegalFooter';
import { useBrandingStore } from '@/stores/useBrandingStore';

/** Walk up until the repository root is found, so the test works from any cwd. */
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
const srcDir = `${repoRoot}/frontend/src`;
const read = (p: string) => readFileSync(p, 'utf8');

/**
 * The screens that render outside the app shell, so each needs its own footer.
 * The buyer portal is here because it is read by people who are not signed in to
 * the product at all, and its footer linked straight at the licence text.
 */
const SHELL_LESS_SCREENS = [
  'frontend/src/features/auth/LoginPage.tsx',
  'frontend/src/features/auth/LoginPageNext.tsx',
  'frontend/src/features/auth/RegisterPage.tsx',
  'frontend/src/features/auth/ForgotPasswordPage.tsx',
  'frontend/src/features/auth/ResetPasswordPage.tsx',
  'frontend/src/features/buyer-portal/BuyerPortalPage.tsx',
];

/** The app shell's own footer, which the shared component's default matches. */
const SHELL_FOOTER = 'frontend/src/app/layout/Sidebar.tsx';

/**
 * What a footer must not say. The same words scripts/check_no_promotional_strings.py
 * forbids, narrowed to what a footer could plausibly carry, plus the two shapes
 * that are a claim without being a word: a link to the licence text, and a
 * copyright line naming the product.
 */
const FORBIDDEN_IN_A_FOOTER =
  /AGPL|open[\s-]?source|SOURCE_REPO_URL|gnu\.org|github\.com|© 2026|M12 0c-6\.626/i;

/**
 * The lines of a source file that are not comments.
 *
 * Block comments are tracked across lines because the comments explaining why a
 * promotional surface was removed name the thing they removed. Asserting on the
 * raw file would fail on its own explanation.
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

function renderFooter(props: { className?: string } = {}) {
  return render(
    <MemoryRouter>
      <LegalFooter {...props} />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  useBrandingStore.setState({ legalLinkVisible: true });
});

describe('the shared footer', () => {
  it('renders the running build version and exactly one link to /legal', () => {
    renderFooter();
    expect(screen.getByText('v0.0.0-test')).toBeTruthy();

    const links = screen.getAllByRole('link');
    expect(links).toHaveLength(1);
    expect(links[0]?.getAttribute('href')).toBe('/legal');
    expect(links[0]?.textContent).toBe('Legal');
  });

  it('renders no licence name, no repository and no mark', () => {
    const { container } = renderFooter();
    const html = container.innerHTML.toLowerCase();
    for (const claim of ['agpl', 'open source', 'open-source', 'github', 'gnu.org', '<svg']) {
      expect(html, `the footer still carries ${claim}`).not.toContain(claim);
    }
    // Non-vacuity: the version and the link are both in there, so the assertions
    // above are reading a rendered footer and not an empty container.
    expect(html).toContain('v0.0.0-test');
    expect(html).toContain('/legal');
  });

  it('drops the link and keeps the version when the workspace setting is off', () => {
    useBrandingStore.setState({ legalLinkVisible: false });
    renderFooter();
    expect(screen.queryByRole('link')).toBeNull();
    // The version is not the setting's business: knowing which build is live is
    // support information, so it stays when an operator hides the link.
    expect(screen.getByText('v0.0.0-test')).toBeTruthy();
  });

  it('keeps the placement a screen passes in, so no layout was rearranged', () => {
    const { container } = renderFooter({ className: 'relative mt-auto pt-10' });
    expect(container.firstElementChild?.getAttribute('class')).toBe('relative mt-auto pt-10');
  });
});

describe('every screen outside the app shell renders it', () => {
  it('imports and renders the shared component', () => {
    expect(SHELL_LESS_SCREENS).toHaveLength(6);
    for (const rel of SHELL_LESS_SCREENS) {
      const text = read(`${repoRoot}/${rel}`);
      expect(text, `${rel} does not import the shared footer`).toContain(
        "import { LegalFooter } from '@/features/legal/LegalFooter';",
      );
      expect(text, `${rel} does not render the shared footer`).toContain('<LegalFooter');
    }
  });

  it('carries no footer claim of its own in any line that is not a comment', () => {
    let scanned = 0;
    const offenders: string[] = [];
    for (const rel of [...SHELL_LESS_SCREENS, SHELL_FOOTER]) {
      const lines = codeLines(read(`${repoRoot}/${rel}`));
      scanned += lines.length;
      for (const line of lines) {
        if (FORBIDDEN_IN_A_FOOTER.test(line)) offenders.push(`${rel}: ${line.trim().slice(0, 120)}`);
      }
    }
    // A scan that read nothing would pass, so assert it read the files.
    expect(scanned).toBeGreaterThan(2000);
    expect(offenders).toEqual([]);
  });

  it('the shell footer states the version and the same single link', () => {
    const lines = codeLines(read(`${repoRoot}/${SHELL_FOOTER}`));
    expect(lines.some((l) => l.includes('v{APP_VERSION}'))).toBe(true);
    expect(lines.some((l) => l.includes('href="/legal"'))).toBe(true);
    expect(lines.filter((l) => l.includes('legalLinkVisible')).length).toBeGreaterThan(0);
  });
});

describe('no component holds the mark the register badge was built from', () => {
  it('finds no octocat path anywhere under frontend/src', () => {
    const OCTOCAT = 'M12 0c-6.626 0-12 5.373-12 12 0 5.302 3.438 9.8 8.207 11.387';
    // Test files are skipped, exactly as the Python guard skips them: this one has
    // to hold the path as a literal to look for it, and scanning itself would make
    // it fail on its own search string.
    const files = readdirSync(srcDir, { recursive: true })
      .map((f) => String(f))
      .filter((f) => f.endsWith('.tsx') || f.endsWith('.ts'))
      .filter((f) => !f.includes('.test.') && !f.includes('__tests__'));
    // Non-vacuity: this is the whole frontend source tree, not an empty glob.
    expect(files.length).toBeGreaterThan(1000);

    const holding = files.filter((f) => read(resolve(srcDir, f)).includes(OCTOCAT));
    expect(holding).toEqual([]);
  });
});
