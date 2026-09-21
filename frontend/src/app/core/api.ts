import { HttpClient, HttpErrorResponse, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { firstValueFrom } from 'rxjs';

import type {
  AnswerResult,
  ImportRun,
  Item,
  ItemPage,
  Lessons,
  Queue,
  QuestionType,
  QuizResult,
  Settings,
  SettingsPatch,
  Stats,
  WaniKaniAccount,
} from './api.types';

/**
 * The one place that talks HTTP.
 *
 * Same-origin throughout: in the deployment nginx serves the SPA and proxies
 * `/api` to the backend over the internal network, so the browser always talks
 * to whatever address it loaded the page from — LAN IP, mDNS name, VPN,
 * reverse proxy — and no base URL is ever configured here.
 */
@Injectable({ providedIn: 'root' })
export class Api {
  private readonly http = inject(HttpClient);

  // --- studying ---------------------------------------------------------

  reviewQueue(limit = 50): Promise<Queue> {
    return this.get('/api/reviews', new HttpParams().set('limit', limit));
  }

  /** `confirm` is the second Enter after a "das sieht falsch aus" warning. */
  answer(
    subjectId: number,
    question: QuestionType,
    answer: string,
    confirm = false,
  ): Promise<AnswerResult> {
    return this.post('/api/reviews/answer', {
      subject_id: subjectId,
      question,
      answer,
      confirm,
    });
  }

  /** One batch from a single level; omit `level` for the lowest open one. */
  lessons(level?: number | null, limit?: number): Promise<Lessons> {
    let params = new HttpParams();
    if (level !== undefined && level !== null) {
      params = params.set('level', level);
    }
    if (limit !== undefined) {
      params = params.set('limit', limit);
    }
    return this.get('/api/lessons', params);
  }

  /** Check a lesson-quiz answer. Moves nothing — see the backend endpoint. */
  quiz(subjectId: number, question: QuestionType, answer: string): Promise<QuizResult> {
    return this.post('/api/lessons/quiz', {
      subject_id: subjectId,
      question,
      answer,
    });
  }

  startLessons(subjectIds: number[]): Promise<{ changed: number }> {
    return this.post('/api/lessons/start', { subject_ids: subjectIds });
  }

  // --- items and the overrides -----------------------------------------

  items(filters: {
    state?: string;
    object_type?: string;
    level?: number;
    srs_stage?: number;
    search?: string;
    limit?: number;
    offset?: number;
  }): Promise<ItemPage> {
    let params = new HttpParams();
    for (const [key, value] of Object.entries(filters)) {
      if (value !== undefined && value !== null && value !== '') {
        params = params.set(key, String(value));
      }
    }
    return this.get('/api/items', params);
  }

  item(subjectId: number): Promise<Item> {
    return this.get(`/api/items/${subjectId}`);
  }

  /** "Das kann ich." The reason this app exists. */
  markKnown(subjectIds: number[], knownStage?: number): Promise<{ changed: number }> {
    return this.post('/api/items/known', {
      subject_ids: subjectIds,
      ...(knownStage === undefined ? {} : { known_stage: knownStage }),
    });
  }

  resetItems(subjectIds: number[]): Promise<{ changed: number }> {
    return this.post('/api/items/reset', { subject_ids: subjectIds });
  }

  suspendItems(subjectIds: number[]): Promise<{ changed: number }> {
    return this.post('/api/items/suspend', { subject_ids: subjectIds });
  }

  unsuspendItems(subjectIds: number[]): Promise<{ changed: number }> {
    return this.post('/api/items/unsuspend', { subject_ids: subjectIds });
  }

  // --- settings and import ---------------------------------------------

  settings(): Promise<Settings> {
    return this.get('/api/settings');
  }

  saveSettings(patch: SettingsPatch): Promise<Settings> {
    return firstValueFrom(this.http.put<Settings>('/api/settings', patch)).catch(rethrow);
  }

  wanikaniAccount(): Promise<WaniKaniAccount> {
    return this.get('/api/settings/wanikani/account');
  }

  startImport(knownSrsStage?: number, remapExisting = false): Promise<ImportRun> {
    return this.post('/api/import', {
      ...(knownSrsStage === undefined ? {} : { known_srs_stage: knownSrsStage }),
      remap_existing: remapExisting,
    });
  }

  importRun(id: number): Promise<ImportRun> {
    return this.get(`/api/import/${id}`);
  }

  latestImport(): Promise<ImportRun | null> {
    return this.get('/api/import/latest');
  }

  stats(): Promise<Stats> {
    return this.get('/api/stats');
  }

  // --- plumbing ---------------------------------------------------------

  private get<T>(url: string, params?: HttpParams): Promise<T> {
    return firstValueFrom(this.http.get<T>(url, { params })).catch(rethrow);
  }

  private post<T>(url: string, body: unknown): Promise<T> {
    return firstValueFrom(this.http.post<T>(url, body)).catch(rethrow);
  }
}

/**
 * Turn an HTTP failure into a German sentence.
 *
 * The backend's `detail` is English by convention, so it gets a German lead-in
 * rather than a translation: the detail names the cause precisely and is worth
 * showing verbatim, but on its own it reads like a stack trace.
 */
function rethrow(error: unknown): never {
  if (error instanceof HttpErrorResponse) {
    const detail = typeof error.error?.detail === 'string' ? error.error.detail : '';
    if (error.status === 0) {
      throw new Error('Der Server ist nicht erreichbar.');
    }
    throw new Error(
      detail ? `Der Server hat abgelehnt: ${detail}` : `Der Server antwortete mit ${error.status}.`,
    );
  }
  throw error;
}
