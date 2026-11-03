// Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
// SPDX-License-Identifier: AGPL-3.0-or-later
/**
 * The Legal page: /legal.
 *
 * This is the one place in the product that says where the software comes from
 * and what licence it is under. Everything else in the UI is silent about it, so
 * this page has to carry the whole obligation on its own:
 *
 *   1. the licence the software is distributed under;
 *   2. the attribution notice from the NOTICE file shipped with it;
 *   3. the AGPL-3.0 section 13 offer of the complete corresponding source,
 *      which for a network service has to be reachable from the product itself.
 *
 * It is deliberately plain. No marketing, no badges, no calls to action: the
 * notice, the offer and the licence text, in that order, with links out to the
 * full documents. The structured facts come from GET /api/source, which the
 * backend already serves for exactly this purpose, so the page cannot drift from
 * what the server states about itself. The licence and NOTICE text are the same
 * files that ship at the repository root, served from /legal/.
 */
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { APP_NAME } from '@/shared/lib/brandName';

/** The shape GET /api/source returns (see backend/app/main.py). */
interface SourceDisclosure {
  license?: string;
  source_code?: string;
  copyright?: string;
  notice?: string;
  projects?: Record<string, string>;
}

export default function LegalPage() {
  const { t } = useTranslation();
  const [disclosure, setDisclosure] = useState<SourceDisclosure | null>(null);
  const [failed, setFailed] = useState(false);
  const [licenseText, setLicenseText] = useState('');
  const [noticeText, setNoticeText] = useState('');

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const res = await fetch('/api/source');
        if (!res.ok) throw new Error(String(res.status));
        const data = (await res.json()) as SourceDisclosure;
        if (!cancelled) setDisclosure(data);
      } catch {
        if (!cancelled) setFailed(true);
      }
    };
    // The two documents are static assets shipped with the frontend. A missing
    // one is not fatal - the disclosure above still states the licence and the
    // offer - so each is fetched on its own and a failure just leaves it empty.
    const loadText = async (url: string, apply: (text: string) => void) => {
      try {
        const res = await fetch(url);
        if (res.ok) apply(await res.text());
      } catch {
        /* leave empty */
      }
    };
    void load();
    void loadText('/legal/LICENSE.txt', (text) => !cancelled && setLicenseText(text));
    void loadText('/legal/NOTICE.txt', (text) => !cancelled && setNoticeText(text));
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="min-h-screen bg-surface-primary text-content-primary" data-testid="legal-page">
      <main className="mx-auto max-w-3xl px-6 py-12">
        <h1 className="text-2xl font-semibold">
          {t('legal.page_title', { defaultValue: 'Legal' })}
        </h1>
        <p className="mt-2 text-sm text-content-secondary">
          {APP_NAME} &middot; {disclosure?.license ?? 'AGPL-3.0-or-later'}
        </p>

        {failed && (
          <p className="mt-6 rounded-md border border-border-light bg-surface-secondary p-3 text-sm text-content-secondary">
            {t('legal.unavailable', {
              defaultValue: 'Some of this information could not be loaded.',
            })}
          </p>
        )}

        <section className="mt-10" data-testid="legal-notice">
          <h2 className="text-base font-semibold">
            {t('legal.notice_heading', { defaultValue: 'Attribution notice' })}
          </h2>
          {disclosure?.copyright && (
            <p className="mt-3 text-sm text-content-secondary">{disclosure.copyright}</p>
          )}
          {disclosure?.notice && (
            <p className="mt-3 text-sm leading-relaxed text-content-secondary">{disclosure.notice}</p>
          )}
          {noticeText && (
            <pre className="mt-4 max-h-72 overflow-auto whitespace-pre-wrap rounded-md border border-border-light bg-surface-secondary p-3 text-xs leading-relaxed text-content-secondary">
              {noticeText}
            </pre>
          )}
        </section>

        <section className="mt-10" data-testid="legal-source-offer">
          <h2 className="text-base font-semibold">
            {t('legal.source_heading', { defaultValue: 'Source code offer' })}
          </h2>
          <p className="mt-3 text-sm leading-relaxed text-content-secondary">
            {t('legal.source_body', {
              defaultValue:
                'The complete corresponding source code for the version of this application you are using is available from the location below, as required by section 13 of the GNU Affero General Public License.',
            })}
          </p>
          {disclosure?.source_code && (
            <p className="mt-3 text-sm">
              <a
                href={disclosure.source_code}
                className="font-medium text-oe-blue hover:underline"
                rel="noopener noreferrer"
                target="_blank"
              >
                {disclosure.source_code}
              </a>
            </p>
          )}
          {disclosure?.projects && Object.keys(disclosure.projects).length > 0 && (
            <ul className="mt-4 space-y-1 text-sm text-content-secondary">
              {Object.entries(disclosure.projects).map(([name, url]) => (
                <li key={name}>
                  <span className="font-medium text-content-primary">{name}</span>{' '}
                  <a href={url} className="text-oe-blue hover:underline" rel="noopener noreferrer" target="_blank">
                    {url}
                  </a>
                </li>
              ))}
            </ul>
          )}
          <p className="mt-4 text-sm text-content-secondary">
            <a href="/api/source" className="text-oe-blue hover:underline">
              {t('legal.machine_readable', { defaultValue: 'Machine-readable disclosure' })}
            </a>
          </p>
        </section>

        <section className="mt-10" data-testid="legal-licence">
          <h2 className="text-base font-semibold">
            {t('legal.licence_heading', { defaultValue: 'Licence' })}
          </h2>
          {licenseText ? (
            <pre className="mt-4 max-h-96 overflow-auto whitespace-pre-wrap rounded-md border border-border-light bg-surface-secondary p-3 text-xs leading-relaxed text-content-secondary">
              {licenseText}
            </pre>
          ) : (
            <p className="mt-3 text-sm text-content-secondary">
              {t('legal.licence_reference', {
                defaultValue: 'The full licence text ships with this distribution as the LICENSE file.',
              })}
            </p>
          )}
        </section>
      </main>
    </div>
  );
}
