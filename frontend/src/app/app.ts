import { Component, computed, inject } from '@angular/core';
import { RouterLink, RouterOutlet } from '@angular/router';
import { SUMI_LAYOUT, type SumiAppShellBrand, type SumiNavItem } from 'sumi-ui/layout';

import { Counters } from './core/counters';

@Component({
  imports: [...SUMI_LAYOUT, RouterLink, RouterOutlet],
  selector: 'app-root',
  templateUrl: './app.html',
})
export class App {
  private readonly counters = inject(Counters);

  protected readonly due = this.counters.due;
  protected readonly lessons = this.counters.lessons;
  protected readonly level = this.counters.level;

  protected readonly brand: SumiAppShellBrand = { glyph: '漢', name: 'Kanji Trainer' };

  // `computed()`, not a plain array, so the due/lessons badges update as the
  // counters change -- `sumi-app-shell` reads `nav()` reactively.
  protected readonly nav = computed<SumiNavItem[]>(() => [
    { label: 'Dashboard', link: '', icon: 'home', exact: true },
    { label: 'Reviews', link: 'review', icon: 'review', badge: this.due(), badgeTone: 'accent' },
    { label: 'Lessons', link: 'lessons', icon: 'lessons', badge: this.lessons(), badgeTone: 'neutral' },
    { label: 'Forecast', link: 'forecast', icon: 'forecast' },
    { label: 'Items', link: 'browse', icon: 'list' },
    { label: 'Settings', link: 'settings', icon: 'settings' },
  ]);

  constructor() {
    void this.counters.refresh();
    // The badge can go stale with nobody touching anything: an item comes due
    // on the clock. Everything the learner does is reported to `Counters` as
    // it happens, so this is the correction for drift, not the count itself.
    // A minute is well under the shortest interval (four hours).
    setInterval(() => void this.counters.refresh(), 60_000);
  }
}
