// The ONE frontend source of the product display name.
//
// This must always equal the backend `app_name` default in
// backend/app/config.py - the agreement is enforced by
// backend/tests/unit/test_the_display_name_is_one_constant.py, which reads
// this file and fails the build when the two sides of the wire drift.
//
// Per-install white-labelling still happens through the branding store
// (useBrandingStore.companyName / CustomBranding): the brand a workspace
// sets overrides this name in the UI; this constant is what a user sees
// when no override is set.
export const APP_NAME = 'Construction AI OS';

// The ONE frontend source of the public source-code link (AGPL-3.0-or-later
// requires the corresponding source to be reachable from the product UI).
// Every "GitHub" / "Source code" link in the app reads this constant; the
// backend mirrors it (app/cli.py REPO_URL, core/self_upgrade.py RELEASES_URL).
export const SOURCE_REPO_URL = 'https://github.com/abdelnabym519-cpu/Construction-AI-OS';
