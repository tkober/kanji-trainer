import { Component } from '@angular/core';
import { SumiPage } from 'sumi-ui/layout';

/**
 * "How do kanji readings work" — reached only through the info flyout next to
 * a reading heading (see `shared/readings/readings.ts`), never from the main
 * nav. A static explainer, not a feature: the flyout itself carries the short
 * version, this is the "More about readings" link's destination for whoever
 * wants the long one.
 */
@Component({
  selector: 'app-readings-page',
  imports: [SumiPage],
  templateUrl: './readings.html',
  styleUrl: './readings.scss',
})
export class ReadingsPage {}
