import { HttpClient, HttpErrorResponse, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { firstValueFrom } from 'rxjs';

import type {
  Activity,
  AnswerResult,
  ImportRun,
  Illustration,
  Item,
  Forecast,
  ItemPage,
  LessonItem,
  Lessons,
  LevelStats,
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

  /**
   * `confirm` is the second Enter after a "that looks wrong" warning.
   * `gaveUp` is Alt+H -- counts as wrong and reveals the answer on purpose,
   * whatever `answer` holds (the caller sends `''`, but the backend does not
   * trust that either).
   */
  answer(
    subjectId: number,
    question: QuestionType,
    answer: string,
    confirm = false,
    gaveUp = false,
  ): Promise<AnswerResult> {
    return this.post('/api/reviews/answer', {
      subject_id: subjectId,
      question,
      answer,
      confirm,
      gave_up: gaveUp,
    });
  }

  /** The selection view for one level; omit `level` for the lowest open one. */
  lessons(level?: number | null): Promise<Lessons> {
    let params = new HttpParams();
    if (level !== undefined && level !== null) {
      params = params.set('level', level);
    }
    return this.get('/api/lessons', params);
  }

  /** Detail for the given ids that are still lessons -- others come back omitted. */
  lessonItems(subjectIds: number[]): Promise<LessonItem[]> {
    let params = new HttpParams();
    for (const id of subjectIds) {
      params = params.append('ids', id);
    }
    return this.get('/api/lessons/items', params);
  }

  /** Check a lesson-quiz answer. Moves nothing — see the backend endpoint.
   * `gaveUp` is Alt+H, same meaning as in `answer()`. */
  quiz(
    subjectId: number,
    question: QuestionType,
    answer: string,
    gaveUp = false,
  ): Promise<QuizResult> {
    return this.post('/api/lessons/quiz', {
      subject_id: subjectId,
      question,
      answer,
      gave_up: gaveUp,
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
    /** 'lapses': worst-offenders-first, for the "trouble items" list --
     *  pass a `limit` alongside it, since this does not filter on its own. */
    sort?: 'default' | 'lapses';
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

  /** The levels the collection actually contains, for the browse filter. */
  levels(): Promise<number[]> {
    return this.get('/api/items/levels');
  }

  /** One item's full detail by subject id -- used to open an item straight
   *  from a link (the statistics page's trouble-items table, via
   *  `/browse?item=<id>`) without first loading the whole browse list. */
  item(subjectId: number): Promise<Item> {
    return this.get(`/api/items/${subjectId}`);
  }

  /** Triggers the lazy fetch on the backend if nothing fresh is cached yet.
   * Never fetches from wanikani.com itself -- see `app.illustrations`. */
  illustration(subjectId: number): Promise<Illustration> {
    return this.get(`/api/items/${subjectId}/illustration`);
  }

  /** Add or replace the learner's own synonyms for one item's meaning
   * (issue #33). Returns the normalised, stored list. */
  setSynonyms(subjectId: number, synonyms: string[]): Promise<string[]> {
    return firstValueFrom(
      this.http.put<{ synonyms: string[] }>(`/api/items/${subjectId}/synonyms`, { synonyms }),
    )
      .then((result) => result.synonyms)
      .catch(rethrow);
  }

  /** "I know this." The reason this app exists. */
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

  /** `tz`: an IANA name (e.g. `Europe/Berlin`) to move "today" to local
   *  midnight for `reviews_today`/`accuracy_today`; omit for UTC midnight. */
  stats(tz?: string): Promise<Stats> {
    let params = new HttpParams();
    if (tz) {
      params = params.set('tz', tz);
    }
    return this.get('/api/stats', params);
  }

  /** Per level x object type, how many items sit in each SRS bucket --
   *  the statistics page aggregates its own reference frame from this. */
  levelStats(): Promise<LevelStats> {
    return this.get('/api/stats/levels');
  }

  /** The activity calendar: one entry per local day over the last `days`
   *  days (default 182), plus the current streak. */
  activity(days?: number, tz?: string): Promise<Activity> {
    let params = new HttpParams();
    if (days !== undefined) {
      params = params.set('days', days);
    }
    if (tz) {
      params = params.set('tz', tz);
    }
    return this.get('/api/stats/activity', params);
  }

  forecast(hours: number): Promise<Forecast> {
    return this.get('/api/forecast', new HttpParams().set('hours', hours));
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
 * Turn an HTTP failure into a sentence.
 *
 * The backend's `detail` names the cause precisely and is worth showing
 * verbatim, but on its own it reads like a stack trace, so it gets a lead-in.
 */
function rethrow(error: unknown): never {
  if (error instanceof HttpErrorResponse) {
    const detail = typeof error.error?.detail === 'string' ? error.error.detail : '';
    if (error.status === 0) {
      throw new Error('The server is not reachable.');
    }
    throw new Error(
      detail ? `The server refused: ${detail}` : `The server answered with ${error.status}.`,
    );
  }
  throw error;
}
