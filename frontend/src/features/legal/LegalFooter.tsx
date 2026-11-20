// Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
// SPDX-License-Identifier: AGPL-3.0-or-later
import type { CSSProperties } from 'react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';

import { useLegalVisibility } from '@/features/legal/useLegalVisibility';
import { APP_VERSION } from '@/shared/lib/version';

/**
 * The one footer the screens outside the app shell render: the running build
 * version and, beside it, a single small link to /legal.
 *
 * It exists because there were five of these and they had drifted apart. The
 * register page carried a GitHub mark in an inline <svg> next to a hardcoded
 * "Open Source · AGPL-3.0" badge; the next-generation sign-in page carried a
 * copyright line; the two password screens carried nothing at all, so a user who
 * landed on /forgot-password had no way to reach the licence from there. Only the
 * sign-in page and the sidebar said what the product now says about itself.
 *
 * One component means one answer. What it renders is deliberately small:
 *
 *   - the version, because knowing which build is live is support information,
 *     not a setting an operator should be able to hide;
 *   - the link to /legal, the only surface carrying the licence, the NOTICE
 *     attribution and the section 13 source offer. It follows the
 *     `legal_link_visible` workspace setting, so an operator who has cleared the
 *     licence position for their deployment can drop the link. The page itself is
 *     never hidden, and neither is /api/source.
 *
 * What it never renders is a claim about where the product came from: no licence
 * name, no repository, no mark. Those belong on /legal, one click away, where they
 * can be stated properly instead of as a badge.
 *
 * `className` and `style` are pass-through so each screen keeps the placement and
 * the entrance animation it already had. The default is the sign-in page's
 * treatment, which is what the sidebar matches.
 */
export function LegalFooter({ className, style }: { className?: string; style?: CSSProperties }) {
  const { t } = useTranslation();
  const legalLinkVisible = useLegalVisibility((s) => s.legalLinkVisible);

  return (
    <div
      className={
        className ??
        'mt-3 flex items-center justify-center gap-1.5 text-2xs font-mono text-content-quaternary/80 tabular-nums'
      }
      style={style}
    >
      <span>v{APP_VERSION}</span>
      {legalLinkVisible && (
        <>
          <span aria-hidden>&middot;</span>
          <Link to="/legal" className="transition-colors hover:text-content-secondary">
            {t('legal.link', { defaultValue: 'Legal' })}
          </Link>
        </>
      )}
    </div>
  );
}
