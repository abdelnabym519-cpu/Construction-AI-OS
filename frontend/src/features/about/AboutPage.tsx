// DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
// Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
/**
 * AboutPage — Application info, license and release history.
 */

import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useLocation, useNavigate } from 'react-router-dom';
import {
  Shield, BookOpen, Award,
  ArrowRight, HandCoins,
  Heart, ChevronDown,
} from 'lucide-react';
import clsx from 'clsx';
import { Card, Button, Badge, Breadcrumb, DismissibleInfo, IntroRichText } from '@/shared/ui';
import { SUPPORTED_LANGUAGES } from '@/app/i18n';
import { PageHeader } from '@/shared/ui/PageHeader';
import { APP_VERSION } from '@/shared/lib/version';
import { APP_NAME, SOURCE_REPO_URL } from '@/shared/lib/brandName';
import { CheckForUpdatesButton, UpdateInlineNotice } from '@/shared/ui/UpdateChecker';
import { Changelog, getRecentReleases } from './Changelog';
import { ThirdPartyLicenses } from './ThirdPartyLicenses';
import { CONTRIBUTORS, SPONSORS, acknowledgedUrl, type Acknowledged } from './acknowledgments';

const DOCS_URL = `${SOURCE_REPO_URL}/tree/main/docs`;

// Warm palette for avatar monograms - a deterministic pick keeps each person's
// fallback color stable across renders while staying varied across the wall.
const MONOGRAM_COLORS = [
  'bg-rose-500',
  'bg-amber-500',
  'bg-emerald-500',
  'bg-sky-500',
  'bg-violet-500',
  'bg-orange-500',
  'bg-teal-500',
  'bg-fuchsia-500',
];

function monogramColor(seed: string): string {
  let hash = 0;
  for (let i = 0; i < seed.length; i += 1) {
    hash = (hash * 31 + seed.charCodeAt(i)) >>> 0;
  }
  return MONOGRAM_COLORS[hash % MONOGRAM_COLORS.length] ?? 'bg-slate-500';
}

function monogramInitials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length >= 2) return ((parts[0]?.[0] ?? '') + (parts[1]?.[0] ?? '')).toUpperCase();
  return name.trim().slice(0, 2).toUpperCase();
}

/**
 * One acknowledgment tile: the person's GitHub avatar above their name, linking
 * to their profile. When the avatar image cannot load (no account image, network
 * block) it falls back to a colored initials monogram so every tile still reads
 * cleanly. Sponsors render slightly larger via the `prominent` flag.
 */
function AckAvatar({ entry, prominent = false }: { entry: Acknowledged; prominent?: boolean }) {
  const [imgFailed, setImgFailed] = useState(false);
  const url = acknowledgedUrl(entry);
  // Avatars are saved into public/contributors/<handle>.png (fetched from
  // GitHub) so they serve same-origin and are not blocked by the image CSP,
  // and the wall still shows faces offline. The initials monogram below
  // remains the fallback for any handle without a saved image.
  const avatarSrc = entry.handle ? `/contributors/${entry.handle}.png` : undefined;
  const showImg = Boolean(avatarSrc) && !imgFailed;

  const body = (
    <>
      <span
        className={clsx(
          'relative flex items-center justify-center overflow-hidden rounded-full shadow-sm transition-transform group-hover:scale-105',
          prominent ? 'h-24 w-24 ring-2 ring-emerald-400/60' : 'h-11 w-11 ring-1 ring-border-light',
        )}
      >
        {showImg ? (
          <img
            src={avatarSrc}
            alt={entry.name}
            loading="lazy"
            onError={() => setImgFailed(true)}
            className="h-full w-full object-cover"
          />
        ) : (
          <span
            className={clsx(
              'flex h-full w-full items-center justify-center font-bold text-white',
              prominent ? 'text-xl' : 'text-2xs',
              monogramColor(entry.name),
            )}
          >
            {monogramInitials(entry.name)}
          </span>
        )}
      </span>
      <span className="mt-1 w-full truncate text-center text-2xs text-content-secondary group-hover:text-content-primary transition-colors">
        {entry.name}
      </span>
    </>
  );

  const cls = clsx(
    'group flex flex-col items-center rounded-lg p-1 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-oe-blue focus-visible:ring-offset-2',
    prominent ? 'w-28' : 'w-[3.75rem]',
  );

  return url ? (
    <a href={url} target="_blank" rel="noopener noreferrer" title={entry.name} className={cls}>
      {body}
    </a>
  ) : (
    <span className={cls} title={entry.name}>
      {body}
    </span>
  );
}

export function AboutPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { hash, key } = useLocation();

  // The changelog is long, so on the About page it starts collapsed to a
  // single latest release with a toggle to reveal the full history. Deep
  // links (the header "View all" and /about#changelog) expand it on arrival.
  const [changelogExpanded, setChangelogExpanded] = useState(false);

  // When arriving via /about#changelog (from the header News button or the
  // sidebar What's-new card) scroll the changelog section into view. We key the
  // effect on the router location key as well as the hash: react-router mints a
  // fresh key on every navigation, so clicking "View in-app changelog" again
  // while already at /about#changelog (same hash value, after the user scrolled
  // away) still re-fires the scroll instead of silently doing nothing.
  useEffect(() => {
    if (hash !== '#changelog') return;
    // A deep link to the changelog should reveal the full list, not the
    // collapsed single-release preview.
    setChangelogExpanded(true);
    // Defer to the next frame so the changelog section is laid out first.
    const frame = window.requestAnimationFrame(() => {
      const changelog = document.querySelector('[data-changelog-anchor]');
      if (changelog) changelog.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [hash, key]);

  // A deep link to the contributor wall (/about#community) scrolls the
  // "Built with our community" card into view.
  useEffect(() => {
    if (hash !== '#community') return;
    const frame = window.requestAnimationFrame(() => {
      const community = document.querySelector('[data-community-anchor]');
      if (community) community.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [hash, key]);

  return (
    <div className="space-y-5 animate-fade-in">
      {/* Breadcrumb — single-item trail auto-hides (canonical top block). */}
      <Breadcrumb items={[{ label: t('nav.about', { defaultValue: 'About' }) }]} />

      {/* Canonical header row — sr-only title for AT; the module name + icon
          live in the top app bar. About keeps its brand identity block below. */}
      <PageHeader srTitle={t('nav.about', { defaultValue: 'About' })} />

      {/* Canonical module intro — pain-named, copy from MODULE_INTRO_COPY. */}
      <DismissibleInfo
        storageKey="about"
        title={t('about.intro_title', {
          defaultValue: 'Know what you are running and why',
        })}
        more={
          t('about.intro_more', { defaultValue: '' })
            ? <IntroRichText text={t('about.intro_more')} />
            : undefined
        }
        links={[
          { label: t('nav.modules', { defaultValue: 'Modules' }), onClick: () => navigate('/modules') },
          { label: t('nav.settings', { defaultValue: 'Settings' }), onClick: () => navigate('/settings') },
        ]}
      >
        {t('about.intro_body', {
          defaultValue:
            'Shows your installed version, recent release notes and the AGPL-3.0 licence terms. Use it to confirm what build you are on before reporting an issue or planning an upgrade.',
        })}
      </DismissibleInfo>

      {/* Header — two columns on wide screens: identity on the left,
          update-availability notification on the right so users can
          spot a new release at a glance. */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 py-6 items-center">

        {/* ── Left column — project identity (left-aligned on wide) ── */}
        <div className="text-center lg:text-left">
          <div className="flex items-center justify-center lg:justify-start gap-2 mb-4">
            <span className="relative flex h-2.5 w-2.5">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75" />
              <span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-emerald-500" />
            </span>
            <span className="text-xs font-semibold uppercase tracking-widest text-emerald-600">Open Source</span>
          </div>
          <h2 className="text-3xl font-bold text-content-primary tracking-tight">{APP_NAME}</h2>
          <p className="mt-2 text-base text-content-secondary">
            {t('about.tagline', { defaultValue: 'Construction cost estimation, project management and resource control' })}
          </p>
          <div className="mt-3 flex items-center justify-center lg:justify-start gap-3 text-sm text-content-tertiary">
            <span className="font-mono">v{APP_VERSION}</span>
            <span>&middot;</span>
            <span>2026</span>
            <span>&middot;</span>
            <Badge variant="blue" size="sm">AGPL-3.0</Badge>
          </div>
        </div>

        {/* ── Right column — update notification + recent releases ──
            UpdateInlineNotice renders one line when an update is on offer
            and not dismissed; the recent-releases
            mini-list lives below it so the right column visually matches
            the left identity block's height on wide screens. */}
        <div className="flex flex-col gap-3">
          <UpdateInlineNotice />
          <CheckForUpdatesButton />

          {/* Recent releases — last 3 published versions with date so
              users can see the cadence at a glance without scrolling
              into the Changelog further down. */}
          <div className="rounded-xl border border-border-light bg-surface-secondary/40 px-4 py-3">
            <div className="mb-2 flex items-center justify-between gap-2">
              <p className="text-2xs font-semibold uppercase tracking-wider text-content-tertiary">
                {t('about.header_recent_label', { defaultValue: 'Recent releases' })}
              </p>
              <a
                href="#changelog"
                className="text-2xs text-oe-blue hover:underline"
                onClick={(e) => {
                  e.preventDefault();
                  setChangelogExpanded(true);
                  const changelog = document.querySelector('[data-changelog-anchor]');
                  if (changelog) changelog.scrollIntoView({ behavior: 'smooth', block: 'start' });
                }}
              >
                {t('about.header_recent_more', { defaultValue: 'View all →' })}
              </a>
            </div>
            <ul className="space-y-1.5">
              {getRecentReleases(3).map(entry => ({ v: entry.version, date: entry.date, note: entry.summary })).map(r => (
                <li key={r.v} className="flex items-start gap-2 text-2xs leading-snug">
                  <span className="shrink-0 inline-flex items-center rounded-md bg-oe-blue/10 text-oe-blue font-mono font-semibold px-1.5 py-0.5">
                    v{r.v}
                  </span>
                  <span className="shrink-0 font-mono text-content-quaternary">{r.date}</span>
                  <span className="min-w-0 flex-1 truncate text-content-secondary" title={r.note}>
                    {r.note}
                  </span>
                </li>
              ))}
            </ul>
          </div>

        </div>
      </div>

      {/* Platform Capabilities card. */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 items-stretch">

      {/* ── Card 1: Platform Capabilities ── */}
      <Card className="animate-card-in h-full flex flex-col" style={{ animationDelay: '50ms' }}>
        <div className="p-6 flex flex-col flex-1">
          <div className="flex items-center gap-2 mb-4">
            <Award size={18} className="text-amber-500" />
            <h2 className="text-lg font-semibold text-content-primary">
              {t('about.platform_title', { defaultValue: 'Platform Capabilities' })}
            </h2>
          </div>
          {/* Stats — one row on wide screens (6 tiles × 1 row) so the card
              stays compact and lines up vertically with the right-hand
              Community card. Smaller tile padding + value font keep the
              shorter tile readable at narrower widths. */}
          <div className="grid grid-cols-3 sm:grid-cols-6 gap-2.5">
            {[
              { value: '120K+', label: t('about.stat_costs', { defaultValue: 'Cost Items' }) },
              { value: String(SUPPORTED_LANGUAGES.length), label: t('about.stat_langs', { defaultValue: 'Languages' }) },
              { value: '47', label: t('about.stat_regions', { defaultValue: 'Countries covered' }) },
              { value: '6', label: t('about.stat_cad_formats', { defaultValue: 'CAD/BIM formats supported' }) },
              { value: '180+', label: t('about.stat_modules', { defaultValue: 'Backend modules' }) },
              { value: '28', label: t('about.stat_sections', { defaultValue: 'Menu sections' }) },
            ].map((s, i) => (
              <div key={i} className="text-center rounded-xl bg-surface-secondary/50 px-2 py-3">
                <div className="text-xl font-bold text-content-primary leading-none">{s.value}</div>
                <div className="text-2xs text-content-tertiary mt-1.5 leading-snug">{s.label}</div>
              </div>
            ))}
          </div>
          <p className="mt-auto pt-4 text-sm text-content-secondary leading-relaxed">
            {t('about.platform_desc', { defaultValue: `${APP_NAME} covers the full construction estimation workflow - BOQ editing, 4D scheduling, 5D cost modeling, AI-powered estimation, CAD/BIM quantity takeoff (RVT, IFC, DWG), tendering, and reporting. Supports regional classification standards and custom schemas.` })}
          </p>
        </div>
      </Card>

      </div>
      {/* /Two-card band */}

      {/* Reference band — Documentation + License side-by-side with matched heights. */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 items-stretch">

        {/* Documentation — same h-full + flex-col + mt-auto pattern as License so
            both cards land their CTA button on the same baseline. The body is
            enriched with a "Popular topics" 2x3 mini-grid to fill the column
            instead of leaving whitespace below the 4 high-level bullets. */}
        <Card className="animate-card-in h-full flex flex-col" style={{ animationDelay: '240ms' }}>
          <div className="p-6 flex flex-col flex-1">
            <div className="flex items-center gap-3 mb-3">
              <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-oe-blue-subtle">
                <BookOpen size={20} className="text-oe-blue" />
              </div>
              <div className="flex-1 min-w-0">
                <h2 className="text-lg font-semibold text-content-primary">
                  {t('about.docs_title', { defaultValue: 'Documentation' })}
                </h2>
                <p className="text-xs text-content-tertiary">
                  {t('about.docs_desc', { defaultValue: 'Installation guides, feature overview, API reference, and tutorials' })}
                </p>
              </div>
            </div>
            <ul className="space-y-1.5 text-xs text-content-secondary mb-4">
              {[
                t('about.docs_b1', { defaultValue: 'Installation & deployment guides (Docker, VPS, Kubernetes)' }),
                t('about.docs_b2', { defaultValue: 'Feature walkthroughs - BOQ editor, BIM viewer, takeoff' }),
                t('about.docs_b3', { defaultValue: 'OpenAPI reference for all 100+ backend modules' }),
                t('about.docs_b4', { defaultValue: 'Module SDK - write your own plugin' }),
              ].map((b, i) => (
                <li key={i} className="flex items-start gap-2">
                  <ArrowRight size={11} className="mt-1 shrink-0 text-oe-blue" />
                  <span>{b}</span>
                </li>
              ))}
            </ul>

            {/* ── Popular topics — deep-links into the repo's docs/ tree on
                GitHub (the marketing-site docs.html 404s). Topics with a
                dedicated doc file link straight to it; the rest fall back to
                the browsable docs/ tree root. ── */}
            <p className="mb-2 text-2xs font-semibold uppercase tracking-wider text-content-tertiary">
              {t('about.docs_popular_label', { defaultValue: 'Popular topics' })}
            </p>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
              {[
                { id: 'quickstart', href: DOCS_URL, label: t('about.docs_pop_quickstart', { defaultValue: 'Quick start - Docker compose' }) },
                { id: 'bim', href: `${SOURCE_REPO_URL}/blob/main/docs/BIM-STORAGE-ARCHITECTURE.md`, label: t('about.docs_pop_bim', { defaultValue: 'Import BIM (RVT/IFC) → BOQ' }) },
                { id: 'gaeb', href: DOCS_URL, label: t('about.docs_pop_gaeb', { defaultValue: 'GAEB X83/X84 import & export' }) },
                { id: 'takeoff', href: DOCS_URL, label: t('about.docs_pop_takeoff', { defaultValue: 'PDF takeoff with annotations' }) },
                { id: 'sdk', href: `${DOCS_URL}/module-development`, label: t('about.docs_pop_sdk', { defaultValue: 'Module SDK · write a plugin' }) },
                { id: 'deploy', href: `${SOURCE_REPO_URL}/blob/main/docs/INSTALL_LINUX.md`, label: t('about.docs_pop_deploy', { defaultValue: 'VPS deployment guide' }) },
              ].map(topic => (
                <a
                  key={topic.id}
                  href={topic.href}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="group flex items-center justify-between gap-2 rounded-lg border border-border-light bg-surface-secondary/40 px-3 py-2 hover:border-oe-blue/40 hover:bg-oe-blue/[0.04] transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-oe-blue focus-visible:ring-offset-1"
                >
                  <span className="text-2xs text-content-secondary group-hover:text-content-primary leading-snug">
                    {topic.label}
                  </span>
                  <ArrowRight size={11} className="shrink-0 text-content-quaternary group-hover:text-oe-blue group-hover:translate-x-0.5 transition-all" />
                </a>
              ))}
            </div>

            <div className="mt-auto pt-4">
              <a href={DOCS_URL} target="_blank" rel="noopener noreferrer">
                <Button variant="primary" size="md" icon={<BookOpen size={15} />}>
                  {t('about.docs_open', { defaultValue: 'Open Docs' })}
                </Button>
              </a>
            </div>
          </div>
        </Card>

        {/* License — restructured into "You can / You must" so users see the
            AGPL trade-off at a glance instead of reading prose. CTA at bottom
            matches the Documentation card's "Open Docs" button position. */}
        <Card className="animate-card-in h-full flex flex-col" style={{ animationDelay: '260ms' }}>
          <div className="p-6 flex flex-col flex-1">
            <div className="flex items-center gap-3 mb-3">
              <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-emerald-50 dark:bg-emerald-900/20">
                <Shield size={20} className="text-emerald-500" />
              </div>
              <div className="flex-1 min-w-0">
                <h2 className="text-lg font-semibold text-content-primary">
                  {t('about.license_title', { defaultValue: 'License & Open Source' })}
                </h2>
                <p className="text-xs text-content-tertiary">
                  {t('about.license_subtitle', { defaultValue: 'AGPL-3.0 - free for any use, including commercial' })}
                </p>
              </div>
            </div>
            <p className="text-sm text-content-secondary leading-relaxed mb-4">
              {t('about.license_desc_short', { defaultValue: 'GNU Affero General Public License v3.0 - copyleft. The full text ships with the source and is also available at gnu.org/licenses/agpl-3.0.' })}
            </p>

            {/* ── You can / You must — concrete bullet pairs so the AGPL
                obligations are visible before download, not after lawyers. ── */}
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-4 gap-y-3 mb-4">
              <div>
                <p className="mb-1.5 text-2xs font-semibold uppercase tracking-wider text-emerald-600 dark:text-emerald-400">
                  {t('about.license_youcan_label', { defaultValue: 'You can' })}
                </p>
                <ul className="space-y-1 text-xs text-content-secondary leading-snug">
                  {[
                    t('about.license_youcan_1', { defaultValue: 'Use for any purpose, including commercial' }),
                    t('about.license_youcan_2', { defaultValue: 'Self-host on your own hardware' }),
                    t('about.license_youcan_3', { defaultValue: 'Modify, fork, and redistribute' }),
                    t('about.license_youcan_4', { defaultValue: 'Build proprietary modules on top' }),
                  ].map((b, i) => (
                    <li key={i} className="flex items-start gap-1.5">
                      <span className="mt-1 inline-block h-1 w-1 rounded-full bg-emerald-500 shrink-0" />
                      <span>{b}</span>
                    </li>
                  ))}
                </ul>
              </div>
              <div>
                <p className="mb-1.5 text-2xs font-semibold uppercase tracking-wider text-amber-600 dark:text-amber-400">
                  {t('about.license_youmust_label', { defaultValue: 'You must' })}
                </p>
                <ul className="space-y-1 text-xs text-content-secondary leading-snug">
                  {[
                    t('about.license_youmust_1', { defaultValue: 'Share modifications under AGPL-3.0' }),
                    t('about.license_youmust_2', { defaultValue: 'Provide source to network users' }),
                    t('about.license_youmust_3', { defaultValue: 'Keep copyright + license notices' }),
                    t('about.license_youmust_4', { defaultValue: 'Document what you changed' }),
                  ].map((b, i) => (
                    <li key={i} className="flex items-start gap-1.5">
                      <span className="mt-1 inline-block h-1 w-1 rounded-full bg-amber-500 shrink-0" />
                      <span>{b}</span>
                    </li>
                  ))}
                </ul>
              </div>
            </div>

            <div className="flex flex-wrap gap-1.5 mb-4">
              <Badge variant="success" size="sm">{t('about.license_badge_free', { defaultValue: 'Free to use' })}</Badge>
              <Badge variant="success" size="sm">{t('about.license_badge_oss', { defaultValue: 'Open source' })}</Badge>
              <Badge variant="success" size="sm">{t('about.license_badge_selfhost', { defaultValue: 'Self-hosted' })}</Badge>
              <Badge variant="success" size="sm">{t('about.license_badge_nolockin', { defaultValue: 'No vendor lock-in' })}</Badge>
              <Badge variant="blue" size="sm">AGPL-3.0</Badge>
            </div>

            {/* Third-party components. AGPL-3.0 above is our own licence and
                not the whole story: the build also redistributes LGPL-2.1 and
                LGPL-3.0 components, and their texts ship inside the artefact
                where nothing could read them until /api/v1/licenses/. */}
            <ThirdPartyLicenses className="mb-4" />

          </div>
        </Card>

      </div>

      {/* Thanks to our community - a warm wall of GitHub avatars for the
          people who report issues, ask questions and fund the work. Placed
          right before the Changelog so the page moves from gratitude into the
          release history. Sponsors and donors lead the wall when present.
          Kept in sync with /docs/legal/ACKNOWLEDGMENTS.md via
          acknowledgments.ts. */}
      <Card className="animate-card-in" style={{ animationDelay: '280ms' }}>
        <div className="p-6" data-community-anchor style={{ scrollMarginTop: '80px' }}>
          <div className="flex items-center gap-2.5 mb-2">
            <div className="flex h-9 w-9 items-center justify-center rounded-full bg-rose-500/10 ring-1 ring-rose-500/20">
              <Heart size={18} className="text-rose-500" fill="currentColor" />
            </div>
            <h2 className="text-lg font-semibold text-content-primary">
              {t('about.thanks_title', { defaultValue: 'Built with our community' })}
            </h2>
          </div>
          <p className="text-sm text-content-secondary leading-relaxed mb-6 max-w-3xl">
            {t('about.thanks_subtitle', {
              defaultValue:
                `${APP_NAME} keeps growing thanks to its community. Every question, bug report and feature idea shapes what we build next, and every star, share and contribution keeps the platform free and open for everyone. Thank you to the people below.`,
            })}
          </p>

          {/* Sponsors and donors lead the wall when we have any. */}
          {SPONSORS.length > 0 ? (
            <div className="mb-6">
              <p className="mb-3 text-2xs font-semibold uppercase tracking-wider text-content-tertiary">
                {t('about.thanks_sponsors_label', { defaultValue: 'Sponsors and donors' })}
              </p>
              {/* Text-only nickname chips - no avatars or remote images, so the
                  wall stays self-contained and reads cleanly offline. When a
                  sponsor supplies a handle or url the chip links to it. */}
              <div className="flex flex-wrap items-center gap-2.5">
                {SPONSORS.map(s => {
                  const url = acknowledgedUrl(s);
                  const chipCls =
                    'inline-flex items-center gap-2 rounded-lg border border-emerald-300/60 bg-emerald-50/60 dark:border-emerald-500/30 dark:bg-emerald-900/15 px-4 py-2 text-sm font-semibold text-content-primary';
                  const label = (
                    <>
                      <HandCoins size={18} className="shrink-0 text-emerald-600 dark:text-emerald-400" />
                      <span>{s.name}</span>
                    </>
                  );
                  return url ? (
                    <a
                      key={s.handle ?? s.name}
                      href={url}
                      target="_blank"
                      rel="noopener noreferrer"
                      title={s.name}
                      className={clsx(chipCls, 'transition-colors hover:border-emerald-400/70 hover:text-content-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-oe-blue focus-visible:ring-offset-1')}
                    >
                      {label}
                    </a>
                  ) : (
                    <span key={s.handle ?? s.name} title={s.name} className={chipCls}>
                      {label}
                    </span>
                  );
                })}
              </div>
            </div>
          ) : null}

          {/* Contributors and reporters - everyone tracked in ACKNOWLEDGMENTS.md. */}
          <p className="mb-3 text-2xs font-semibold uppercase tracking-wider text-content-tertiary">
            {t('about.thanks_contributors_label', { defaultValue: 'Contributors and reporters' })}
          </p>
          <div className="flex flex-wrap gap-3">
            {CONTRIBUTORS.map(c => (
              <AckAvatar key={c.handle ?? c.name} entry={c} />
            ))}
          </div>

          {/* Closing CTA - invites the reader onto the wall. */}
          <p className="mt-6 pt-4 border-t border-border-light text-xs text-content-secondary">
            <a
              href={SOURCE_REPO_URL}
              target="_blank"
              rel="noopener noreferrer"
              className="font-medium text-oe-blue hover:underline"
            >
              {t('about.thanks_cta', {
                defaultValue: 'Want to see your name here? Report issues, improve the code and contribute on GitHub.',
              })}
            </a>
          </p>
        </div>
      </Card>

      {/* Changelog - collapsed to the latest release by default with a toggle
          to reveal the full history. maxEntries caps the releases, so while
          unreleased work is pending the collapsed view shows two cards rather
          than one: that card is rendered above the list and is deliberately
          not a member of it. Anchored so the header's "View all" jump target
          lands on the section heading instead of mid-scroll. */}
      <Card>
        <div className="p-6" data-changelog-anchor>
          <Changelog maxEntries={changelogExpanded ? undefined : 1} />
          <div className="mt-4 flex justify-center">
            <button
              type="button"
              onClick={() => setChangelogExpanded(v => !v)}
              aria-expanded={changelogExpanded}
              className="inline-flex items-center gap-1.5 rounded-lg border border-border-light bg-surface-secondary/40 px-3.5 py-2 text-xs font-semibold text-oe-blue hover:border-oe-blue/40 hover:bg-oe-blue/[0.04] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-oe-blue focus-visible:ring-offset-1"
            >
              {changelogExpanded
                ? t('about.changelog_show_less', { defaultValue: 'Show less' })
                : t('about.changelog_show_all', { defaultValue: 'Show full changelog' })}
              <ChevronDown
                size={14}
                className={clsx('transition-transform', changelogExpanded && 'rotate-180')}
                aria-hidden
              />
            </button>
          </div>
        </div>
      </Card>

      {/* Credits — the AGPL wants the corresponding source reachable from
          the product; this line and the sidebar footer both carry it. */}
      <div className="text-center py-4 text-xs text-content-quaternary">
        <p className="flex items-center justify-center gap-1">
          {APP_NAME}
          {' · '}
          <a
            href={SOURCE_REPO_URL}
            target="_blank"
            rel="noopener noreferrer"
            className="hover:text-oe-blue transition-colors"
          >
            {t('dataSecurity.source', { defaultValue: 'Source code' })}
          </a>
          {' · '}
          AGPL-3.0-or-later
        </p>
      </div>
    </div>
  );
}
