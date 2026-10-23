// DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
// Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
//
// Runtime proof that /videos no longer reaches the Academy video library.
//
// theAcademyVideoLibraryIsGone.test.ts reads the sources: no feature folder,
// no <Route>, no nav row, no catalogue, no locale key. This file asks the
// running application the question a visitor asks, which is not "is the route
// declared" but "what do I get". It mounts the real <App /> inside a real
// router at /videos and reads back where the router landed.
//
// Both branches of the catch-all in App.tsx are covered, because the element
// it renders depends on the session: a signed-in visitor gets the app's own
// 404 page, a signed-out one is sent to /login. Neither is the library, and
// the second one also shows the router still resolving a retained route.
//
// The proxy allowlist still carries a `videos` segment (see
// scripts/app_route_allowlist.txt: it mirrors the live Caddyfile and is only
// re-extracted after that file changes). A segment there decides which origin
// answers a path, not what the app renders, so this is the check that matters:
// the app itself has nothing left to serve for it.
//
// Run: npx vitest run src/app/__tests__/videosRouteIsNotServedAtRuntime.test.tsx

import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, useLocation } from 'react-router-dom';

const api = vi.hoisted(() => ({
  apiGet: vi.fn(),
  apiPost: vi.fn(),
  apiPatch: vi.fn(),
  apiDelete: vi.fn(),
}));

// Hoisted, so it lands before App.tsx is evaluated: the module initialises the
// theme store at import, and jsdom has no matchMedia for it to read.
vi.hoisted(() => {
  if (typeof window !== 'undefined' && !window.matchMedia) {
    window.matchMedia = ((query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    })) as unknown as typeof window.matchMedia;
  }
});

vi.mock('@/shared/lib/api', async () => {
  const actual = await vi.importActual<typeof import('@/shared/lib/api')>('@/shared/lib/api');
  return { ...actual, ...api };
});

// English copy without the i18next runtime, the way the layout tests do it:
// `defaultValue` is what a first-paint English session reads anyway.
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, opts?: { defaultValue?: unknown }) =>
      typeof opts?.defaultValue === 'string' ? opts.defaultValue : key,
    // App.tsx subscribes to languageChanged at mount and reads the direction,
    // so the stand-in carries the surface it touches, not just `language`.
    i18n: {
      language: 'en',
      resolvedLanguage: 'en',
      languages: ['en'],
      isInitialized: true,
      changeLanguage: vi.fn(),
      on: vi.fn(),
      off: vi.fn(),
      dir: () => 'ltr',
      getResource: () => undefined,
    },
  }),
  Trans: ({ children }: { children: ReactNode }) => children,
  initReactI18next: { type: '3rdParty', init: () => {} },
}));

import App from '../App';
import { useAuthStore } from '@/stores/useAuthStore';
import { LEARN_GROUP_ID, navGroups } from '../layout/navCatalog';

/** Reads the location the router actually settled on. */
function LocationProbe() {
  const { pathname } = useLocation();
  return <div data-testid="route">{pathname}</div>;
}

async function mountAt(path: string) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: 0 } },
  });
  const view = render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <App />
        <LocationProbe />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  // The shell renders at once and the queries it fires settle a tick later.
  // Draining inside act keeps those updates in the test instead of landing
  // after it, which React reports as a suspended resource resolving outside.
  await act(async () => {
    await Promise.resolve();
  });
  return view;
}

/** What the router resolved to, as a path. */
function settledRoute(): string {
  return screen.getByTestId('route').textContent ?? '';
}

beforeEach(() => {
  // Nothing may reach the network. `null` and not `undefined`: react-query
  // treats an undefined query result as a broken queryFn and says so, which
  // would fill the run with noise about an API layer this test is not about.
  api.apiGet.mockResolvedValue(null);
  api.apiPost.mockResolvedValue(null);
  api.apiPatch.mockResolvedValue(null);
  api.apiDelete.mockResolvedValue(null);
});

afterEach(() => {
  cleanup();
  useAuthStore.setState({ isAuthenticated: false, authPending: false });
  vi.clearAllMocks();
});

describe('GET /videos, asked of the running app', () => {
  it('answers the app 404 page to a signed-in visitor, and stays on the path', async () => {
    useAuthStore.setState({ isAuthenticated: true, authPending: false });
    await mountAt('/videos');

    // The router kept the path: nothing matched it, so the catch-all rendered
    // in place rather than moving the visitor somewhere else.
    expect(settledRoute()).toBe('/videos');
    expect(screen.getByText('404')).toBeTruthy();
    expect(screen.getByText('Page not found')).toBeTruthy();

    // And what rendered is the shared 404, not a video library: no poster
    // grid, no player, no channel wording anywhere on the page.
    expect(screen.queryByText(/Academy/i)).toBeNull();
    expect(screen.queryByText(/Video guides/i)).toBeNull();
    expect(screen.queryByText(/Tutorial videos/i)).toBeNull();
    expect(document.querySelectorAll('video, iframe').length).toBe(0);
  });

  it('sends a signed-out visitor to /login, where the sign-in page really renders', async () => {
    useAuthStore.setState({ isAuthenticated: false, authPending: false });
    await mountAt('/videos');

    // The catch-all's other branch: a redirect, so the path changes and a
    // route that still exists takes over. This is also the check that the
    // router is healthy after the removal - it resolves a retained route.
    expect(settledRoute()).toBe('/login');
    expect(screen.queryByText('404')).toBeNull();
    expect(screen.queryByText(/Academy/i)).toBeNull();
    expect(document.querySelectorAll('video, iframe').length).toBe(0);
  });

  it('resolves a retained deep route to itself, not to the 404', async () => {
    // Control for the two above: the catch-all is what /videos falls into,
    // not what every path falls into. /share/:token is public and renders
    // without a session, so it needs no auth state to prove the point.
    useAuthStore.setState({ isAuthenticated: false, authPending: false });
    await mountAt('/share/abc123');

    expect(settledRoute()).toBe('/share/abc123');
  });
});

describe('the library is not loadable at runtime either', () => {
  it('fails to resolve the page module when anything asks for it', async () => {
    // Assembled so no build-time resolver sees a literal path to check; the
    // question is what the runtime does when something still asks for it.
    const specifier = ['@/features', 'videos', 'VideosPage'].join('/');
    await expect(vi.importActual(specifier)).rejects.toThrow();
  });

  it('offers no navigation row that leads there', () => {
    const everyRow = navGroups.flatMap((group) => group.items.map((item) => item.to));
    expect(everyRow.length).toBeGreaterThan(100);
    expect(everyRow).not.toContain('/videos');
    expect(everyRow.some((to) => to.includes('video'))).toBe(false);

    // The card the row used to sit in is still there, with what is left.
    const learn = navGroups.find((group) => group.id === LEARN_GROUP_ID);
    expect(learn?.items.map((item) => item.to)).toEqual(['/cases', '/packs']);
  });
});
