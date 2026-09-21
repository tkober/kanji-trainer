import { Routes } from '@angular/router';

export const routes: Routes = [
  {
    path: '',
    loadComponent: () => import('./pages/dashboard/dashboard').then((m) => m.Dashboard),
    title: 'Dashboard · Kanji Trainer',
  },
  {
    path: 'review',
    loadComponent: () => import('./pages/review/review').then((m) => m.Review),
    title: 'Reviews · Kanji Trainer',
  },
  {
    path: 'lessons',
    loadComponent: () => import('./pages/lessons/lessons').then((m) => m.LessonsPage),
    title: 'Lessons · Kanji Trainer',
  },
  {
    path: 'forecast',
    loadComponent: () => import('./pages/forecast/forecast').then((m) => m.ForecastPage),
    title: 'Forecast · Kanji Trainer',
  },
  {
    path: 'browse',
    loadComponent: () => import('./pages/browse/browse').then((m) => m.Browse),
    title: 'Items · Kanji Trainer',
  },
  {
    path: 'settings',
    loadComponent: () => import('./pages/settings/settings').then((m) => m.SettingsPage),
    title: 'Settings · Kanji Trainer',
  },
  { path: '**', redirectTo: '' },
];
