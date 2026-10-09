import { ApplicationConfig, provideBrowserGlobalErrorListeners } from '@angular/core';
import { provideHttpClient, withFetch } from '@angular/common/http';
import { provideRouter, withComponentInputBinding } from '@angular/router';
import { provideSumi } from 'sumi-ui/core';

import { routes } from './app.routes';

export const appConfig: ApplicationConfig = {
  providers: [
    provideBrowserGlobalErrorListeners(),
    provideRouter(routes, withComponentInputBinding()),
    provideHttpClient(withFetch()),
    // `accent`, `motif`, `pattern` and `companion` are all still placeholders
    // until the real design is picked in tkober/sumi-ui#25.
    provideSumi({ accent: 'ai', motif: 'fuji', pattern: 'asanoha', companion: 'tsuru' }),
  ],
};
