import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source = readFileSync(new URL('../static/js/documentLibrary.js', import.meta.url), 'utf8')
  .replace(/^import .*;\r?\n/gm, '')
  .replace(/^(\s*)export /gm, '$1');
const response = (data, ok = true, status = 200) => ({ ok, status, json: async () => data });

function node(tagName = 'div') {
  return {
    tagName: tagName.toUpperCase(), children: [], style: {}, dataset: {}, listeners: {},
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    appendChild(child) { this.children.push(child); child.parentElement = this; return child; },
    append(...children) { children.forEach(child => this.appendChild(child)); },
    replaceChildren(...children) { this.children = []; this.append(...children); },
    addEventListener(type, fn) { this.listeners[type] = fn; },
    setAttribute(name, value) { this[name] = value; },
    remove() {}, querySelector() { return null; }, querySelectorAll() { return []; }, focus() {},
  };
}

function library() {
  const calls = [], errors = [], toasts = [];
  const elements = {
    'doclib-tag-filters': node(),
    'doclib-tags-list': node(),
  };
  const context = vm.createContext({
    console, URL, URLSearchParams, FormData, setTimeout: () => 0, clearTimeout() {},
    document: {
      body: node('body'), head: node('head'),
      getElementById: id => elements[id] || null,
      createElement: tag => node(tag), addEventListener() {}, querySelectorAll: () => [],
    },
    window: { location: { hostname: 'localhost', href: 'http://localhost' }, innerWidth: 1200 },
    navigator: { platform: 'Win32' }, confirm: () => true,
    uiModule: {
      showError: message => errors.push(message), showToast: message => toasts.push(message),
      styledPrompt: async () => context.promptAnswer,
      styledConfirm: async message => { context.confirmMessage = message; return context.confirmAnswer; },
      esc: value => String(value),
    },
    promptAnswer: null, confirmAnswer: true,
    fetch: async (url, options = {}) => {
      calls.push({ url, options });
      if (context.respond) return context.respond(url, options);
      if (url.includes('/tags')) return response({ tags: [] });
      return response({ documents: [], total: 0, languages: {}, session_count: 0 });
    },
  });
  vm.runInContext(source, context);
  vm.runInContext(`
    libraryRenderStats = () => {};
    libraryRenderLangChips = () => {};
    libraryRenderGrid = () => {};
    libraryRenderLoadMore = () => {};
    libraryUpdateBulkCount = () => {};
    libraryExitSelectMode = () => { _librarySelectMode = false; _librarySelectedIds.clear(); };
    globalThis.state = () => ({
      tags: _libraryTags, active: [..._libraryActiveTagIds],
      documents: _libraryDocs, selected: [..._librarySelectedIds],
    });
  `, context);
  return { context, calls, errors, toasts, elements,
    run: script => vm.runInContext(script, context),
    state: () => JSON.parse(JSON.stringify(context.state())),
  };
}

test('multiple active tags are sent as repeated tag_id filters (ALL semantics)', async () => {
  const lib = library();
  lib.run("_libraryActiveTagIds = new Set(['tag-a', 'tag-b'])");
  await lib.context.libraryFetch(false);
  const params = new URL(lib.calls.at(-1).url, 'http://localhost').searchParams;
  assert.deepEqual(params.getAll('tag_id'), ['tag-a', 'tag-b']);
});

test('tag names render as text and filter chips toggle without evaluating markup', async () => {
  const lib = library();
  lib.context.respond = async () => response({ tags: [
    { id: 'unsafe', name: '<img src=x onerror=alert(1)>', document_count: 2 },
  ] });
  await lib.context.libraryFetchTags();
  const chips = lib.elements['doclib-tag-filters'].children;
  assert.equal(chips[1].textContent, '<img src=x onerror=alert(1)> (2)');
  chips[1].listeners.click();
  assert.deepEqual(lib.state().active, ['unsafe']);
});

test('single assignment replaces tags while bulk add preserves each document existing tags', async () => {
  const lib = library();
  lib.run(`
    _libraryTags = [
      { id: 'a', name: 'A', document_count: 1 },
      { id: 'b', name: 'B', document_count: 1 },
      { id: 'c', name: 'C', document_count: 0 }
    ];
    _libraryDocs = [
      { id: 'one', tags: [{ id: 'a', name: 'A' }] },
      { id: 'two', tags: [{ id: 'b', name: 'B' }] }
    ];
    libraryFetchTags = async () => {};
    libraryFetch = async () => {};
    libraryPickTags = async () => ['c'];
  `);
  await lib.context.libraryAssignTags(['one'], false);
  assert.deepEqual(JSON.parse(lib.calls.at(-1).options.body), { tag_ids: ['c'] });
  lib.calls.length = 0;
  await lib.context.libraryAssignTags(['one', 'two'], true);
  assert.deepEqual(lib.calls.map(call => JSON.parse(call.options.body)), [
    { tag_ids: ['a', 'c'] }, { tag_ids: ['b', 'c'] },
  ]);
});

test('deleting a reusable tag says documents are kept and clears its active filter', async () => {
  const lib = library();
  lib.run("_libraryActiveTagIds = new Set(['tag-a']); libraryFetchTags = async () => {}; libraryFetch = async () => {};");
  await lib.context.libraryDeleteTag({ id: 'tag-a', name: 'Work' });
  assert.match(lib.context.confirmMessage, /Documents using it will be kept/);
  assert.equal(lib.calls[0].options.method, 'DELETE');
  assert.deepEqual(lib.state().active, []);
  assert.equal(lib.toasts[0], 'Tag deleted; documents kept');
});

test('text imports inherit the active document tag filters', async () => {
  const lib = library();
  lib.run("_libraryActiveTagIds = new Set(['tag-a', 'tag-b']); libraryFetch = async () => {};");
  lib.context.FileReader = class {
    readAsText() { this.result = 'hello'; this.onload(); }
  };
  await lib.context.libraryImportFiles([{ name: 'note.txt' }]);
  const create = lib.calls.find(call => call.options.method === 'POST');
  assert.deepEqual(JSON.parse(create.options.body).tag_ids, ['tag-a', 'tag-b']);
});

test('new documents pass requested tags through the document create payload', () => {
  const documentSource = readFileSync(new URL('../static/js/document.js', import.meta.url), 'utf8');
  assert.match(documentSource, /tag_ids:\s*Array\.isArray\(options\.tag_ids\)\s*\?\s*options\.tag_ids\s*:\s*\[\]/);
});
