// Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
// SPDX-License-Identifier: AGPL-3.0-or-later
/**
 * Whether the product shows its Legal link.
 *
 * The Legal page itself is always reachable at /legal: it carries the licence,
 * the attribution notice and the source offer, and this software is distributed
 * under AGPL-3.0-or-later, so the page is not optional. What IS optional is how
 * loudly the product points at it. The requirement is one small footer link, and
 * an operator who has cleared the licence position for their own deployment - a
 * commercial licence, or legal review of the AGPL obligations - may want that
 * link to be the only mention.
 *
 * So this is a visibility setting for the link, defaulting to ON, and it is
 * deliberately NOT a way to hide the licence text: /legal keeps working with the
 * setting off, /api/source keeps serving the offer, and the copyright line stays
 * on every screen. Hiding a pointer does not remove an obligation.
 *
 * The setting is workspace data. It lives on the branding record beside the logo
 * and the support destination, so an admin turns it off once and it is off for
 * every user of that deployment - which is what "the operator cleared the licence
 * position" actually means. It used to be per-browser localStorage, where one
 * person's choice changed nobody else's screen and an operator could not set it
 * for their users at all.
 *
 * This module keeps its old shape - a hook taking a selector over
 * `{ legalLinkVisible, setLegalLinkVisible }` - so the three footers that read it
 * (login, the next login page, and the sidebar) did not have to change.
 */
import { useBrandingStore } from '@/stores/useBrandingStore';

export interface LegalVisibilityState {
  /** Default true. When false the footer link is not rendered. */
  legalLinkVisible: boolean;
  setLegalLinkVisible: (visible: boolean) => void;
}

/**
 * Read - and set - the Legal-link setting from the branding store.
 *
 * Subscribes to the two fields separately instead of handing zustand a selector
 * that builds a fresh `{ legalLinkVisible, setLegalLinkVisible }` object: a new
 * object every render never satisfies the store's Object.is comparison, and the
 * three footers would re-render in a loop.
 */
export function useLegalVisibility<T>(selector: (state: LegalVisibilityState) => T): T {
  const legalLinkVisible = useBrandingStore((s) => s.legalLinkVisible);
  const setLegalLinkVisible = useBrandingStore((s) => s.setLegalLinkVisible);
  return selector({ legalLinkVisible, setLegalLinkVisible });
}
