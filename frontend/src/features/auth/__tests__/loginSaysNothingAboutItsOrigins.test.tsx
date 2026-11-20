// DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
// Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

/**
 * What the sign-in screen is allowed to say about where the product came from,
 * and what it says when the server has not finished starting.
 *
 * Two things are pinned here that a grep cannot pin. A grep over the source
 * finds strings; it cannot say whether a string is still *rendered*, and it
 * cannot see the marketing panel coming back through a component that is
 * imported rather than written inline. So these cases mount the real page and
 * read the DOM it produces.
 *
 * `t` is mocked to echo the key, exactly as `loginErrorWiring.test.tsx` does,
 * so an assertion names the branch that ran instead of a sentence that a copy
 * edit or a locale change could move. Asserting that `login.hero_desc` is
 * absent from the tree is a claim about the component; asserting that "Plan,
 * estimate, schedule..." is absent would only be a claim about one locale.
 *
 * The second half is the wording for a cold start. `backend/app/main.py`
 * documents a process that answers nothing else for 52s to 140s while it builds
 * the OpenAPI document inline, and the embedded cluster runs one worker, so a
 * first boot leaves `fetch` waiting long enough to be aborted - which REJECTS
 * rather than returning a status. The page used to report that as "unable to
 * connect to server", which is the one thing the code knows is untrue: the
 * server is there and working towards an answer. `/api/health` now decides the
 * wording, and until it has answered once a rejected call is a start in
 * progress, rendered as a status rather than as an error.
 *
 * The wording reuses `auth.preparing_workspace`, which this page already shows
 * while the desktop bootstrap runs: it is answered by all 45 locales, so the
 * notice is translated everywhere on the day it ships. A dedicated string would
 * have needed 45 translations, and shipping the English one to the other 44 is
 * exactly what `check_i18n_leak_baseline.py` exists to refuse.
 */

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { language: 'en', changeLanguage: vi.fn() },
  }),
}));

vi.mock('@/app/i18n', () => ({
  SUPPORTED_LANGUAGES: [{ code: 'en', name: 'English', flag: 'gb', country: 'gb' }],
}));

// The background animates on an interval and has no bearing on any of this;
// stubbing it keeps the render deterministic, as in the neighbouring test.
vi.mock('../AuthBackground', () => ({
  AuthBackground: () => null,
}));

import { LoginPage } from '../LoginPage';

/**
 * Mount the page with `fetch` already scripted.
 *
 * A health probe that has not answered is modelled as a promise that never
 * settles rather than one that rejects: that is what a busy boot looks like
 * from the browser, and it keeps the component's own retry timer from firing
 * after the test has finished reading the tree.
 */
function renderPage(opts: { health: 'ready' | 'starting'; login?: () => Promise<unknown> }) {
  const fetchMock = vi.fn((input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes('/api/health')) {
      return opts.health === 'ready'
        ? Promise.resolve({ ok: true, json: async () => ({ status: 'ok' }) })
        : new Promise(() => {});
    }
    if (url.includes('/auth/first-run')) {
      return Promise.resolve({ ok: true, json: async () => ({ demo_enabled: false }) });
    }
    if (url.includes('/oidc/config/')) {
      return Promise.resolve({ ok: true, json: async () => ({ enabled: false }) });
    }
    if (url.includes('/users/auth/login/')) {
      return opts.login
        ? opts.login()
        : Promise.resolve({ ok: true, json: async () => ({ access_token: 'a', refresh_token: 'r' }) });
    }
    return Promise.resolve({ ok: true, json: async () => ({}) });
  });
  vi.stubGlobal('fetch', fetchMock);
  const view = render(
    <MemoryRouter>
      <LoginPage />
    </MemoryRouter>,
  );
  return { ...view, fetchMock };
}

/** Let the mount effects and the health probe settle. */
async function flush() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('the sign-in screen', () => {
  it('renders the form and its language and theme controls, and nothing else', async () => {
    const { container } = renderPage({ health: 'ready' });
    await flush();

    // What must survive: the way in, and the two controls that are about this
    // visitor rather than about the project.
    expect(container.querySelector('form')).toBeTruthy();
    expect(container.querySelector('#login-email')).toBeTruthy();
    expect(container.querySelector('input[type="password"]')).toBeTruthy();
    expect(container.querySelector('button[type="submit"]')!.textContent).toContain('auth.login');
    expect(screen.getByRole('radiogroup')).toBeTruthy(); // theme
    expect(screen.getByText('English')).toBeTruthy(); // language
  });

  it('carries no marketing panel, no demo accounts and no way off to the project', async () => {
    const { container } = renderPage({ health: 'ready' });
    await flush();

    // The headline, its subtitle, the six-number stats row, the honeycomb of
    // modules, the two blurbs under it and the "number one open source" badge.
    for (const key of [
      'login.hero_h_a',
      'login.hero_h_b',
      'login.hero_h_c',
      'login.hero_desc',
      'login.stat_costs',
      'login.stat_langs',
      'login.stat_regions',
      'login.stat_cad',
      'login.stat_modules',
      'login.stat_sections',
      'login.mod.core',
      'login.mod.bim',
      'login.feat_local_title',
      'login.feat_ai_title',
      'login.open_source_badge',
      'login.benefit.open_source',
    ]) {
      expect(screen.queryByText(key)).toBeNull();
    }

    // The demo tile named two upstream addresses and a person who does not
    // exist, and offered a sign-in that needs no password.
    expect(screen.queryByText('auth.try_demo')).toBeNull();
    expect(container.textContent).not.toMatch(/openconstructionerp\.com/i);
    expect(container.textContent).not.toMatch(/Michael Carter/i);

    // No route off the product to the repository, the licence or a community,
    // and no word about how the product is licensed or who built it.
    expect(container.querySelectorAll('a[target="_blank"]').length).toBe(0);
    expect(container.textContent).not.toMatch(/AGPL/i);
    expect(container.textContent).not.toMatch(/open[-\s]?source/i);
    expect(container.textContent).not.toMatch(/github/i);
    expect(container.textContent).not.toMatch(/telegram/i);
  });

  it('says the server is still starting, not broken, when a cold boot has not answered health', async () => {
    const { container } = renderPage({
      health: 'starting',
      login: () => Promise.reject(new Error('aborted while the boot ran')),
    });
    await flush();

    fireEvent.submit(container.querySelector('form')!);
    await waitFor(() => expect(screen.getByTestId('login-starting')).toBeTruthy());

    expect(screen.getByTestId('login-starting').textContent).toContain('auth.preparing_workspace');
    // The point of the change: a boot in progress is not reported as a failure.
    expect(screen.queryByTestId('login-error')).toBeNull();
  });

  it('still calls a real connection failure a connection failure', async () => {
    const { container } = renderPage({
      health: 'ready',
      login: () => Promise.reject(new Error('the network dropped')),
    });
    await flush();

    fireEvent.submit(container.querySelector('form')!);
    await waitFor(() => expect(screen.getByTestId('login-error')).toBeTruthy());

    expect(screen.getByTestId('login-error').textContent).toContain('auth.connection_error');
    expect(screen.queryByTestId('login-starting')).toBeNull();
  });
});
