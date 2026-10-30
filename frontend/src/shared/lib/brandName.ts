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
