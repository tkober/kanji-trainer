import { describe, expect, it } from 'vitest';
import {
  canGoBack,
  canGoForward,
  canMarkKnown,
  type ReadingHotkeyState,
} from './reading-hotkeys';

function state(overrides: Partial<ReadingHotkeyState> = {}): ReadingHotkeyState {
  return { phase: 'reading', busy: false, index: 1, itemCount: 3, ...overrides };
}

describe('canGoBack', () => {
  it('is true in the middle of a batch', () => {
    expect(canGoBack(state({ index: 1 }))).toBe(true);
  });

  it('is false on the first item', () => {
    expect(canGoBack(state({ index: 0 }))).toBe(false);
  });

  it('is true on the last item', () => {
    expect(canGoBack(state({ index: 2, itemCount: 3 }))).toBe(true);
  });

  it('is false outside the reading phase', () => {
    expect(canGoBack(state({ phase: 'gate' }))).toBe(false);
    expect(canGoBack(state({ phase: 'quiz' }))).toBe(false);
  });

  it('is false while busy', () => {
    expect(canGoBack(state({ busy: true }))).toBe(false);
  });
});

describe('canGoForward', () => {
  it('is true in the middle of a batch', () => {
    expect(canGoForward(state({ index: 1 }))).toBe(true);
  });

  it('is still true on the last item -- the handler opens the quiz gate instead of stepping forward', () => {
    expect(canGoForward(state({ index: 2, itemCount: 3 }))).toBe(true);
  });

  it('is false outside the reading phase', () => {
    expect(canGoForward(state({ phase: 'select' }))).toBe(false);
  });

  it('is false while busy', () => {
    expect(canGoForward(state({ busy: true }))).toBe(false);
  });

  it('is false with an empty batch', () => {
    expect(canGoForward(state({ itemCount: 0 }))).toBe(false);
  });
});

describe('canMarkKnown', () => {
  it('is true with focus on a non-editable element', () => {
    const div = document.createElement('div');
    expect(canMarkKnown(state(), div)).toBe(true);
  });

  it('is true with no focused element at all', () => {
    expect(canMarkKnown(state(), null)).toBe(true);
  });

  it('is false with focus in an input', () => {
    const input = document.createElement('input');
    expect(canMarkKnown(state(), input)).toBe(false);
  });

  it('is false with focus in a textarea', () => {
    const textarea = document.createElement('textarea');
    expect(canMarkKnown(state(), textarea)).toBe(false);
  });

  it('is false outside the reading phase', () => {
    const div = document.createElement('div');
    expect(canMarkKnown(state({ phase: 'quiz' }), div)).toBe(false);
  });

  it('is false while busy', () => {
    const div = document.createElement('div');
    expect(canMarkKnown(state({ busy: true }), div)).toBe(false);
  });
});
