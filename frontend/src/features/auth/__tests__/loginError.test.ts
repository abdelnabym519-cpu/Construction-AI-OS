import { existsSync, readFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { describe, it, expect } from 'vitest';
import {
  PENDING_ACTIVATION_DETAIL,
  loginFailureKind,
  loginFailureKindFromResponse,
} from '../loginError';

/**
 * The case this exists for: a backend that is down, behind a proxy.
 *
 * `fetch` resolves normally for a 502 - it is a response, not a network
 * failure - so the `catch` branch labelled "unable to connect" is never
 * reached, and before this helper every such status fell through to the
 * credentials wording. On 2026-08-29 our own demo application was down for a
 * day behind a proxy answering 502, and the sign-in screen told everyone who
 * tried that their password was wrong.
 */
describe('loginFailureKind', () => {
  it('calls a proxy 502 unavailable, because nothing read the password', () => {
    expect(loginFailureKind(502)).toBe('unavailable');
  });

  it.each([500, 501, 503, 504])('treats %i as unavailable', (status) => {
    expect(loginFailureKind(status)).toBe('unavailable');
  });

  it.each([400, 401, 403, 404, 422, 429])(
    'treats %i as a credentials answer, because the server read the request',
    (status) => {
      expect(loginFailureKind(status)).toBe('credentials');
    },
  );

  it('does not depend on where the 5xx boundary is written', () => {
    // 499 and 500 sit either side of the only comparison in the helper, so
    // this pair is what would catch an off-by-one rather than restating it.
    expect(loginFailureKind(499)).toBe('credentials');
    expect(loginFailureKind(500)).toBe('unavailable');
  });
});

/**
 * What the status split above left open.
 *
 * A 5xx is only one of the ways something in front of the application answers
 * for it. A WAF blocks the request with 403, a CDN throttles it with 429, a
 * proxy that lost the route replies 404, and each of them serves its own page.
 * `res.json()` throws on a page, so both sign-in screens are already holding
 * `null` at that point, and `null` fell straight through to "Invalid email or
 * password" - about a request that never reached anything holding a password.
 */
describe('loginFailureKindFromResponse', () => {
  const detail = 'Invalid email or password';

  it.each([403, 404, 429])(
    'calls %i unavailable when the body carried no message, because nothing we own answered it',
    (status) => {
      expect(loginFailureKindFromResponse(status, null)).toBe('unavailable');
    },
  );

  it('still calls a 401 carrying our own detail a credentials answer', () => {
    // The control that stops the rule swallowing real refusals. Every 401 our
    // login endpoint raises carries this detail, so a wrong password keeps its
    // own wording rather than being excused as an outage.
    expect(loginFailureKindFromResponse(401, detail)).toBe('credentials');
  });

  it('keeps a wrong password and an unknown account indistinguishable', () => {
    // The backend answers both with one status and one detail. Nothing here
    // may split them, and the only way to be sure is to ask about the pair.
    expect(loginFailureKindFromResponse(401, detail)).toBe(loginFailureKindFromResponse(401, detail));
    expect(loginFailureKindFromResponse(401, detail)).toBe('credentials');
  });

  // Both of these are messages the server really sends, not invented ones. The
  // 429 sentence is copied from the throttle our own login route raises, in
  // backend/app/modules/users/router.py, and it matters that it is a
  // HTTPException with a string detail: it reaches the browser as JSON, so a
  // burst of wrong passwords keeps the throttle wording and cannot be excused
  // as an outage. Only a 429 from something in front of us, serving its own
  // page, arrives with nothing to read.
  it.each([
    [422, 'body.email: value is not a valid email address'],
    [429, 'Too many login attempts. Please wait a minute and try again.'],
  ])('treats %i with a detail as an answer about the request', (status, message) => {
    expect(loginFailureKindFromResponse(status as number, message as string)).toBe('credentials');
  });

  it('leaves the 5xx half alone whether or not a body came with it', () => {
    expect(loginFailureKindFromResponse(502, null)).toBe('unavailable');
    expect(loginFailureKindFromResponse(500, 'Internal server error')).toBe('unavailable');
  });

  it('parts company with the status-only rule exactly at the gap it was written for', () => {
    // Naming the delta rather than the result: the status half has to keep
    // saying 'credentials' here, and the whole point of the second half is
    // that it overrules it. A revert of either one collapses this pair.
    expect(loginFailureKind(403)).toBe('credentials');
    expect(loginFailureKindFromResponse(403, null)).toBe('unavailable');
  });

  it('reads an empty detail as no detail', () => {
    // The screens fall back on a falsy `parsed`, so a message that is present
    // but empty has to classify the same way as one that is absent, otherwise
    // the two halves disagree about the same response.
    expect(loginFailureKindFromResponse(403, '')).toBe('unavailable');
  });
});

/**
 * The one refusal that can be split out safely, and the reason it has to be.
 *
 * `registration_mode` defaults to `admin-approve`, so every account but the
 * bootstrap one on an install is stored dormant. `login` used to answer a
 * dormant account with the same generic 401 it uses for a wrong password, which
 * is a claim about the password - the one thing that had been checked and was
 * right - and sent the person into a retry loop that cannot end. It now answers
 * 403 with a stable code, and only after the password verified.
 *
 * So these cases pin both halves of the trade: the dormant account is told the
 * truth about itself, and everybody who cannot prove they own it still gets one
 * indistinguishable refusal.
 */
describe('the pending-account split', () => {
  const generic = 'Invalid email or password';

  it('reads the code the backend sends as pending', () => {
    expect(loginFailureKindFromResponse(403, PENDING_ACTIVATION_DETAIL)).toBe('pending');
  });

  it('lets the code decide rather than the status it arrives on', () => {
    // A server still answering 401 for a dormant account is the behaviour this
    // split was written to replace, and a reader of it is still told the truth
    // rather than being sent back to their keyboard.
    expect(loginFailureKindFromResponse(401, PENDING_ACTIVATION_DETAIL)).toBe('pending');
  });

  it('still lets a 5xx overrule the code', () => {
    // Whatever a proxy serves on its way to saying "the backend is down" is not
    // an opinion about the account, so the outage wording keeps priority.
    expect(loginFailureKindFromResponse(503, PENDING_ACTIVATION_DETAIL)).toBe('unavailable');
  });

  it.each([
    ['a dormant account and a wrong password', 401, 'wrong-password-1'],
    ['an active account and a wrong password', 401, generic],
    ['an address nobody registered', 401, generic],
  ])('keeps %s a credentials answer', (_label, status, detail) => {
    // The control that keeps the split from becoming an enumeration oracle: only
    // the exact code moves off the generic wording, and only the server sends
    // that, only after the password verified.
    expect(loginFailureKindFromResponse(status, detail)).toBe('credentials');
  });

  it('does not read a sentence as the code', () => {
    // A message from an older server, or from anything standing in front of one,
    // keeps its own wording: the split is on a value, not on prose that happens
    // to mention activation.
    expect(loginFailureKindFromResponse(403, 'Account is deactivated.')).toBe('credentials');
  });
});

/**
 * That the two copies of the code are the same string.
 *
 * The backend keeps its copy as `ACCOUNT_PENDING_ACTIVATION` in the users
 * service. Nothing at runtime compares them - the browser only ever sees the
 * value on the wire - so a rename on either side would leave the sign-in screen
 * showing a dormant account the raw code, or a sentence in one language only.
 * Both halves are green alone; this is the file that fails.
 */
describe('the code the backend sends', () => {
  const root = [resolve(process.cwd(), '..'), process.cwd()].find((p) =>
    existsSync(join(p, 'backend/app/modules/users/service.py')),
  );

  it('is the value the users service raises', () => {
    expect(root, 'could not locate the repo root from the test working directory').toBeTruthy();
    const service = readFileSync(join(root!, 'backend/app/modules/users/service.py'), 'utf8');
    const declared = service.match(/^ACCOUNT_PENDING_ACTIVATION = "([^"]+)"$/m);

    expect(declared, 'ACCOUNT_PENDING_ACTIVATION is no longer a plain string constant').toBeTruthy();
    expect(declared![1]).toBe(PENDING_ACTIVATION_DETAIL);
  });

  it('is acted on by both sign-in screens, the mounted one included', () => {
    expect(root, 'could not locate the repo root from the test working directory').toBeTruthy();
    // `/login` renders LoginPage and `/login-next` renders LoginPageNext; both
    // post to the same endpoint, so a branch added to one and not the other
    // would leave the route people actually reach showing the raw code. The
    // render test beside this file covers LoginPageNext only, which is why the
    // mounted screen is read rather than rendered here.
    for (const page of ['LoginPage.tsx', 'LoginPageNext.tsx']) {
      const source = readFileSync(join(root!, `frontend/src/features/auth/${page}`), 'utf8');
      expect(source, `${page} no longer asks the classifier`).toContain('loginFailureKindFromResponse(');
      expect(source, `${page} does not branch on the pending kind`).toContain("kind === 'pending'");
      // The literal key, not a computed one: scripts/check_i18n_computed_keys.py
      // baselines computed keys, and a key the orphan guard cannot resolve is a
      // key that reaches 45 languages in English.
      expect(source, `${page} does not translate the pending refusal`).toContain(
        "t('auth.registration_pending_activation'",
      );
    }
  });

  it('is raised only after the password verified', () => {
    expect(root, 'could not locate the repo root from the test working directory').toBeTruthy();
    const service = readFileSync(join(root!, 'backend/app/modules/users/service.py'), 'utf8');
    const login = service.slice(service.indexOf('    async def login('));
    const body = login.slice(0, login.search(/\n {4}async def /));

    // The order is the whole safety argument: a caller learns the account is
    // dormant only once they have proved they hold its password.
    expect(body.indexOf('_password_matches')).toBeGreaterThan(-1);
    expect(body.indexOf('_password_matches')).toBeLessThan(body.indexOf('ACCOUNT_PENDING_ACTIVATION'));
  });
});
