// DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
// Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

/**
 * The sign-up form has to say why it will not send.
 *
 * Its submit button stays disabled until the typed password meets the server's
 * policy, and the only message that names the policy is set by `handleSubmit`,
 * which a disabled button never reaches. So a reader who typed `12345678` saw a
 * strength word reading "Medium" - the old ladder measured length only - beside
 * a button that would not move, and no reason anywhere on the page. What an
 * installer does then is change something else on the form until the button
 * lights, and sign in later with the password they believe they registered. The
 * account exists, a second sign-up says so, and the password typed at the door
 * is not the one that was stored: the door answers "Invalid email or password"
 * truthfully and reads as a broken installation.
 *
 * These cases pin the two things that make the form say so. The strength word
 * answers the same question the button asks, and the rule is printed for as long
 * as the button is disabled - which is what the password-reset form beside it
 * already did through its `hint`, on a page whose button is never disabled for
 * this reason.
 *
 * `t` echoes the key, so an assertion names the branch that ran rather than a
 * sentence a copy edit could move.
 */

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { language: 'en', changeLanguage: vi.fn() },
  }),
}));

vi.mock('@/app/i18n', () => ({
  SUPPORTED_LANGUAGES: [{ code: 'en', name: 'English', flag: 'gb', country: 'gb' }],
  getLanguageByCode: () => ({ code: 'en', name: 'English', flag: 'gb', country: 'gb' }),
}));

vi.mock('../AuthBackground', () => ({
  AuthBackground: () => null,
}));

import { RegisterPage } from '../RegisterPage';

const REASON = 'register-password-reason';
const SUBMIT = 'auth.create_account';

function renderForm() {
  return render(
    <MemoryRouter initialEntries={['/register']}>
      <RegisterPage />
    </MemoryRouter>,
  );
}

function typePassword(value: string) {
  const field = document.getElementById('register-password');
  if (field === null) {
    throw new Error('the password field is not on the page');
  }
  fireEvent.change(field, { target: { value } });
}

describe('the sign-up form says why its button is disabled', () => {
  it('prints the rule while a password the server will refuse is typed', () => {
    renderForm();
    typePassword('12345678');

    expect(screen.getByTestId(REASON)).toHaveTextContent('auth.password_requirements');
    expect(screen.getByRole('button', { name: SUBMIT })).toBeDisabled();
  });

  it('makes the strength word agree with the button', () => {
    renderForm();
    typePassword('12345678');

    // Eight characters, so the length-only ladder said "Medium" here while the
    // button stayed disabled for want of a letter.
    expect(screen.getByText('auth.password_strength_weak')).toBeInTheDocument();
    expect(screen.queryByText('auth.password_strength_medium')).toBeNull();
  });

  it('stops printing the rule once the password is one the server will take', () => {
    renderForm();
    typePassword('12345678');
    expect(screen.getByTestId(REASON)).toBeInTheDocument();

    typePassword('Abcdef12');
    expect(screen.queryByTestId(REASON)).toBeNull();
    expect(screen.getByText('auth.password_strength_medium')).toBeInTheDocument();
  });

  it('says nothing about the rule before anything is typed', () => {
    renderForm();
    expect(screen.queryByTestId(REASON)).toBeNull();
  });
});
