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
 * setting off, and the copyright line stays on every screen.
 *
 * Persistence is localStorage only. It is a per-browser presentation choice, not
 * workspace data, so it does not go through /api/v1/branding/ and needs no
 * schema change. Making it a server-backed workspace setting is listed as a
 * follow-up in the Part C report.
 */
import { create } from 'zustand';

const STORAGE_KEY = 'oe_legal_link_visible_v1';

/** Absent or unparseable storage means the default, which is visible. */
function readStored(): boolean {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    return raw === null ? true : raw === 'true';
  } catch {
    // Private browsing and the desktop build can both throw on localStorage.
    // The default is the safe one: show the link.
    return true;
  }
}

export interface LegalVisibilityState {
  /** Default true. When false the footer link is not rendered. */
  legalLinkVisible: boolean;
  setLegalLinkVisible: (visible: boolean) => void;
}

export const useLegalVisibility = create<LegalVisibilityState>((set) => ({
  legalLinkVisible: readStored(),
  setLegalLinkVisible: (visible) => {
    try {
      window.localStorage.setItem(STORAGE_KEY, String(visible));
    } catch {
      // Keeping the in-memory value is enough; the next load falls back to ON.
    }
    set({ legalLinkVisible: visible });
  },
}));
