import { ApplicationConfig, provideBrowserGlobalErrorListeners } from '@angular/core';
import { provideHttpClient, withFetch } from '@angular/common/http';
import {
  provideRouter,
  withComponentInputBinding,
  withInMemoryScrolling,
} from '@angular/router';
import { provideSumi } from 'sumi-ui/core';

import { routes } from './app.routes';

export const appConfig: ApplicationConfig = {
  providers: [
    provideBrowserGlobalErrorListeners(),
    provideRouter(
      routes,
      withComponentInputBinding(),
      // Lets a link such as the dashboard's empty-state button land on a
      // specific settings card via `fragment="import"`, not just the top of
      // the page. The settings page itself re-scrolls once its data has
      // loaded (see `SettingsPage.scrollToFragment`) because this fires
      // right after navigation, before a lazy-loaded route's first render is
      // guaranteed to be done.
      withInMemoryScrolling({ anchorScrolling: 'enabled', scrollPositionRestoration: 'enabled' }),
    ),
    provideHttpClient(withFetch()),
    // `accent`, `motif`, `pattern` and `companion` are all still placeholders
    // until the real design is picked in tkober/sumi-ui#25.
    provideSumi({ accent: 'ai', motif: 'fuji', pattern: 'asanoha', companion: 'tsuru' }),
  ],
};
