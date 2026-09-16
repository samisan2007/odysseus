import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

// Execute the browser module with only its DOM/import dependencies stubbed.
const source = readFileSync(new URL('../static/js/documentLibrary.js', import.meta.url), 'utf8')
  .replace(/^import .*;\r?\n/gm, '')
  .replace(/^(\s*)export /gm, '$1');
const response = (data, ok = true, status = 200) => ({ ok, status, json: async () => data });

function library() {
  const calls = [], errors = [], toasts = [], prompts = [];
  const select = { children: [], replaceChildren() { this.children = []; }, appendChild(child) { this.children.push(child); } };
  const newFolder = {};
  const elements = { 'doclib-folder': select, 'doclib-new-folder': newFolder };
  const context = vm.createContext({
    console, URL, URLSearchParams, FormData, setTimeout: () => 0, clearTimeout() {},
    document: { addEventListener() {}, getElementById: id => elements[id] || null, createElement: () => ({}) },
    window: { location: { hostname: 'localhost', href: 'http://localhost' } },
    navigator: { platform: 'Win32' },
    uiModule: {
      showError: message => errors.push(message), showToast: message => toasts.push(message),
      styledPrompt: async (...args) => { prompts.push(args); return context.promptAnswer; },
    },
    promptAnswer: null,
    fetch: async (url, options = {}) => {
      calls.push({ url, options });
      if (context.respond) return context.respond(url, options);
      if (url.includes('/folders')) return response({ folders: [{ path: 'Work/Notes' }, { path: 'Work' }], path: 'Work/Notes' });
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
    globalThis.state = () => ({ folder: _libraryFolder, documents: _libraryDocs, selected: [..._librarySelectedIds] });
  `, context);
  return { context, calls, errors, toasts, prompts, select, newFolder,
    run: script => vm.runInContext(script, context),
    state: () => JSON.parse(JSON.stringify(context.state())),
  };
}

test('folder filtering distinguishes all documents, root, and nested folders while preserving other filters', async () => {
  const lib = library();
  for (const [folder, expected] of [[null, null], ['', ''], ['Work/Notes', 'Work/Notes']]) {
    lib.run(`_libraryFolder = ${JSON.stringify(folder)}; _librarySearch = 'report'; _libraryActiveLanguage = 'pdf'; _libraryArchivedView = true;`);
    await lib.context.libraryFetch(false);
    const params = new URL(lib.calls.at(-1).url, 'http://localhost').searchParams;
    assert.equal(params.get('folder'), expected);
    assert.equal(params.get('search'), 'report');
    assert.equal(params.get('language'), 'pdf');
    assert.equal(params.get('archived'), 'true');
  }
  lib.run('_libraryDeletedView = true');
  await lib.context.libraryFetch(false);
  const params = new URL(lib.calls.at(-1).url, 'http://localhost').searchParams;
  assert.equal(params.get('deleted'), 'true');
  assert.equal(params.has('archived'), false);
});

test('a late response from a previous folder cannot replace the current folder documents', async () => {
  const lib = library();
  let resolveFirst;
  lib.context.respond = () => new Promise(resolve => { resolveFirst = resolve; });
  const first = lib.context.libraryFetch(false);
  lib.run("_libraryFolder = 'Work/Notes'");
  lib.context.respond = async () => response({ documents: [{ id: 'new' }], total: 1, languages: {}, session_count: 0 });
  await lib.context.libraryFetch(false);
  resolveFirst(response({ documents: [{ id: 'old' }], total: 1, languages: {}, session_count: 0 }));
  await first;
  assert.deepEqual(lib.state().documents, [{ id: 'new' }]);
});

test('nested folder creation selects its returned path and keeps labels as plain text', async () => {
  const lib = library();
  lib.run("_libraryFolder = 'Work'");
  lib.context.promptAnswer = 'Work/Notes';
  await lib.context.libraryCreateFolder();
  assert.equal(lib.prompts[0][1].defaultValue, 'Work/');
  assert.deepEqual(JSON.parse(lib.calls[0].options.body), { path: 'Work/Notes' });
  assert.equal(lib.state().folder, 'Work/Notes');
  assert.equal(lib.select.value, 'Work/Notes');
  assert.equal(lib.newFolder.textContent, 'New subfolder');
  assert.deepEqual(lib.select.children.map(child => child.textContent), ['All documents', 'Library root', 'Work', 'Work/Notes']);
  const before = lib.calls.length;
  lib.context.promptAnswer = null;
  await lib.context.libraryCreateFolder();
  assert.equal(lib.calls.length, before);
});

test('bulk move sends the root folder and reports partial failures without discarding failed selections', async () => {
  const lib = library();
  lib.run("_librarySelectedIds = new Set(['good', 'bad']); _librarySelectMode = true;");
  lib.context.promptAnswer = '';
  lib.context.respond = async (url, options) => {
    if (options.method === 'PATCH') return response({ detail: 'Access denied' }, !url.endsWith('/bad'), 403);
    return response({ folders: [], documents: [], total: 0, languages: {}, session_count: 0 });
  };
  await lib.context.libraryMoveDocuments(['good', 'bad']);
  const moves = lib.calls.filter(call => call.options.method === 'PATCH');
  assert.equal(moves.length, 2);
  assert.deepEqual(JSON.parse(moves[0].options.body), { folder: '' });
  assert.deepEqual(lib.state().selected, ['bad']);
  assert.match(lib.errors[0], /Moved 1 of 2 documents.*Access denied/);
});

test('PDF and text imports keep the chosen folder when navigation changes during reading', async () => {
  const lib = library();
  lib.run("_libraryFolder = 'Work/Notes'");
  lib.context.FileReader = class {
    readAsText() { lib.run("_libraryFolder = 'Elsewhere'"); this.result = 'hello'; this.onload(); }
  };
  const pdf = new File(['pdf'], 'form.pdf', { type: 'application/pdf' });
  await lib.context.libraryImportFiles([{ name: 'note.txt' }, pdf]);
  const imports = lib.calls.filter(call => call.options.method === 'POST');
  assert.equal(JSON.parse(imports[0].options.body).folder, 'Work/Notes');
  assert.equal(imports[1].options.body.get('folder'), 'Work/Notes');
  assert.equal(lib.errors.length, 0);
});
