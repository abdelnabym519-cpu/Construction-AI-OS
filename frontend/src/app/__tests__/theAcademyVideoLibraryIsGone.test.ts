// DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
// Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
//
// The in-app Academy - the /videos tutorial library, its player, its generated
// catalogue and the scripts that fetched the channel - was taken out of the
// product. This is the regression test for the removal itself.
//
// WHY IT READS FILES INSTEAD OF ONLY IMPORTING THEM. A deleted route cannot be
// asserted from the router: there is nothing left to render, and a test that
// renders nothing passes for every path it tries. So the negative half reads
// the source that used to declare the feature, which is the only place a
// comeback would have to show up first.
//
// WHAT MUST NOT COME BACK: a feature folder, an import of one, a <Route>, a
// sidebar row, a hover-preload entry, the catalogue build scripts, or a locale
// string that still advertises video guides in any of the shipped languages.
//
// WHAT MUST SURVIVE: the Learn card's remaining rows (Cases, Packs), and the
// generic viewers that play a file a user uploaded. Those share an HTML
// element with the deleted player and are a different thing entirely - the
// file manager's lightbox and the documents preview are business features.
//
// Run: npx vitest run src/app/__tests__/theAcademyVideoLibraryIsGone.test.ts

import { describe, expect, it } from 'vitest';
import * as fs from 'node:fs';
import * as path from 'node:path';

import { LEARN_GROUP_ID, navGroups } from '../layout/navCatalog';

const FRONTEND = path.resolve(__dirname, '..', '..', '..');
const REPO = path.resolve(FRONTEND, '..');
const SRC = path.join(FRONTEND, 'src');
const LOCALES = path.join(SRC, 'app', 'locales');

/** Every non-test TypeScript source file, walked fresh on each run. */
function sourceFiles(dir: string): string[] {
  const out: string[] = [];
  const walk = (current: string) => {
    for (const entry of fs.readdirSync(current, { withFileTypes: true })) {
      const full = path.join(current, entry.name);
      if (entry.isDirectory()) {
        if (entry.name !== '__snapshots__') walk(full);
      } else if (/\.tsx?$/.test(entry.name) && !/\.test\.tsx?$/.test(entry.name)) {
        out.push(full);
      }
    }
  };
  walk(dir);
  return out;
}

function read(...parts: string[]): string {
  return fs.readFileSync(path.join(...parts), 'utf8');
}

describe('the Academy video library is gone', () => {
  it('leaves no feature folder, no import of one, and no route to it', () => {
    expect(fs.existsSync(path.join(SRC, 'features', 'videos'))).toBe(false);

    // A folder that is gone still breaks the build through one import, so the
    // import is what this really guards: the walk is over the whole of src.
    const population = sourceFiles(SRC);
    expect(population.length).toBeGreaterThan(2000); // a walk that finds nothing passes everything
    const importing = population
      .filter((file) => /features\/videos/.test(read(file)))
      .map((file) => path.relative(FRONTEND, file));
    expect(importing).toEqual([]);

    const app = read(SRC, 'app', 'App.tsx');
    expect(app).not.toMatch(/path="\/videos"/);
    expect(app).not.toMatch(/VideosPage|ModuleVideosSlot/);
  });

  it('keeps the Learn card, with no row that leads nowhere', () => {
    const everyRow = navGroups.flatMap((group) => group.items.map((item) => item.to));
    expect(everyRow.length).toBeGreaterThan(100);
    expect(everyRow).not.toContain('/videos');

    // The card itself stays: taking the videos row out is not a reason to take
    // the two rows that were already there with it.
    const learn = navGroups.find((group) => group.id === LEARN_GROUP_ID);
    expect(learn?.items.map((item) => item.to)).toEqual(['/cases', '/packs']);
  });

  it('no longer preloads the page or ships the catalogue tooling', () => {
    expect(read(SRC, 'shared', 'lib', 'routePreload.ts')).not.toMatch(/'\/videos'/);

    for (const gone of [
      'scripts/build_video_catalog.py',
      'scripts/fetch_video_channel.py',
      'scripts/video_catalog',
      'assets/email/landshut-thumbs',
    ]) {
      expect(fs.existsSync(path.join(REPO, gone)), gone).toBe(false);
    }
  });

  it('drops the tutorial copy from every shipped language', () => {
    const locales = fs.readdirSync(LOCALES).filter((name) => name.endsWith('.ts'));
    expect(locales.length).toBeGreaterThan(40);

    for (const name of locales) {
      const src = read(LOCALES, name);
      expect(src, `${name} still carries a videos.* key`).not.toMatch(/^\s*"videos\./m);
      expect(src, `${name} still carries nav.videos`).not.toMatch(/^\s*"nav\.videos"/m);
    }

    // The Learn card's own chrome said "video guides" in all 43 full locales.
    // English is asserted word by word; the rest are asserted through the key
    // they answer, because the sentence each one keeps is its own language's.
    const en = read(LOCALES, 'en.ts');
    const learnCopy = [...en.matchAll(/"(sidebar\.learn\.[a-z_]+)": "([^"]*)"/g)].map((m) => m[2]);
    expect(learnCopy.length).toBeGreaterThanOrEqual(6);
    for (const value of learnCopy) {
      expect(value.toLowerCase()).not.toMatch(/video|tutorial/);
    }
  });

  it('still plays a video a user uploaded', () => {
    const viewers = [
      'features/file-manager/components/MediaLightbox.tsx',
      'features/file-manager/kindModule.ts',
      'features/documents/DocumentsPage.tsx',
    ];
    for (const viewer of viewers) {
      expect(fs.existsSync(path.join(SRC, viewer)), viewer).toBe(true);
    }

    const kinds = read(SRC, 'features', 'file-manager', 'kindModule.ts');
    expect(kinds).toMatch(/VIDEO_EXTS/);
    expect(kinds).toMatch(/DOC_VIDEO_VIEW/);

    const documents = read(SRC, 'features', 'documents', 'DocumentsPage.tsx');
    expect(documents).toMatch(/mime\.startsWith\('video\/'\)/);
  });
});
