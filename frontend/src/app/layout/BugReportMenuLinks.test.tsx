// DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
// Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
/**
 * Every channel in the bug menu has to leave the app, on both builds.
 *
 * Reported against 18.5.0 as "none of the links work". Two things in this menu
 * made that true without any link being broken on its own:
 *
 *  - the e-mail channel assigned `window.location.href = 'mailto:...'`, which
 *    the desktop webview does not hand to a mail client, so it did nothing;
 *  - while the recent errors were only network blips, the whole channel list
 *    sat under `opacity-40 pointer-events-none`. The desktop app logs a few of
 *    those on every start while its server comes up, so the menu read as a
 *    column of dead links until somebody found "report anyway".
 *
 * `openLink` is the one place that knows how to open a link on each build (see
 * desktop.test.ts), so here it is observed rather than exercised. That rule
 * outlived the channel it was written for: the destination used to be this
 * product's own issue tracker, hard-coded, and it is now whatever the workspace
 * set - a support page or a mailbox - so the same "it must actually leave"
 * question is asked of both kinds below.
 *
 * Why the channel disappears when nothing is configured, instead of falling back
 * to a default: a bug report carries the user's own words plus their machine,
 * their build and the page they were on. Where that goes is the operator's
 * decision, and a product that quietly ships a destination is deciding it for
 * them. A missing row is also more honest than a disabled one - a row the user
 * cannot click invites them to write a report and then throws it away.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';

const openLink = vi.hoisted(() => vi.fn());
const copyToClipboard = vi.hoisted(() => vi.fn());
const networkOnly = vi.hoisted(() => ({ value: false }));

vi.mock('@/shared/lib/desktop', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/shared/lib/desktop')>();
  return { ...actual, openLink };
});

vi.mock('@/shared/lib/browser', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/shared/lib/browser')>();
  return { ...actual, copyToClipboard };
});

vi.mock('@/shared/lib/errorLogger', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/shared/lib/errorLogger')>();
  return { ...actual, isLastErrorNetworkOnly: () => networkOnly.value };
});

import { BugReportMenu } from './Header';
import { useBrandingStore, type SupportDestination } from '@/stores/useBrandingStore';

const REAL_DESCRIPTION =
  'I clicked Install on the UK JCT pack on the modules page and nothing happened.';

const MAILBOX = { kind: 'email', value: 'support@builder.example' } as const;
const SUPPORT_PAGE = { kind: 'url', value: 'https://support.builder.example/new-ticket' } as const;
const REPORT_LABEL = 'Report a bug (with logs)';

function setDestination(destination: SupportDestination | null) {
  useBrandingStore.setState({ supportDestination: destination });
}

function openMenu() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <BugReportMenu />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  fireEvent.click(screen.getByLabelText('Report a bug or send feedback'));
  fireEvent.change(screen.getByLabelText(/What happened/), { target: { value: REAL_DESCRIPTION } });
}

const channel = (label: string) => screen.getByText(label).closest('button') as HTMLButtonElement;

describe('bug menu channels', () => {
  beforeEach(() => {
    openLink.mockClear();
    copyToClipboard.mockReset();
    copyToClipboard.mockResolvedValue(true);
    networkOnly.value = false;
    setDestination(null);
  });

  afterEach(() => {
    setDestination(null);
  });

  it('leaves the report channel out entirely when the workspace chose no destination', () => {
    openMenu();

    expect(screen.queryByText(REPORT_LABEL)).toBeNull();
    expect(openLink).not.toHaveBeenCalled();
    // The menu is not empty and not broken: the log is still there, which is the
    // channel that needs no destination and no network.
    expect(channel('Download log only')).toBeTruthy();
  });

  it('sends a mailbox destination as a mailto carrying the whole report', () => {
    setDestination(MAILBOX);
    openMenu();
    fireEvent.click(channel(REPORT_LABEL));

    expect(openLink).toHaveBeenCalledTimes(1);
    const url = openLink.mock.calls[0]![0] as string;
    // Through openLink, not `location.href`: the desktop webview does not hand a
    // mailto to a mail client unless it goes the same way every other link does.
    expect(url.startsWith(`mailto:${MAILBOX.value}?`)).toBe(true);
    const parsed = new URL(url);
    // The screen name comes from the route, which this helper does not set, so
    // the shape is what is pinned: the line names a screen and stays English.
    expect(parsed.searchParams.get('subject')).toMatch(/^\[.+\] Bug report from in-app menu$/);
    expect(parsed.searchParams.get('body')).toContain(REAL_DESCRIPTION);
    // The payload travelled inside the link, so the clipboard was not needed.
    expect(copyToClipboard).not.toHaveBeenCalled();
  });

  it('opens a page destination as the operator set it, and copies the report so nothing is lost', async () => {
    setDestination(SUPPORT_PAGE);
    openMenu();
    fireEvent.click(channel(REPORT_LABEL));

    await waitFor(() => expect(openLink).toHaveBeenCalledTimes(1));
    // Exactly the configured page: no title or body query string bolted on,
    // because whose form it is and what its fields are called is not ours to
    // guess, and a wrong guess loses the report silently.
    expect(openLink.mock.calls[0]![0]).toBe(SUPPORT_PAGE.value);

    expect(copyToClipboard).toHaveBeenCalledTimes(1);
    const copied = copyToClipboard.mock.calls[0]![0] as string;
    expect(copied).toContain(REAL_DESCRIPTION);
    expect(copied).toContain('### Description');
  });

  it('keeps the report on screen when the clipboard refuses it', async () => {
    copyToClipboard.mockResolvedValue(false);
    setDestination(SUPPORT_PAGE);
    openMenu();
    fireEvent.click(channel(REPORT_LABEL));

    await waitFor(() => expect(openLink).toHaveBeenCalledTimes(1));
    // The page still opened - that is what the user asked for - but the words
    // stay in the field, because a toast is not a copy of a report.
    expect((screen.getByLabelText(/What happened/) as HTMLTextAreaElement).value).toBe(REAL_DESCRIPTION);
  });

  it('names the destination under the channel, so a user can see where a report leaves for', () => {
    setDestination(SUPPORT_PAGE);
    openMenu();

    // The host, not a sentence: it is data, so it needs no translation in the
    // 43 locales that carry one for everything else in this menu.
    expect(screen.getByText('support.builder.example')).toBeTruthy();
  });

  it('offers no upstream contact surface any more', () => {
    setDestination(MAILBOX);
    openMenu();
    // The e-mail channel (info@datadrivenconstruction.io) and the web form
    // (openconstructionerp.com/contact.html) were upstream funnels. A mailbox an
    // operator typed into their own settings is not one: it is their address,
    // and this test is here to keep the difference visible.
    expect(screen.queryByText('Email the team')).toBeNull();
    expect(screen.queryByText('Web feedback form')).toBeNull();
  });

  it('keeps every channel usable while the network banner is showing', async () => {
    networkOnly.value = true;
    setDestination(MAILBOX);
    openMenu();

    // The banner is still there to explain, it just no longer locks the menu.
    expect(screen.getByRole('alert')).toBeTruthy();
    const list = channel('Download log only').parentElement as HTMLElement;
    expect(list.className).not.toMatch(/pointer-events-none|opacity-40/);

    fireEvent.click(channel(REPORT_LABEL));
    expect(openLink).toHaveBeenCalledTimes(1);
  });
});
