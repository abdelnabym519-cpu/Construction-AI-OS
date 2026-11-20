import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { act } from 'react';

/**
 * Who is offered the white-label editor on the sign-in screen.
 *
 * The pencil beside the product name opened `BrandingEditorModal` for anybody who
 * could reach /login, which is everybody: the sign-in page is the one screen an
 * anonymous visitor is guaranteed to see. What the editor does is write the
 * branding record, and writing it is `PUT /api/v1/branding/` behind
 * `RequireRole("admin")`, so the control offered an anonymous visitor a form the
 * server refuses - a logo upload that appears to save and then does not, on the
 * screen where a first impression of the product is formed.
 *
 * It is also the screen where the offer does the most damage. An operator who has
 * not signed in yet is looking at the default brand, and the pencil reads as an
 * invitation to change it; the change they would be making is to somebody else's
 * workspace, or to none at all.
 *
 * So the control follows the permission it drives: rendered for a signed-in admin,
 * and not rendered for an anonymous visitor or for a signed-in user without the
 * role. `CustomBranding.tsx` in the sidebar keeps the same editor reachable inside
 * the app, where the session is established and its `canPersist` already turns on
 * the same role, so nothing an admin could do is lost by taking the pre-auth
 * affordance away.
 *
 * The gate reads `isAuthenticated` and `userRole` together rather than the role
 * alone. `userRole` is the server-confirmed role and the store's own comment warns
 * against treating a JWT-decoded role as authoritative; `isAuthenticated` is what
 * says a session exists to hold it. Reading only the role would show the pencil to
 * a tab that is mid-bootstrap or mid-handover, before either field has settled.
 *
 * `t` echoes the key, as `loginSaysNothingAboutItsOrigins.test.tsx` does, so an
 * assertion names the branch that ran instead of a sentence in one locale.
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

vi.mock('../AuthBackground', () => ({
  AuthBackground: () => null,
}));

// A sentinel rather than the real modal: what is being judged is whether the
// control is offered and whether it opens the editor, not what the editor renders.
// The real one uploads files and writes branding, which would need the whole API
// scripted to prove a button's visibility.
vi.mock('@/app/layout/CustomBranding', () => ({
  BrandingEditorModal: () => <div data-testid="brand-editor-sentinel" />,
  CustomBranding: () => null,
}));

import { LoginPage } from '../LoginPage';
import { useAuthStore } from '@/stores/useAuthStore';
import { APP_NAME } from '@/shared/lib/brandName';

/** The label the control carries, which `t` renders as the key itself. */
const CONTROL = 'login.brand_edit';

function renderPage() {
  const fetchMock = vi.fn((input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes('/api/health')) {
      return Promise.resolve({ ok: true, json: async () => ({ status: 'ok' }) });
    }
    if (url.includes('/auth/first-run')) {
      return Promise.resolve({ ok: true, json: async () => ({ demo_enabled: false }) });
    }
    if (url.includes('/oidc/config/')) {
      return Promise.resolve({ ok: true, json: async () => ({ enabled: false }) });
    }
    return Promise.resolve({ ok: true, json: async () => ({}) });
  });
  vi.stubGlobal('fetch', fetchMock);
  return render(
    <MemoryRouter>
      <LoginPage />
    </MemoryRouter>,
  );
}

/** Let the mount effects and the health probe settle. */
async function flush() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

/** Put the store in the session shape the case is about, before the page mounts. */
function signInAs(role: string | null) {
  useAuthStore.setState({
    isAuthenticated: role !== null,
    authPending: false,
    userRole: role,
    accessToken: null,
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
  signInAs(null);
});

describe('the white-label control on the sign-in screen', () => {
  it('is not offered to an anonymous visitor, who still sees the product name', async () => {
    signInAs(null);
    const { container } = renderPage();
    await flush();

    expect(screen.queryByLabelText(CONTROL)).toBeNull();
    expect(screen.queryByTitle(CONTROL)).toBeNull();
    expect(container.querySelector('[data-testid="brand-editor-sentinel"]')).toBeNull();
    // Hiding the control must not take the brand block with it: the default brand
    // is what an anonymous visitor is meant to see.
    expect(screen.getByText(APP_NAME)).toBeTruthy();
  });

  it('is not offered to a signed-in user without the admin role', async () => {
    signInAs('viewer');
    renderPage();
    await flush();

    expect(screen.queryByLabelText(CONTROL)).toBeNull();
    expect(screen.queryByTitle(CONTROL)).toBeNull();
  });

  it('is offered to a signed-in admin, and opens the editor', async () => {
    signInAs('admin');
    const { container } = renderPage();
    await flush();

    const control = screen.getByLabelText(CONTROL);
    expect(control).toBeTruthy();
    expect(container.querySelector('[data-testid="brand-editor-sentinel"]')).toBeNull();

    fireEvent.click(control);
    await flush();
    expect(container.querySelector('[data-testid="brand-editor-sentinel"]')).toBeTruthy();
  });

  it('does not leave the editor mounted for a visitor who is not an admin', async () => {
    // The role can change under a mounted page: the store refreshes it from
    // /users/me/, and an admin demoted mid-session stops being one. The modal is
    // gated on the same condition as the control, so losing the role closes the
    // offer instead of leaving a write form open that the server now refuses.
    signInAs('admin');
    const { container } = renderPage();
    await flush();
    fireEvent.click(screen.getByLabelText(CONTROL));
    await flush();
    expect(container.querySelector('[data-testid="brand-editor-sentinel"]')).toBeTruthy();

    act(() => {
      useAuthStore.setState({ isAuthenticated: false, userRole: null });
    });
    await flush();
    expect(container.querySelector('[data-testid="brand-editor-sentinel"]')).toBeNull();
  });
});
