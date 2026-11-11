// DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
// Copyright (c) 2026 Artem Boiko / DataDrivenConstruction

/**
 * The password rules the server enforces, checked before the request is sent.
 *
 * Mirrors `_validate_strong_password` in backend/app/modules/users/schemas.py:
 * 8 to 128 characters, at least one letter and at least one digit. The
 * request schemas strip surrounding whitespace before the check, so this does
 * too. "Letter" is any Unicode letter, as Python's `str.isalpha` reads it, so
 * a password in Greek, Arabic or Chinese is not refused here and accepted
 * there. The common-password blacklist stays on the server; a password that
 * passes here can still come back as 422 for that reason alone.
 */
export const PASSWORD_MIN_LENGTH = 8;
export const PASSWORD_MAX_LENGTH = 128;
/** Length at which an acceptable password is called strong rather than medium. */
export const PASSWORD_STRONG_LENGTH = 12;

export function meetsPasswordPolicy(password: string): boolean {
  const value = password.trim();
  // Python's len() counts code points; `.length` counts UTF-16 units and would
  // read one emoji as two characters.
  const length = Array.from(value).length;
  return (
    length >= PASSWORD_MIN_LENGTH &&
    length <= PASSWORD_MAX_LENGTH &&
    /\p{L}/u.test(value) &&
    /\p{Nd}/u.test(value)
  );
}

export type PasswordStrength = 'weak' | 'medium' | 'strong';

/**
 * The word the sign-up form shows beside its three bars.
 *
 * It answers the same question the submit button asks, which the length-only
 * ladder it replaces did not: a password the server refuses is "weak" here
 * whatever its length, so the form can never read "Medium" next to a button
 * that stays disabled. The length ladder is what separates medium from strong
 * once the policy is met, exactly as before.
 */
export function passwordStrength(password: string): PasswordStrength {
  if (!meetsPasswordPolicy(password)) {
    return 'weak';
  }
  return Array.from(password.trim()).length < PASSWORD_STRONG_LENGTH ? 'medium' : 'strong';
}
